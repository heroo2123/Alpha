"""Offline, bounded corroboration of captured Gamma labels with NOAA AWC proxy data."""
from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from collections import Counter
from datetime import date, datetime, time as day_time, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from polymarket_scanner.v11.evidence import EvidenceError, EvidenceStore, digest, finite, identity, sha
from polymarket_scanner.v11.label_attestation import attest_resolved_day
from polymarket_scanner.v11.weather_sources import parse_awc_metar

BASE = Path('/home/alphaadmin/AlphaV11_BrainForward')
START_DAY = '2026-10-05'
NAMESPACE = 'CHALLENGER:katl-shadow'
MAX_DAYS = 64
MAX_DIRECTORY_ENTRIES = 4096
MAX_ROWS = 10000
MAX_BYTES = 32 * 1024 * 1024
MAX_OBSERVATIONS = 100000
MAX_SECONDS = 5.0
PAGE_SIZE = 32


def captured_days(base):
    days = []
    with os.scandir(base) as entries:
        for count, entry in enumerate(entries, 1):
            if count > MAX_DIRECTORY_ENTRIES:
                raise EvidenceError('ATTESTATION_DIRECTORY_BOUND')
            if (entry.is_dir(follow_symlinks=False)
                    and re.fullmatch(r'\d{4}-\d{2}-\d{2}', entry.name)
                    and entry.name >= START_DAY
                    and (Path(entry.path) / 'capture-status.json').is_file()):
                days.append(entry.name)
                if len(days) > MAX_DAYS:
                    raise EvidenceError('ATTESTATION_DAY_BOUND')
    return tuple(sorted(days))


def resolved_days(base):
    return tuple(d for d in captured_days(base) if (base / d / 'labels-status.json').exists())


class _ReadView:
    """One read-only SQLite transaction for all roots and dependency records."""

    def __init__(self, path):
        self.path = path
        self.started = time.monotonic()
        self.rows = 0
        self.bytes = 0
        self.db = None

    def __enter__(self):
        if not self.path.is_file():
            raise EvidenceError('ATTESTATION_ARCHIVE_MISSING')
        self.db = sqlite3.connect(self.path.as_uri() + '?mode=ro', uri=True, timeout=1)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA query_only=ON')
        self.db.set_progress_handler(lambda: self._expired(), 1000)
        try:
            self.db.execute('BEGIN')
            meta = dict(self._query('SELECT key,value FROM v11_meta'))
            if meta.get('namespace') != NAMESPACE or meta.get('version') != 'alpha_v11_evidence_v1':
                raise EvidenceError('NAMESPACE_OR_VERSION_MISMATCH')
            self.tip = self._query('SELECT COALESCE(MAX(seq),0) FROM v11_records').fetchone()[0]
            return self
        except Exception:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *_):
        if self.db is not None:
            self.db.set_progress_handler(None, 0)
            self.db.rollback()
            self.db.close()

    def _expired(self):
        return time.monotonic() - self.started >= MAX_SECONDS

    def _query(self, sql, params=()):
        if self._expired():
            raise EvidenceError('ATTESTATION_ARCHIVE_INCOMPLETE_BOUND')
        try:
            return self.db.execute(sql, params)
        except sqlite3.OperationalError as exc:
            if self._expired() or 'interrupted' in str(exc).lower():
                raise EvidenceError('ATTESTATION_ARCHIVE_INCOMPLETE_BOUND') from None
            raise

    def _decode(self, row):
        if self._expired() or self.rows >= MAX_ROWS or self.bytes + len(row['body'].encode()) > MAX_BYTES:
            raise EvidenceError('ATTESTATION_ARCHIVE_INCOMPLETE_BOUND')
        self.rows += 1
        self.bytes += len(row['body'].encode())
        record = EvidenceStore._decode(row)
        if record['body'].get('namespace') != NAMESPACE:
            raise EvidenceError('NAMESPACE_OR_VERSION_MISMATCH')
        return record

    def get(self, record_id):
        identity(record_id)
        row = self._query('SELECT * FROM v11_records WHERE record_id=? AND seq<=?',
                          (record_id, self.tip)).fetchone()
        if row is None:
            raise EvidenceError('ATTESTATION_REFERENCE_MISSING')
        return self._decode(row)

    def latest(self, kind, event_id):
        row = self._query('SELECT * FROM v11_records WHERE kind=? AND event_id=? AND seq<=? '
                          'ORDER BY seq DESC LIMIT 1', (kind, event_id, self.tip)).fetchone()
        return self._decode(row) if row is not None else None

    def station_rows(self, station):
        after = 0
        while True:
            if self._expired():
                raise EvidenceError('ATTESTATION_ARCHIVE_INCOMPLETE_BOUND')
            page = self._query('SELECT * FROM v11_records WHERE kind=? AND event_id=? '
                               'AND seq>? AND seq<=? ORDER BY seq LIMIT ?',
                               ('OFFICIAL_OBSERVATION', 'station:' + station, after, self.tip, PAGE_SIZE)).fetchall()
            if not page:
                return
            for row in page:
                yield self._decode(row)
            after = page[-1]['seq']


def _public_record(record, *, kind, provider, event_id, now):
    body = record['body']
    if (record['kind'] != kind or record['event_id'] != event_id
            or body.get('provider') != provider or body.get('evidence_class') != 'PUBLIC_OBSERVED'
            or finite(body.get('received_at')) > finite(body.get('available_at'))
            or body['available_at'] > now or body['recorded_at'] > now):
        raise EvidenceError('ATTESTATION_REFERENCE_PROVENANCE_INVALID')


def _join_capture(view, capture, rule_state, rule, labels, *, day, now):
    details = capture['body'].get('details', {})
    fp = digest(rule)
    if (capture['kind'] != 'MEASUREMENT' or not details.get('complete_event_vector')
            or not details.get('parent_feature_contract_verified')
            or capture['event_id'] != rule.get('event_id') or rule.get('target_date') != day
            or rule_state['event_id'] != capture['event_id']
            or rule_state['body'].get('details', {}).get('fingerprint') != fp
            or details.get('binding', {}).get('rule_fingerprint') != fp
            or details.get('rule', {}).get('sha256') != fp
            or details.get('context', {}).get('station_id') != rule['station']
            or details.get('context', {}).get('event_id') != capture['event_id']
            or capture['body']['available_at'] > now
            or rule_state['body']['available_at'] > now):
        raise EvidenceError('ATTESTATION_CAPTURE_RULE_MISMATCH')
    rows = details.get('rows')
    if not isinstance(rows, list) or not rows:
        raise EvidenceError('ATTESTATION_CAPTURE_VECTOR_INVALID')
    targets = {}
    for row in rows:
        target = row.get('target_identity') if isinstance(row, dict) else None
        if not isinstance(target, dict) or not isinstance(target.get('market_id'), str):
            raise EvidenceError('ATTESTATION_CAPTURE_VECTOR_INVALID')
        mid = target['market_id']
        if mid in targets:
            raise EvidenceError('ATTESTATION_CAPTURE_VECTOR_INVALID')
        decision = view.get(row.get('decision_id'))
        if (decision['sha256'] != sha(row.get('decision_sha256'))
                or decision['kind'] != 'DECISION' or decision['event_id'] != capture['event_id']
                or decision['seq'] >= capture['seq']
                or decision['body']['available_at'] > capture['body']['available_at']
                or decision['body'].get('target') != 'FINAL_CONTRACT_PAYOUT'
                or decision['body'].get('binding', {}).get('rule_fingerprint') != fp
                or decision['body'].get('explanation', {}).get('target_identity') != target):
            raise EvidenceError('ATTESTATION_CAPTURE_CHILD_MISMATCH')
        targets[mid] = target
    if set(targets) != {b['market_id'] for b in rule['partition']} or set(targets) != set(labels):
        raise EvidenceError('ATTESTATION_CAPTURE_VECTOR_INVALID')
    for bucket in rule['partition']:
        target = targets[bucket['market_id']]
        if (target.get('condition_id') != bucket.get('condition_id')
                or target.get('token_id') != bucket.get('yes_token')
                or target.get('side') != 'YES'):
            raise EvidenceError('ATTESTATION_CAPTURE_RULE_MISMATCH')
    canonical_rule = details.get('rule', {}).get('canonical_json')
    try:
        if json.loads(canonical_rule) != rule:
            raise EvidenceError('ATTESTATION_CAPTURE_RULE_MISMATCH')
    except (TypeError, ValueError):
        raise EvidenceError('ATTESTATION_CAPTURE_RULE_MISMATCH') from None
    context = dict(station=rule['station'], city=details['context']['city_id'], local_date=day,
                   target='FINAL_CONTRACT_PAYOUT', rule_fingerprint=fp)
    for mid, label in labels.items():
        body = label['body']
        payload = body['payload']
        if (label['event_id'] != capture['event_id'] or body.get('evidence_class') != 'PUBLIC_OBSERVED'
                or body.get('source_identity') != mid or body.get('provider') != 'GAMMA_CLOSED_MARKET_EXACT_TOKEN_PAYOUT'
                or payload.get('target_identity') != targets[mid] or payload.get('context') != context
                or payload.get('decision_target') != 'FINAL_CONTRACT_PAYOUT'
                or payload.get('evidence_type') != 'EXACT_SOURCE_LABEL'
                or body.get('revision') != payload.get('label_version')
                or finite(payload.get('knowable_at')) > body['available_at']
                or body['available_at'] > now or body['recorded_at'] < capture['body']['recorded_at']):
            raise EvidenceError('ATTESTATION_LABEL_IDENTITY_MISMATCH')
    return fp


def _gamma_market(payload, market_id, event_id):
    """Find one exact Gamma market in a retained event or market response."""
    hits = []
    event_found = False

    def visit(value, depth=0, force_event=False, bound_event=False):
        nonlocal event_found
        if depth > 4:
            raise EvidenceError('ATTESTATION_LABEL_SOURCE_INVALID')
        if isinstance(value, list):
            for item in value:
                visit(item, depth + 1, force_event, bound_event)
        elif isinstance(value, dict):
            wrappers = ('event', 'market', 'response')
            if force_event or 'markets' in value:
                # A retained event cannot also be a wrapper. Otherwise a
                # nested event can contradict its ID while its markets win.
                if any(key in value for key in wrappers):
                    raise EvidenceError('ATTESTATION_LABEL_SOURCE_INVALID')
                event_found = True
                if (str(value.get('id')) != event_id
                        or not isinstance(value.get('markets'), list)):
                    raise EvidenceError('ATTESTATION_LABEL_SOURCE_INVALID')
                visit(value['markets'], depth + 1, bound_event=True)
            elif any(key in value for key in wrappers):
                if 'id' in value or bound_event:
                    raise EvidenceError('ATTESTATION_LABEL_SOURCE_INVALID')
                for key in wrappers:
                    if key in value:
                        visit(value[key], depth + 1, key == 'event', bound_event)
            else:
                if 'events' in value:
                    events = value['events']
                    if (not isinstance(events, list) or not events
                            or any(not isinstance(event, dict)
                                   or str(event.get('id')) != event_id for event in events)):
                        raise EvidenceError('ATTESTATION_LABEL_SOURCE_INVALID')
                if str(value.get('id')) == market_id:
                    hits.append((value, bound_event))

    for key in ('event', 'market', 'response'):
        if key in payload:
            visit(payload[key], force_event=key == 'event')
    if len(hits) != 1 or (event_found and not hits[0][1]):
        raise EvidenceError('ATTESTATION_LABEL_SOURCE_INVALID')
    return hits[0][0], event_found


def _gamma_field(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            raise EvidenceError('ATTESTATION_LABEL_SOURCE_INVALID') from None
    if not isinstance(value, list):
        raise EvidenceError('ATTESTATION_LABEL_SOURCE_INVALID')
    return value


def _check_label_sources(view, labels, *, event_id, now, rule):
    source_shas = set()
    buckets = {b['market_id']: b for b in rule['partition']}
    for mid, label in labels.items():
        if view._expired():
            raise EvidenceError('ATTESTATION_ARCHIVE_INCOMPLETE_BOUND')
        payload = label['body']['payload']
        source = view.get(payload['source_capture_id'])
        if source['sha256'] != sha(payload.get('source_capture_sha256')):
            raise EvidenceError('ATTESTATION_REFERENCE_HASH_MISMATCH')
        body = source['body']
        if body.get('provider') not in {'GAMMA_EVENT', 'GAMMA_MARKET', 'GAMMA_CLOSED_MARKET',
                                        'GAMMA_CLOSED_MARKET_EXACT_TOKEN_PAYOUT'}:
            raise EvidenceError('ATTESTATION_LABEL_SOURCE_INVALID')
        _public_record(source, kind='RULES', provider=body['provider'], event_id=event_id, now=now)
        source_payload = body.get('payload', {})
        market, event_found = _gamma_market(source_payload, mid, event_id)
        bucket = buckets[mid]
        if (source['seq'] >= label['seq'] or source['body']['available_at'] > label['body']['available_at']
                or body.get('source_identity') not in {'event:' + event_id, 'market:' + mid, mid}
                or (body['provider'] == 'GAMMA_EVENT' and not event_found)
                or (body.get('source_identity') == 'event:' + event_id and not event_found)
                or market.get('closed') is not True
                or market.get('conditionId', market.get('condition_id')) != bucket['condition_id']):
            raise EvidenceError('ATTESTATION_LABEL_SOURCE_INVALID')
        try:
            outcomes = _gamma_field(market.get('outcomes'))
            prices = _gamma_field(market.get('outcomePrices'))
            tokens = _gamma_field(market.get('clobTokenIds'))
            values = [Decimal(str(x)) for x in prices]
        except (InvalidOperation, TypeError):
            raise EvidenceError('ATTESTATION_LABEL_SOURCE_INVALID') from None
        if (outcomes != ['Yes', 'No'] or tokens != [bucket['yes_token'], bucket['no_token']]
                or values not in ([Decimal(0), Decimal(1)], [Decimal(1), Decimal(0)])
                or payload['value'] != int(values[0])):
            raise EvidenceError('ATTESTATION_LABEL_SOURCE_INVALID')
        source_shas.add(source['sha256'])
    return source_shas


def _check_weather(view, rows, *, station, day, tz_name, now, gamma_shas):
    try:
        target_date = date.fromisoformat(day)
        zone = ZoneInfo(tz_name)
    except (ValueError, TypeError, ZoneInfoNotFoundError):
        raise EvidenceError('ATTESTATION_TIMEZONE_UNKNOWN') from None
    day_start = datetime.combine(target_date, day_time.min, zone).timestamp()
    day_end = datetime.combine(target_date + timedelta(days=1), day_time.min, zone).timestamp()
    by_id = {r['id']: r for r in rows}
    normalized = []
    relevant_raw = set()
    processed_raw = set()
    observation_count = 0
    for row in rows:
        if view._expired():
            raise EvidenceError('ATTESTATION_ARCHIVE_INCOMPLETE_BOUND')
        payload = row['body'].get('payload', {})
        _public_record(row, kind='OFFICIAL_OBSERVATION', provider='NOAA_AWC',
                       event_id='station:' + station, now=now)
        if row['body'].get('source_identity') != station:
            raise EvidenceError('ATTESTATION_RAW_DERIVED_MISMATCH')
        if 'response' in payload and 'observations' in payload:
            raise EvidenceError('ATTESTATION_RAW_RESPONSE_INVALID')
        if 'response' in payload:
            if not isinstance(payload['response'], list):
                raise EvidenceError('ATTESTATION_RAW_RESPONSE_INVALID')
            for item in payload['response']:
                if not isinstance(item, dict):
                    raise EvidenceError('ATTESTATION_RAW_RESPONSE_INVALID')
                try:
                    observed_at = finite(float(item['obsTime']))
                except (KeyError, TypeError, ValueError, OverflowError):
                    raise EvidenceError('ATTESTATION_RAW_RESPONSE_INVALID') from None
                if day_start <= observed_at < day_end:
                    relevant_raw.add(row['id'])
            continue
        if not isinstance(payload.get('observations'), list):
            raise EvidenceError('ATTESTATION_NORMALIZED_OBSERVATION_INVALID')
        raw = by_id.get(payload.get('raw_evidence_id'))
        if raw is None:
            raise EvidenceError('ATTESTATION_REFERENCE_MISSING')
        if raw['sha256'] != sha(payload.get('raw_evidence_sha256')):
            raise EvidenceError('ATTESTATION_REFERENCE_HASH_MISMATCH')
        raw_payload = raw['body'].get('payload', {})
        _public_record(raw, kind='OFFICIAL_OBSERVATION', provider='NOAA_AWC',
                       event_id='station:' + station, now=now)
        if (raw['seq'] >= row['seq'] or raw['body']['available_at'] > row['body']['available_at']
                or raw['body']['source_identity'] != row['body']['source_identity']
                or raw['body']['revision'] != row['body']['revision']
                or raw['body']['received_at'] != row['body']['received_at']
                or not isinstance(raw_payload.get('response'), list)
                or payload.get('settlement_station_context') != station):
            raise EvidenceError('ATTESTATION_RAW_DERIVED_MISMATCH')
        accepted = payload['observations']
        observation_count += len(accepted)
        if observation_count > MAX_OBSERVATIONS:
            raise EvidenceError('ATTESTATION_ARCHIVE_INCOMPLETE_BOUND')
        # The attestation policy covers the entire target local day as of this
        # raw receipt. It depends on the raw record and rule, never on accepted
        # output. The one-second margin includes a reading at local midnight.
        max_age = max(1.0, raw['body']['received_at'] - day_start + 1.0)
        policy_sha = digest(dict(raw_id=raw['id'], raw_sha256=raw['sha256'],
                                 station=station, official_max_age_seconds=max_age))
        if payload.get('normalization_sha256') != policy_sha:
            raise EvidenceError('ATTESTATION_NORMALIZATION_POLICY_MISMATCH')
        parsed = parse_awc_metar(raw_payload['response'], station=station,
                                 received_at=raw['body']['received_at'],
                                 max_age_seconds=max_age)
        replay = parsed['observations']
        target_items = sum(day_start <= finite(float(item['obsTime'])) < day_end
                           for item in raw_payload['response'])
        target_replay = sum(day_start <= obs['observed_at'] < day_end for obs in replay)
        if target_replay != target_items:
            raise EvidenceError('ATTESTATION_RAW_RESPONSE_INVALID')
        try:
            reading = lambda obs: (identity(obs['station']), finite(obs['observed_at']),
                                   finite(obs['temperature_c'], nonnegative=False))
            if Counter(map(reading, accepted)) != Counter(map(reading, replay)):
                raise EvidenceError('ATTESTATION_RAW_DERIVED_MISMATCH')
        except (KeyError, TypeError, ValueError, OverflowError):
            raise EvidenceError('ATTESTATION_RAW_DERIVED_MISMATCH') from None
        processed_raw.add(raw['id'])
        if view._expired():
            raise EvidenceError('ATTESTATION_ARCHIVE_INCOMPLETE_BOUND')
        if raw['sha256'] in gamma_shas:
            raise EvidenceError('ATTESTATION_SOURCE_LINEAGE_NOT_DISTINCT')
        normalized.append(row)
    if relevant_raw - processed_raw:
        raise EvidenceError('ATTESTATION_RAW_UNPROCESSED')
    return normalized


def attest_day(base, day, *, now):
    now = finite(now)
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', day) or day < START_DAY:
        raise EvidenceError('ATTESTATION_DAY_INVALID')
    root = base / day
    cs = json.loads((root / 'capture-status.json').read_text())
    ls = json.loads((root / 'labels-status.json').read_text())
    ids = ls['label_ids']
    if type(ids) is not dict or not ids or len(set(ids.values())) != len(ids):
        raise EvidenceError('ATTESTATION_LABEL_SET_INVALID')
    with _ReadView(root / 'source.sqlite') as view:
        capture = view.get(cs['capture_id'])
        if sha(cs.get('prediction_sha256')) != sha(capture['body'].get('details', {}).get('prediction_sha256')):
            raise EvidenceError('ATTESTATION_CAPTURE_VECTOR_INVALID')
        event_id = capture['event_id']
        rule_state = view.latest('RULE_STATE', event_id)
        if rule_state is None:
            return {'day': day, 'event_id': event_id, 'state': 'ATTESTATION_BLOCKED_RULE_FINGERPRINT_MISSING'}
        details = rule_state['body']['details']
        if details.get('quarantined'):
            return {'day': day, 'event_id': event_id, 'state': 'ATTESTATION_BLOCKED_RULE_QUARANTINED'}
        rule = details['preimage']
        labels = {mid: view.get(lid) for mid, lid in ids.items()}
        _join_capture(view, capture, rule_state, rule, labels, day=day, now=now)
        gamma_shas = _check_label_sources(view, labels, event_id=event_id, now=now, rule=rule)
        weather = list(view.station_rows(identity(rule['station'])))
        normalized = _check_weather(view, weather, station=rule['station'], day=day,
                                    tz_name=rule['timezone'], now=now, gamma_shas=gamma_shas)
        result = attest_resolved_day(rule_fingerprint_payload=rule, label_records=labels,
                                     official_observation_records=normalized, now=now)
        if view._expired():
            raise EvidenceError('ATTESTATION_ARCHIVE_INCOMPLETE_BOUND')
        return {'day': day, 'event_id': event_id, **result}


def run(base=BASE, *, now=None):
    now = time.time() if now is None else now
    days = resolved_days(base)
    if not days:
        raise SystemExit('WAITING_FORWARD_LABELS')
    out = {'version': 'alpha_v11_brain_label_attestation_v2',
           'days': [attest_day(base, day, now=now) for day in days],
           'financial_authority': False, 'automatic_promotion': False}
    (base / 'label-attestation-status.json').write_text(json.dumps(out, indent=2, sort_keys=True) + '\n')
    os.chmod(base / 'label-attestation-status.json', 0o600)
    print(json.dumps(out, sort_keys=True))
    return out


if __name__ == '__main__':
    run(BASE)
