#!/usr/bin/env python3
"""Research-only historical point retrieval. No operational source/receipt claims.

Run with --help. Default is a dry plan. The frozen GEFS plan and splits are
preserved; provider expectations omit unsupported hours without interpolation.
"""
import argparse
import asyncio
from collections import defaultdict
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import date, datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import time
from urllib.parse import urlsplit

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from polymarket_scanner.v11 import ecmwf_grib
from polymarket_scanner.v11.ecmwf_sources import (
    BASE, ByteRange, ECMWFRequest, ECMWFCollector, MAX_INDEX_BYTES, MAX_INDEX_ROWS,
)
from polymarket_scanner.v11.evidence import EvidenceError
from polymarket_scanner.v11.model_panel import SourceIdentity, coordinates, strict_json
from polymarket_scanner.v11.pws_quality import geometry

VERSION = 'alpha_v11_ecmwf_historical_v2'
WORKTREE_ROOT = ROOT
DATABASE_ROOT = Path('/home/alphaadmin/AlphaV11_BrainWork')
PROGRESS_ROOT = Path('/home/alphaadmin/AlphaV11_Commissioning/evidence')
SOURCE_COMMIT = '2e85fd29a794f46dc02c6e2e4376b73492485793'
PUBLIC_ROOT = 'https://ecmwf-forecasts.s3.eu-central-1.amazonaws.com'
FLAGS = dict(financial_authority=False, promotion_authority=False)
SPLITS = {'TRAIN', 'DEVELOPMENT', 'HISTORICAL_CONFIRMATION'}


@dataclass(frozen=True)
class HistoricalRequest(ECMWFRequest):
    """Reviewed current layouts on the common six-hour multi-model cadence.

    SourceIdentity here is only an internal adapter input, never exported as a
    reviewed operational release. Both its digest and the request header binding
    are observed from this response, not independent release evidence.
    """
    def __post_init__(self):
        if type(self.step) is not int or self.step < 0 or self.step % 6:
            raise EvidenceError('UNSUPPORTED_PROVIDER_FORECAST_HOUR')
        ECMWFRequest(self.source, self.initialized_at, self.step,
                     self.member, self.grib_signature_sha256)


def request_for(spec, signature='0' * 64):
    provider, run_date, cycle, hour, member = spec
    run = datetime.fromisoformat(run_date).replace(hour=cycle, tzinfo=timezone.utc).timestamp()
    source = SourceIdentity(f'ECMWF_{provider}_ENS', 'historical-observed-header-only',
                            'ecmwf-open-data:0p25', signature)
    return HistoricalRequest(source, run, hour, member, signature)


def public_url(request, source_root=PUBLIC_ROOT):
    return source_root + request.url.removeprefix(BASE)


def file_ranges(index, requests):
    """Reviewed adapter's exact selectors/range fences, parsed once per file.

    Missing members remain individual failures. No aggregate download is made;
    each returned ByteRange is used in its own bounded GET.
    """
    if type(index) is not bytes or not 0 < len(index) <= MAX_INDEX_BYTES:
        raise EvidenceError('ECMWF_INDEX_BYTES_BOUND')
    if (type(requests) is not tuple or not 1 <= len(requests) <= 51 or
            any(not isinstance(r, ECMWFRequest) for r in requests) or
            len({r.url for r in requests}) != 1 or
            len({r.member for r in requests}) != len(requests)):
        raise EvidenceError('ECMWF_REQUEST_BATCH_BOUND')
    lines = index.splitlines()
    if not 1 <= len(lines) <= MAX_INDEX_ROWS:
        raise EvidenceError('ECMWF_INDEX_ROWS_BOUND')
    names = tuple(requests[0].selectors)
    wanted = {tuple(r.selectors[n] for n in names): r.member for r in requests}
    found = {}; previous_end = 0
    sha = hashlib.sha256(index).hexdigest()
    for line in lines:
        row = strict_json(line, MAX_INDEX_BYTES)
        if type(row) is not dict or '_offset' not in row or '_length' not in row:
            raise EvidenceError('ECMWF_INDEX_SCHEMA')
        offset, length = row['_offset'], row['_length']
        if (type(offset) is not int or type(length) is not int or offset < previous_end
                or length <= 0 or offset+length > 16*1024**3):
            raise EvidenceError('ECMWF_INDEX_OFFSETS')
        previous_end = offset+length
        actual = {n: str(row.get(n, '')) for n in names}
        if type(row.get('type')) is str and row['type'] in {'cf', 'fc'} and 'number' not in row:
            actual['number'] = '0'
        t = actual['time']
        if t in {'0', '6', '12', '18'}: actual['time'] = f'{int(t):02}00'
        if t in {'00', '06'}: actual['time'] = t+'00'
        member = wanted.get(tuple(actual[n] for n in names))
        if member is not None:
            if member in found:
                raise EvidenceError('ECMWF_DUPLICATE_FIELD')
            found[member] = ByteRange(offset, length, sha)
    return found


def supported_hours(row, provider):
    """Reviewed common six-hour multi-model subset; never interpolate omitted slots."""
    if provider not in {'IFS', 'AIFS'}:
        raise ValueError('PROVIDER')
    limit = 144 if provider == 'IFS' and row['cycle'] in (6, 18) else 360
    return [h for h in row['forecast_hours'] if h <= limit and h % 6 == 0]


def provider_expectations(days, providers):
    result = {}
    for provider in providers:
        station_days = {}
        for sid, row in days.items():
            hours = supported_hours(row, provider)
            if not hours:
                raise ValueError('NO_SUPPORTED_PROVIDER_HOURS')
            station_days[sid] = dict(
                required_hours=hours,
                expected_values=51 * len(hours),
                unsupported_plan_hours_omitted=[h for h in row['forecast_hours'] if h not in hours],
            )
        result[provider] = dict(
            temporal_resolution_hours=6,
            temporal_resolution_policy='reviewed common six-hour multi-model panel; no interpolation',
            interpolation=False,
            station_days=station_days,
        )
    return result


def provider_policies(expectations):
    """Compact progress identity; exact per-day expectations live in SQLite."""
    result = {}
    for provider, item in expectations.items():
        omitted = sorted({h for day in item['station_days'].values()
                          for h in day['unsupported_plan_hours_omitted']})
        result[provider] = dict(
            temporal_resolution_hours=item['temporal_resolution_hours'],
            temporal_resolution_policy=item['temporal_resolution_policy'],
            interpolation=False,
            unsupported_plan_hours_omitted=omitted,
        )
    return result


def load_plan(path, providers):
    raw = Path(path).read_bytes()
    plan = strict_json(raw, 8 * 1024**2)
    if (plan.get('financial_authority') is not False or
            plan.get('promotion_authority') is not False):
        raise ValueError('NONFINANCIAL_PLAN_REQUIRED')
    days = {}; specs = {}; deps = defaultdict(list)
    for row in plan['station_days']:
        station = row['station']
        if not isinstance(station, str) or not station or '|' in station:
            raise ValueError('STATION_IDENTITY')
        for name in ('target_date', 'run_date'):
            if date.fromisoformat(row[name]).isoformat() != row[name]:
                raise ValueError('PLAN_DATE')
        coordinates(row['latitude'], row['longitude'])
        if type(row['cycle']) is not int or row['cycle'] not in (0, 6, 12, 18):
            raise ValueError('PLAN_CYCLE')
        if row['split'] not in SPLITS:
            raise ValueError('PLAN_SPLIT')
        hours = row['forecast_hours']
        if (not isinstance(hours, list) or not hours or len(hours) > 121 or
                any(type(h) is not int or not 0 <= h <= 360 for h in hours) or
                len(set(hours)) != len(hours)):
            raise ValueError('PLAN_HOURS')
        sid = station + '|' + row['target_date']
        if sid in days:
            raise ValueError('DUPLICATE_STATION_DAY')
        days[sid] = row
        for provider in providers:
            for hour in supported_hours(row, provider):
                for member in range(51):
                    spec = (provider, row['run_date'], row['cycle'], hour, member)
                    key = '|'.join(map(str, spec))
                    specs[key] = spec
                    deps[key].append(sid)
    if not 1 <= len(days) <= 10000:
        raise ValueError('PLAN_STATION_DAY_BOUND')
    return hashlib.sha256(raw).hexdigest(), plan.get('artifact_sha256'), days, specs, deps


def disk_snapshot(output):
    root = shutil.disk_usage('/')
    destination = shutil.disk_usage(output.parent)
    return dict(root_used_percent=100 * root.used / root.total,
                root_free_bytes=root.free, output_used_percent=100 * destination.used / destination.total,
                output_free_bytes=destination.free)


def disk_safe(snapshot):
    return snapshot['root_used_percent'] < 84 and snapshot['output_used_percent'] < 84


def atomic_json(path, payload):
    fd, temporary = tempfile.mkstemp(prefix=path.name+'.', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(payload, stream, indent=2, sort_keys=True, allow_nan=False)
            stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def open_db(path, identity, days, specs, expectations):
    validate_existing(path, None, identity)
    db = sqlite3.connect(path)
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('PRAGMA synchronous=FULL')
    db.execute('PRAGMA foreign_keys=ON')
    db.executescript('''
      CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS station_days(
        station_day TEXT PRIMARY KEY, split TEXT NOT NULL, expected_values INTEGER NOT NULL,
        context_json TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS station_provider_expectations(
        station_day TEXT REFERENCES station_days(station_day), provider TEXT NOT NULL,
        expected_values INTEGER NOT NULL, required_hours_json TEXT NOT NULL,
        omitted_hours_json TEXT NOT NULL, PRIMARY KEY(station_day,provider));
      CREATE TABLE IF NOT EXISTS messages(
        message_key TEXT PRIMARY KEY, provider TEXT, run_date TEXT, cycle INTEGER,
        hour INTEGER, member INTEGER, status TEXT NOT NULL DEFAULT 'PENDING'
          CHECK(status IN ('PENDING','DONE','FAILED')),
        attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts BETWEEN 0 AND 3),
        public_url TEXT, index_sha256 TEXT, index_byte_length INTEGER, grib_sha256 TEXT, byte_length INTEGER,
        range_start INTEGER, observed_header_sha256 TEXT, grid_sha256 TEXT,
        error TEXT, updated_at REAL);
      CREATE TABLE IF NOT EXISTS point_values(
        message_key TEXT REFERENCES messages(message_key),
        station_day TEXT REFERENCES station_days(station_day), provider TEXT,
        member INTEGER, hour INTEGER, value_k REAL CHECK(value_k BETWEEN 150 AND 350),
        distance_km REAL CHECK(distance_km BETWEEN 0 AND 50), nearest_lat REAL, nearest_lon REAL,
        PRIMARY KEY(message_key,station_day));
      CREATE INDEX IF NOT EXISTS points_by_day ON point_values(station_day,provider);
    ''')
    encoded = json.dumps(identity, sort_keys=True)
    previous = db.execute("SELECT value FROM metadata WHERE key='identity'").fetchone()
    if previous and previous[0] != encoded:
        db.close()
        raise ValueError('RESUME_IDENTITY_MISMATCH')
    with db:
        db.execute("INSERT OR IGNORE INTO metadata VALUES('identity',?)", (encoded,))
        db.executemany('INSERT OR IGNORE INTO station_days VALUES(?,?,?,?)',
                       [(sid, row['split'], sum(51*len(supported_hours(row, p))
                           for p in identity['selected_providers']), json.dumps(row, sort_keys=True))
                        for sid, row in days.items()])
        db.executemany('INSERT OR IGNORE INTO station_provider_expectations VALUES(?,?,?,?,?)',
                       [(sid, provider, entry['expected_values'], json.dumps(entry['required_hours']),
                         json.dumps(entry['unsupported_plan_hours_omitted']))
                        for provider, expectation in expectations.items()
                        for sid, entry in expectation['station_days'].items()])
        db.executemany('''INSERT OR IGNORE INTO messages
                       (message_key,provider,run_date,cycle,hour,member) VALUES(?,?,?,?,?,?)''',
                       [(key, *spec) for key, spec in specs.items()])
        # A process killed after reserving the final attempt must not strand PENDING.
        db.execute("UPDATE messages SET status='FAILED', error='INTERRUPTED_ATTEMPT_LIMIT' "
                   "WHERE status='PENDING' AND attempts=3")
    return db


class AnonymousFetcher:
    def __init__(self, transport=None):
        self.transport = transport
        self.collector = ECMWFCollector(None)
        self.client = httpx.AsyncClient(
            transport=transport, trust_env=False, follow_redirects=False, timeout=15., auth=None,
            limits=httpx.Limits(max_connections=4, max_keepalive_connections=4, keepalive_expiry=30.))

    async def get(self, url, maximum, selection=None):
        # Reuse one anonymous connection pool for the run. The reviewed collector
        # clears cookies before every request and enforces exact range/byte fences.
        async with asyncio.timeout(45):
            return await self.collector._get(self.client, url, maximum, byte_range=selection)

    async def close(self):
        await self.client.aclose()


def progress(db, identity, providers, state, started, initial_done, disk):
    totals = {}
    completed = {}
    for provider in providers:
        counts = dict(db.execute('SELECT status,count(*) FROM messages WHERE provider=? GROUP BY status', (provider,)))
        totals[provider] = dict(total=sum(counts.values()), done=counts.get('DONE', 0),
                                failed=counts.get('FAILED', 0), pending=counts.get('PENDING', 0))
        completed[provider] = {sid for sid, in db.execute('''SELECT s.station_day FROM station_provider_expectations s
            LEFT JOIN point_values p ON p.station_day=s.station_day AND p.provider=s.provider
            LEFT JOIN messages m ON m.message_key=p.message_key AND m.status='DONE'
            WHERE s.provider=? GROUP BY s.station_day HAVING count(m.message_key)=s.expected_values''', (provider,))}
    combined = set.intersection(*completed.values())
    split_days = defaultdict(set)
    for sid, split in db.execute('SELECT station_day,split FROM station_days'):
        split_days[split].add(sid)
    done = sum(t['done'] for t in totals.values())
    return dict(identity, state=state, updated_at=time.time(), providers=totals,
                station_days_total=sum(map(len, split_days.values())),
                station_days_complete={p: len(s) for p, s in completed.items()},
                station_days_combined_complete=len(combined), combined_scope=list(providers),
                splits={s: dict(total=len(ids), combined_complete=len(ids & combined),
                                by_provider={p: len(ids & c) for p, c in completed.items()})
                        for s, ids in split_days.items()}, disk=disk,
                session_messages_per_minute=(done-initial_done)*60/max(.001, time.monotonic()-started))


def validate_existing(output, progress_path, identity=None):
    """Read-only identity validation before SQLite pragmas or progress writes."""
    if output.exists():
        with sqlite3.connect(output.as_uri()+'?mode=ro', uri=True) as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='metadata'").fetchone():
                raise ValueError('NOT_A_BACKFILL_DATABASE')
            row = db.execute("SELECT value FROM metadata WHERE key='identity'").fetchone()
            previous = json.loads(row[0]) if row else {}
            if previous.get('worker_version') != VERSION:
                raise ValueError('NOT_A_BACKFILL_DATABASE')
            if identity is not None and previous != identity:
                raise ValueError('RESUME_IDENTITY_MISMATCH')
    if progress_path is not None and progress_path.exists():
        previous = json.loads(progress_path.read_text())
        if previous.get('worker_version') != VERSION:
            raise ValueError('NOT_A_BACKFILL_PROGRESS_FILE')
        if identity is not None and any(previous.get(k) != v for k, v in identity.items()):
            raise ValueError('RESUME_PROGRESS_IDENTITY_MISMATCH')


def checked_paths(plan, output, progress_path):
    plan = Path(plan).resolve()
    destinations = []
    for value, allowed in ((output, DATABASE_ROOT), (progress_path, PROGRESS_ROOT)):
        original = Path(value)
        if original.is_symlink():
            raise ValueError('OUTPUT_SYMLINK')
        p = original.resolve()
        roots = [allowed.resolve()]
        # Tests may explicitly monkeypatch ROOT; the production checkout is not
        # an authorized runtime destination merely because it contains this code.
        if ROOT != WORKTREE_ROOT:
            roots.append(ROOT.resolve())
        if not any(p != root and p.is_relative_to(root) for root in roots) or not p.parent.is_dir():
            raise ValueError('OUTPUT_OUTSIDE_ALLOWED_DIRECTORY_OR_MISSING_PARENT')
        destinations.append(p)
    output, progress_path = destinations
    reserved = {output, Path(str(output)+'-wal'), Path(str(output)+'-shm'),
                Path(str(output)+'-journal'), Path(str(output)+'.lock')}
    progress_reserved = {progress_path, Path(str(progress_path)+'.lock')}
    if plan in reserved | progress_reserved or reserved & progress_reserved:
        raise ValueError('OUTPUT_PATH_COLLISION')
    for p in reserved | progress_reserved:
        if p.is_symlink() or (p.exists() and (not p.is_file() or p.stat().st_nlink != 1)):
            raise ValueError('OUTPUT_UNSAFE_FILE')
        if p.name.endswith('.lock') and p.exists() and p.stat().st_size:
            raise ValueError('NOT_A_BACKFILL_LOCK')
    if not output.exists() and any(p.exists() for p in reserved - {output, Path(str(output)+'.lock')}):
        raise ValueError('OUTPUT_ORPHAN_SQLITE_SIDECAR')
    validate_existing(output, progress_path)
    return plan, output, progress_path


async def run(*, plan, output, progress_path, provider='BOTH', max_messages=None,
              concurrency=4, execute=False, probe_public_smoke=False,
              source_root=PUBLIC_ROOT, transport=None, disk_check=disk_snapshot,
              sleep=asyncio.sleep):
    if type(concurrency) is not int or not 1 <= concurrency <= 4:
        raise ValueError('CONCURRENCY_BOUND')
    if max_messages is not None and (type(max_messages) is not int or max_messages < 1):
        raise ValueError('MAX_MESSAGES_BOUND')
    if provider not in {'IFS', 'AIFS', 'BOTH'}:
        raise ValueError('PROVIDER')
    parsed = urlsplit(source_root)
    if (parsed.username or parsed.password or parsed.query or parsed.fragment or
            (source_root != PUBLIC_ROOT and transport is None)):
        raise ValueError('PUBLIC_ROOT_OVERRIDE_REQUIRES_OFFLINE_TEST_TRANSPORT')
    if probe_public_smoke and (max_messages is None or max_messages > 4):
        raise ValueError('SMOKE_REQUIRES_MAX_MESSAGES_1_TO_4')
    plan, output, progress_path = checked_paths(plan, output, progress_path)
    providers = ('IFS', 'AIFS') if provider == 'BOTH' else (provider,)
    plan_sha, artifact_sha, days, specs, deps = load_plan(plan, providers)
    expectations = provider_expectations(days, providers)
    identity = dict(worker_version=VERSION, source_adapter_commit=SOURCE_COMMIT,
                    decoder_version=ecmwf_grib.VERSION, source_root=source_root,
                    public_anonymous=True, plan_sha256=plan_sha, plan_artifact_sha256=artifact_sha,
                    output_sqlite=str(output), progress_json=str(progress_path),
                    selected_providers=list(providers),
                    provider_policies=provider_policies(expectations), evidence_class=(
                        'SYNTHETIC' if transport is not None else 'HISTORICAL_PUBLIC_RETRIEVAL'),
                    historical_availability='UNKNOWN', availability_at=None,
                    historical_classification='RETRIEVED_LATER_RESEARCH_ONLY',
                    release_binding='OBSERVED_HEADER_ONLY', operational_release_reviewed=False,
                    research_input_only=True, **FLAGS)
    started = time.monotonic()
    with ExitStack() as locks:
        for destination in (output, progress_path):
            fd = os.open(str(destination)+'.lock', os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            lock = locks.enter_context(os.fdopen(fd, 'a'))
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError('BACKFILL_WORKER_ALREADY_RUNNING') from None
        validate_existing(output, progress_path, identity)
        disk = disk_check(output)
        if not disk_safe(disk):
            payload = dict(identity, state='PAUSED_DISK', disk=disk,
                           planned_messages=len(specs), planned_station_days=len(days))
            atomic_json(progress_path, payload)
            return payload
        db = open_db(output, identity, days, specs, expectations)
        try:
            initial_done = db.execute("SELECT count(*) FROM messages WHERE status='DONE'").fetchone()[0]
            def publish(state):
                payload = progress(db, identity, providers, state, started, initial_done, disk_check(output))
                atomic_json(progress_path, payload)
                return payload
            groups = defaultdict(list)
            for key, status, attempts in db.execute('SELECT message_key,status,attempts FROM messages'):
                if status == 'DONE' or attempts >= 3:
                    continue
                req = request_for(specs[key])
                groups[public_url(req, source_root)].append(key)
            if not execute and not probe_public_smoke:
                return publish('PLANNED')
            # Smoke is exactly one run/cycle/hour, at most control+member 1/provider.
            if probe_public_smoke and groups:
                first = min((specs[k][1:4] for keys in groups.values() for k in keys
                             if specs[k][3] % 6 == 0))
                groups = {url: [k for k in keys if specs[k][1:4] == first and specs[k][4] <= 1]
                          for url, keys in groups.items()}
            remaining = max_messages
            fetcher = AnonymousFetcher(transport)
            publish('RUNNING')
            paused = False
            for url, keys in groups.items():
                if not keys or remaining == 0:
                    continue
                if remaining is not None:
                    keys = keys[:remaining]
                # One cached response and one strict range parse per file identity.
                index = None
                selections = None
                for start in range(0, len(keys), concurrency):
                    if not disk_safe(disk_check(output)):
                        paused = True
                        break
                    batch = keys[start:start+concurrency]
                    async def retrieve(key):
                        nonlocal index, selections, paused
                        req = request_for(specs[key])
                        while True:
                            attempts = db.execute('SELECT attempts FROM messages WHERE message_key=?', (key,)).fetchone()[0]
                            if attempts >= 3:
                                return
                            if not disk_safe(disk_check(output)):
                                paused = True
                                return
                            with db:
                                db.execute("UPDATE messages SET attempts=attempts+1,status='PENDING',updated_at=? WHERE message_key=?",
                                           (time.time(), key))
                            try:
                                # Index acquisition is serialized by the per-file lock;
                                # failed responses are not cached, successful bodies are.
                                async with index_lock:
                                    if index is None:
                                        index = await fetcher.get(url[:-6]+'.index', MAX_INDEX_BYTES)
                                    if selections is None:
                                        requests = tuple(request_for(specs[k]) for k in keys)
                                        selections = file_ranges(index, requests)
                                with db:
                                    db.execute('UPDATE messages SET index_sha256=?,index_byte_length=?,public_url=? WHERE message_key=?',
                                               (hashlib.sha256(index).hexdigest(), len(index), url, key))
                                selection = selections.get(req.member)
                                if selection is None:
                                    raise EvidenceError('ECMWF_REQUESTED_FIELD_NOT_AVAILABLE')
                                if not disk_safe(disk_check(output)):
                                    paused = True
                                    return
                                raw = await fetcher.get(url, selection.length, selection)
                                signature = ecmwf_grib.release_signature(raw)
                                with db:
                                    db.execute('''UPDATE messages SET grib_sha256=?,byte_length=?,range_start=?,
                                        observed_header_sha256=? WHERE message_key=?''',
                                        (hashlib.sha256(raw).hexdigest(), len(raw), selection.start, signature, key))
                                observed = request_for(specs[key], signature)
                                targets = tuple(ecmwf_grib.HistoricalPointTarget(latitude=days[s]['latitude'], longitude=days[s]['longitude'],
                                                                maximum_grid_distance_km=50.) for s in deps[key])
                                points = ecmwf_grib.decode_stations(raw, request=observed, targets=targets)
                                del raw
                                values = [(key, sid, specs[key][0], req.member, req.step, point['kelvin'],
                                           geometry(target.latitude, target.longitude, point['latitude'], point['longitude'])[0],
                                           point['latitude'], point['longitude'])
                                          for sid, target, point in zip(deps[key], targets, points)]
                                with db:
                                    db.executemany('INSERT INTO point_values VALUES(?,?,?,?,?,?,?,?,?)', values)
                                    db.execute("UPDATE messages SET status='DONE',grid_sha256=?,error=NULL,updated_at=? WHERE message_key=?",
                                               (points[0]['grid_sha256'], time.time(), key))
                                return
                            except (EvidenceError, httpx.HTTPError, TimeoutError) as exc:
                                # Do not retain raw fields in exception frames or retry locals.
                                if 'raw' in locals():
                                    del raw
                                reason = str(exc) if isinstance(exc, EvidenceError) else type(exc).__name__
                                with db:
                                    db.execute("UPDATE messages SET status='FAILED',error=?,updated_at=? WHERE message_key=?",
                                               (reason, time.time(), key))
                                if attempts < 2:
                                    await sleep(3 ** (attempts + 1))
                    index_lock = asyncio.Lock()
                    await asyncio.gather(*(retrieve(key) for key in batch))
                    if remaining is not None:
                        remaining -= len(batch)
                    publish('PAUSED_DISK' if paused else 'RUNNING')
                    if paused:
                        break
                index = None
                if paused:
                    break
            left = db.execute("SELECT count(*) FROM messages WHERE status!='DONE'").fetchone()[0]
            return publish('PAUSED_DISK' if paused else 'COMPLETE' if not left else 'PARTIAL' if max_messages else 'ATTENTION_REQUIRED')
        finally:
            if 'fetcher' in locals():
                await fetcher.close()
            db.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', required=True, type=Path)
    parser.add_argument('--output-sqlite', required=True, type=Path)
    parser.add_argument('--progress-json', required=True, type=Path)
    parser.add_argument('--provider', choices=('IFS', 'AIFS', 'BOTH'), default='BOTH')
    parser.add_argument('--max-messages', type=int)
    parser.add_argument('--concurrency', type=int, default=4)
    parser.add_argument('--execute', action='store_true', help='Enable retrieval; default only prepares the local plan')
    parser.add_argument('--probe-public-smoke', action='store_true', help='Tiny anonymous run; requires --max-messages <=4')
    args = parser.parse_args()
    try:
        result = asyncio.run(run(plan=args.plan, output=args.output_sqlite, progress_path=args.progress_json,
                                 provider=args.provider, max_messages=args.max_messages, concurrency=args.concurrency,
                                 execute=args.execute, probe_public_smoke=args.probe_public_smoke))
    except (ValueError, OSError, sqlite3.Error, EvidenceError) as exc:
        print(json.dumps(dict(state='ERROR', error=str(exc), **FLAGS)))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0 if result['state'] in {'COMPLETE', 'PLANNED', 'PARTIAL'} else 2


if __name__ == '__main__':
    sys.exit(main())
