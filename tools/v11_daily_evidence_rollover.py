#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
from pathlib import Path

from polymarket_scanner.v11.certification import CapabilityScope, StationMetadata, StationRegistry
from polymarket_scanner.v11.evidence import EvidenceStore, canonical, digest
from polymarket_scanner.v11.rules import RuleFingerprint

ROOT = Path('/home/alphaadmin/AlphaV11_ForwardShadow/continuous-shadow-v2')
MANIFEST = Path('/etc/alpha-v11/approvals/station-capabilities.json')
NAMESPACE = 'CHALLENGER:katl-shadow'
SCOPE_KEY = 'f110f6d088bd72d5b72234787f613ee7f9140bb3d48c6319d493b575b8c4351f'
ROTATE_AT_BYTES = 220 * 1024 * 1024
BASE_IDS = (
    'decision-shadow:station:KATL:raw',
    'decision-shadow:station:KATL:metadata',
    'decision-shadow:technical-readiness:0dd809ea4b42',
)

def _atomic_json(path: Path, value: dict, mode: int = 0o600) -> None:
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + '\n')
    os.chmod(tmp, mode)
    os.replace(tmp, path)

def _sha_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()

def db_path(day: str) -> Path:
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', day):
        raise RuntimeError('ROTATION_DAY_INVALID')
    return ROOT / f'daily-{day}.sqlite'

def marker_path(day: str) -> Path:
    return ROOT / f'daily-{day}.rotation-request.json'

def archive_bytes(path: Path) -> int:
    return sum(
        p.stat().st_size
        for p in (path, Path(str(path) + '-wal'))
        if p.exists()
    )

def rotation_requested(day: str) -> bool:
    return marker_path(day).exists()

def request_rotation(day: str, event_id: str, *, reason: str, force: bool = False) -> dict | None:
    path = db_path(day)
    if not path.exists():
        return None
    size = archive_bytes(path)
    if not force and size < ROTATE_AT_BYTES:
        return None
    marker = marker_path(day)
    value = {
        'version': 'alpha_v11_daily_evidence_rotation_request_v1',
        'day': day,
        'event_id': str(event_id),
        'database': str(path),
        'archive_bytes': size,
        'rotate_at_bytes': ROTATE_AT_BYTES,
        'reason': str(reason),
        'requested_at': time.time(),
        'financial_authority': False,
        'real_orders_sent': False,
    }
    _atomic_json(marker, value)
    return value

def _review_for(rule_sha: str, metadata_sha: str) -> dict:
    manifest = json.loads(MANIFEST.read_text())
    hits = [
        r for r in manifest.get('reviews', [])
        if r.get('namespace') == NAMESPACE
        and r.get('stage') == 'SHADOW'
        and r.get('scope_key') == SCOPE_KEY
        and r.get('metadata_fingerprint') == metadata_sha
        and r.get('rule_fingerprint') == rule_sha
        and float(r.get('expires_at', 0)) > time.time()
    ]
    if len(hits) != 1:
        raise RuntimeError('ROTATION_EXACT_PROTECTED_REVIEW_REQUIRED')
    return hits[0]

def _row_by_id(db: sqlite3.Connection, record_id: str):
    row = db.execute('select * from v11_records where record_id=?', (record_id,)).fetchone()
    if row is None:
        raise RuntimeError('ROTATION_DEPENDENCY_MISSING:' + record_id)
    return row

def _closure(db: sqlite3.Connection, seeds: set[str]) -> dict[str, sqlite3.Row]:
    selected: dict[str, sqlite3.Row] = {}
    pending = list(seeds)
    while pending:
        record_id = pending.pop()
        if record_id in selected:
            continue
        row = _row_by_id(db, record_id)
        body = json.loads(row['body'])
        if digest(body) != row['body_sha256']:
            raise RuntimeError('ROTATION_SOURCE_BODY_HASH_MISMATCH:' + record_id)
        selected[record_id] = row
        for ref in body.get('evidence', ()) or ():
            if isinstance(ref, dict) and isinstance(ref.get('id'), str):
                pending.append(ref['id'])
        payload = body.get('payload', {})
        if isinstance(payload, dict) and isinstance(payload.get('source_capture_id'), str):
            pending.append(payload['source_capture_id'])
    return selected

def _compact_snapshot(source: Path, target: Path, event_id: str) -> tuple[dict, dict]:
    if target.exists():
        target.unlink()
    for suffix in ('-wal', '-shm'):
        side = Path(str(target) + suffix)
        if side.exists():
            side.unlink()
    with sqlite3.connect(f'file:{source}?mode=ro', uri=True) as src:
        src.row_factory = sqlite3.Row
        integrity = src.execute('pragma integrity_check').fetchone()[0]
        if integrity != 'ok':
            raise RuntimeError('ROTATION_SOURCE_INTEGRITY_FAILED:' + str(integrity))
        initial = src.execute(
            "select * from v11_records where kind='RULE_STATE' and event_id=? order by seq limit 1",
            (event_id,),
        ).fetchone()
        latest = src.execute(
            "select * from v11_records where kind='RULE_STATE' and event_id=? order by seq desc limit 1",
            (event_id,),
        ).fetchone()
        if initial is None or latest is None:
            raise RuntimeError('ROTATION_RULE_STATE_REQUIRED')
        initial_details = json.loads(initial['body'])['details']
        latest_details = json.loads(latest['body'])['details']
        rule_sha = initial_details.get('fingerprint')
        if latest_details.get('fingerprint') != rule_sha or latest_details.get('quarantined') is True or latest_details.get('changed') is True:
            raise RuntimeError('ROTATION_LATEST_RULE_NOT_STABLE')
        mdrow = _row_by_id(src, 'decision-shadow:station:KATL:metadata')
        md = json.loads(mdrow['body'])['details']['metadata']
        metadata_sha = StationMetadata(
            **dict(
                md,
                observation_providers=tuple(md['observation_providers']),
                forecast_providers=tuple(md['forecast_providers']),
            )
        ).fingerprint
        review = _review_for(rule_sha, metadata_sha)
        frontier = int(review['reviewed_through_seq'])
        if frontier <= 0:
            raise RuntimeError('ROTATION_REVIEW_FRONTIER_INVALID')
        prefix = src.execute(
            'select * from v11_records where seq<=? order by seq', (frontier,)
        ).fetchall()
        if (len(prefix) != frontier or not prefix
                or int(prefix[0]['seq']) != 1 or int(prefix[-1]['seq']) != frontier):
            raise RuntimeError('ROTATION_PROTECTED_PREFIX_NOT_CONTIGUOUS')
        selected = {row['record_id']: row for row in prefix}
        anchors = set(BASE_IDS)
        anchors.add(initial['record_id'])
        anchors.update(str(v['id']) for v in review['capability_proofs'].values())
        if not anchors <= selected.keys():
            raise RuntimeError('ROTATION_PROTECTED_ANCHOR_MISSING')
        source_count, source_min, source_max = src.execute(
            'select count(*),min(seq),max(seq) from v11_records'
        ).fetchone()

    EvidenceStore(target, NAMESPACE)
    with sqlite3.connect(target) as dst:
        for row in sorted(selected.values(), key=lambda r: r['seq']):
            dst.execute(
                'insert into v11_records(seq,record_id,kind,event_id,recorded_at,available_at,body,body_sha256) '
                'values(?,?,?,?,?,?,?,?)',
                (
                    row['seq'], row['record_id'], row['kind'], row['event_id'],
                    row['recorded_at'], row['available_at'], row['body'], row['body_sha256'],
                ),
            )
        dst.commit()
        if dst.execute('pragma integrity_check').fetchone()[0] != 'ok':
            raise RuntimeError('ROTATION_COMPACT_INTEGRITY_FAILED')
        compact_count, compact_min, compact_max = dst.execute(
            'select count(*),min(seq),max(seq) from v11_records'
        ).fetchone()
        if compact_min != 1 or compact_count != frontier or compact_max != frontier:
            raise RuntimeError('ROTATION_COMPACT_PREFIX_NOT_CONTIGUOUS')
        checkpoint = dst.execute('pragma wal_checkpoint(truncate)').fetchone()
        if checkpoint and int(checkpoint[0]) != 0:
            raise RuntimeError('ROTATION_COMPACT_WAL_CHECKPOINT_BUSY')

    compact = EvidenceStore(target, NAMESPACE)
    md = compact.get('decision-shadow:station:KATL:metadata')['body']['details']['metadata']
    metadata = StationMetadata(
        **dict(
            md,
            observation_providers=tuple(md['observation_providers']),
            forecast_providers=tuple(md['forecast_providers']),
        )
    )
    rd = compact.get(initial['record_id'])['body']['details']
    rule = RuleFingerprint(canonical(rd['preimage']), rd['fingerprint'], rd['source_event_sha256'])
    scope = CapabilityScope(
        'KATL', 'HIGH', 'NWS_WRH_TIMESERIES', 'v11-exact-day-historical-v2',
        'D0', 'FUTURE_FORECAST', 'FALL', 'ALL_DAY',
    )
    assessment = StationRegistry(compact).assess(
        scope,
        stage='SHADOW',
        metadata_fingerprint=metadata.fingerprint,
        rule_fingerprint=rule.sha256,
    )
    if not assessment.get('eligible') or assessment.get('reason') != 'REVIEWED_CAPABILITIES_MATCH':
        raise RuntimeError('ROTATION_COMPACT_CERTIFICATION_FAILED:' + str(assessment.get('reason')))
    latest_compact = compact.latest(kind='RULE_STATE', event_id=event_id)
    if latest_compact is None:
        raise RuntimeError('ROTATION_COMPACT_RULE_STATE_MISSING')
    lcd = latest_compact['body']['details']
    if (lcd.get('fingerprint') != rule_sha or lcd.get('quarantined') is True
            or lcd.get('changed') is True):
        raise RuntimeError('ROTATION_COMPACT_RULE_STATE_INVALID')
    info = {
        'review_id': review.get('review_id'),
        'reviewed_through_seq': frontier,
        'rule_fingerprint': rule_sha,
        'metadata_fingerprint': metadata.fingerprint,
        'source_count': source_count,
        'source_min_seq': source_min,
        'source_max_seq': source_max,
        'compact_count': compact_count,
        'compact_max_seq': compact_max,
        'selected_ids': sorted(anchors),
        'latest_rule_id': latest_compact['id'],
    }
    return info, assessment

def _remove_sidecars(path: Path) -> None:
    for suffix in ('-wal', '-shm'):
        side = Path(str(path) + suffix)
        if side.exists():
            side.unlink()

def _next_segment(day: str) -> tuple[int, Path, Path]:
    used = []
    pattern = re.compile(rf'^daily-{re.escape(day)}\.segment-(\d{{3}})-through-seq-(\d+)\.sqlite$')
    for path in ROOT.glob(f'daily-{day}.segment-*-through-seq-*.sqlite'):
        m = pattern.match(path.name)
        if m:
            used.append(int(m.group(1)))
    index = max(used, default=0) + 1
    return index, ROOT / f'daily-{day}.segment-{index:03d}-through-seq-PENDING.sqlite', ROOT / f'daily-{day}.segment-{index:03d}.manifest.json'

def perform_pending_rotation(day: str) -> dict | None:
    marker = marker_path(day)
    if not marker.exists():
        return None
    request = json.loads(marker.read_text())
    if request.get('day') != day or request.get('database') != str(db_path(day)):
        raise RuntimeError('ROTATION_REQUEST_IDENTITY_MISMATCH')
    event_id = str(request.get('event_id') or '')
    if not event_id.isdigit():
        raise RuntimeError('ROTATION_EVENT_ID_INVALID')
    source = db_path(day)
    if not source.exists():
        raise RuntimeError('ROTATION_SOURCE_MISSING')

    # No writer exists at this point: continuous_day_manager calls this before
    # opening EvidenceStore. Fold any committed WAL into the source and validate.
    with sqlite3.connect(source) as db:
        row = db.execute('pragma wal_checkpoint(truncate)').fetchone()
        if row and int(row[0]) != 0:
            raise RuntimeError('ROTATION_WAL_CHECKPOINT_BUSY')
        if db.execute('pragma integrity_check').fetchone()[0] != 'ok':
            raise RuntimeError('ROTATION_SOURCE_INTEGRITY_FAILED')
    _remove_sidecars(source)

    temp = ROOT / f'.daily-{day}.rotation-next.sqlite'
    info, assessment = _compact_snapshot(source, temp, event_id)
    # Validation opens the compact DB in WAL mode. Fold any zero-authority
    # validation side effects back into the main file and discard stale
    # sidecars before the pathname swap.
    with sqlite3.connect(temp) as db:
        row = db.execute('pragma wal_checkpoint(truncate)').fetchone()
        if row and int(row[0]) != 0:
            raise RuntimeError('ROTATION_COMPACT_WAL_CHECKPOINT_BUSY')
        if db.execute('pragma integrity_check').fetchone()[0] != 'ok':
            raise RuntimeError('ROTATION_COMPACT_INTEGRITY_FAILED')
    _remove_sidecars(temp)
    compact_sha = _sha_file(temp)
    source_sha = _sha_file(source)
    index, pending_segment, manifest_path = _next_segment(day)
    segment = ROOT / f'daily-{day}.segment-{index:03d}-through-seq-{info["source_max_seq"]}.sqlite'
    if segment.exists() or manifest_path.exists():
        raise RuntimeError('ROTATION_SEGMENT_COLLISION')

    renamed_source = False
    try:
        os.replace(source, segment)
        renamed_source = True
        os.replace(temp, source)
    except Exception:
        if renamed_source and not source.exists() and segment.exists():
            os.replace(segment, source)
        raise
    os.chmod(segment, 0o444)
    os.chmod(source, 0o600)

    # Verify both post-swap objects before declaring success.
    if _sha_file(segment) != source_sha or _sha_file(source) != compact_sha:
        raise RuntimeError('ROTATION_POST_SWAP_HASH_MISMATCH')
    with sqlite3.connect(f'file:{source}?mode=ro', uri=True) as db:
        if db.execute('pragma integrity_check').fetchone()[0] != 'ok':
            raise RuntimeError('ROTATION_POST_SWAP_COMPACT_INTEGRITY_FAILED')

    result = {
        'version': 'alpha_v11_daily_evidence_segment_manifest_v1',
        'day': day,
        'event_id': event_id,
        'segment_index': index,
        'sealed_segment': str(segment),
        'sealed_segment_sha256': source_sha,
        'sealed_segment_records': info['source_count'],
        'sealed_segment_min_seq': info['source_min_seq'],
        'sealed_segment_max_seq': info['source_max_seq'],
        'compact_database': str(source),
        'compact_database_sha256': compact_sha,
        'compact_records': info['compact_count'],
        'compact_max_seq': info['compact_max_seq'],
        'compact_selected_ids': info['selected_ids'],
        'latest_rule_id': info['latest_rule_id'],
        'rule_fingerprint': info['rule_fingerprint'],
        'metadata_fingerprint': info['metadata_fingerprint'],
        'review_id': info['review_id'],
        'certification_reason': assessment['reason'],
        'history_preserved': True,
        'accepted_for_unified_causal_replay': False,
        'financial_authority': False,
        'real_orders_sent': False,
        'rotated_at': time.time(),
        'rotation_request': request,
    }
    _atomic_json(manifest_path, result, mode=0o444)
    marker.unlink()
    return result

if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('action', choices=('status', 'request', 'rotate'))
    p.add_argument('--day', required=True)
    p.add_argument('--event-id')
    p.add_argument('--force', action='store_true')
    args = p.parse_args()
    if args.action == 'status':
        path = db_path(args.day)
        print(json.dumps({
            'day': args.day,
            'database': str(path),
            'archive_bytes': archive_bytes(path) if path.exists() else None,
            'rotate_at_bytes': ROTATE_AT_BYTES,
            'rotation_requested': rotation_requested(args.day),
            'financial_authority': False,
        }, sort_keys=True))
    elif args.action == 'request':
        if not args.event_id:
            raise SystemExit('--event-id required')
        print(json.dumps(request_rotation(args.day, args.event_id, reason='OPERATOR_OR_THRESHOLD_REQUEST', force=args.force), sort_keys=True))
    else:
        print(json.dumps(perform_pending_rotation(args.day), sort_keys=True))
