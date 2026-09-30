#!/usr/bin/env python3
"""Immutable historical point-panel audit; never a live model/artifact writer.

The archived t2m fields are point forecasts, not interval extremes. In particular,
filtering a three-hour bracketing plan to six hours need not preserve brackets.
This tool retains those facts and refuses exact-day fitting rather than repairing
inputs, interpolating, or relabelling sampled extrema as complete daily extrema.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta, timezone
import gzip
import hashlib
from itertools import groupby
import json
import math
from pathlib import Path
import platform
import sqlite3
import subprocess
import sys
import zlib
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
VERSION = 'alpha_v11_multimodel_historical_panel_v1'
PROVIDERS = {'GEFS': 31, 'IFS': 51, 'AIFS': 51}
SPLITS = ('TRAIN', 'DEVELOPMENT', 'HISTORICAL_CONFIRMATION')
FAMILIES = ('daily_high_temperature', 'daily_low_temperature')
CONFIRMATION = 'HISTORICAL_CONFIRMATION_NOT_FORWARD_UNTOUCHED'
FLAGS = dict(financial_authority=False, promotion_authority=False,
             host_approved=False, order_authority=False)


class PanelError(ValueError):
    pass


def require(ok, reason):
    if not ok:
        raise PanelError(reason)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=True, allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def file_sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def is_sha(value):
    return isinstance(value, str) and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def strict_json(raw):
    def pairs(items):
        out = {}
        for key, value in items:
            require(key not in out, 'DUPLICATE_JSON_KEY')
            out[key] = value
        return out
    def invalid(value):
        raise PanelError('NONFINITE_JSON:' + value)
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)


def artifact(path, expected):
    raw = Path(path).read_bytes()
    require(hashlib.sha256(raw).hexdigest() == expected['file_sha256'], 'INPUT_FILE_HASH:' + Path(path).name)
    obj = strict_json(raw)
    if 'artifact_sha256' in obj:
        body = {k: v for k, v in obj.items() if k != 'artifact_sha256'}
        require(digest(body) == obj['artifact_sha256'], 'ARTIFACT_SELF_HASH')
    return obj


def content_sha(db):
    """All columns, all tables, deterministic ordering; includes retrieval times."""
    h = hashlib.sha256()
    for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
        require(name.replace('_', '').isalnum(), 'SQL_TABLE_NAME')
        columns = [r[1] for r in db.execute(f'PRAGMA table_info("{name}")')]
        h.update(canonical([name, columns]) + b'\n')
        order = ','.join(str(i + 1) for i in range(len(columns)))
        for row in db.execute(f'SELECT * FROM "{name}" ORDER BY {order}'):
            h.update(canonical(list(row)) + b'\n')
    return h.hexdigest()


def legacy_gefs_content_sha(db):
    """Recompute the digest used by the already-frozen GEFS manifest."""
    specs = {
        'messages': 'SELECT message_key,run_date,cycle,hour,member,status,attempts,idx_sha256,grib_sha256,bytes,error FROM messages ORDER BY message_key',
        'station_days': 'SELECT station_day,station,target_date,split,expected_values,status FROM station_days ORDER BY station_day',
        'point_values': 'SELECT message_key,station_day,member,hour,value_k,distance_km,nearest_lat,nearest_lon FROM point_values ORDER BY station_day,member,hour,message_key',
    }
    h = hashlib.sha256()
    for name, sql in specs.items():
        h.update((name + '\n').encode())
        for row in db.execute(sql):
            h.update(canonical(list(row)) + b'\n')
    return h.hexdigest()


@contextmanager
def immutable_db(path, expected):
    path = Path(path).resolve()
    def check():
        for suffix in ('-wal', '-journal'):
            side = Path(str(path) + suffix)
            require(not side.exists() or side.stat().st_size == 0, 'UNQUIESCED_SQLITE')
        require(file_sha(path) == expected['file_sha256'], 'DATABASE_FILE_HASH')
    check()
    db = sqlite3.connect(path.as_uri() + '?mode=ro&immutable=1', uri=True)
    db.row_factory = sqlite3.Row
    try:
        db.execute('PRAGMA query_only=ON')
        require(db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok', 'SQLITE_INTEGRITY')
        require(content_sha(db) == expected['content_sha256'], 'DATABASE_CONTENT_HASH')
        yield db
    finally:
        db.close()
        check()


def local_window(day, zone):
    d = date.fromisoformat(day)
    require(d.isoformat() == day, 'LOCAL_DATE')
    tz = ZoneInfo(zone)
    start = datetime.combine(d, time(), tz).timestamp()
    end = datetime.combine(d + timedelta(days=1), time(), tz).timestamp()
    require(0 < end - start <= 26 * 3600, 'LOCAL_DAY_DURATION')
    return start, end


def split_for(day):
    require('2026-08-23' <= day <= '2026-09-28', 'FROZEN_DATE_RANGE')
    return 'TRAIN' if day <= '2026-09-15' else 'DEVELOPMENT' if day <= '2026-09-23' else 'HISTORICAL_CONFIRMATION'


def day_context(row):
    start, end = local_window(row['target_date'], row['timezone'])
    initialized = datetime.fromisoformat(row['run_utc'])
    require(initialized.tzinfo is not None and initialized.utcoffset() == timedelta(0), 'EXPLICIT_UTC_RUN_REQUIRED')
    run = initialized.timestamp()
    expected_run = math.floor((start - 3600) / 21600) * 21600
    require(run == expected_run, 'RUN_NOT_PREREGISTERED_PRE_DAY_CYCLE')
    dt = datetime.fromtimestamp(run, timezone.utc)
    require((dt.date().isoformat(), dt.hour) == (row['run_date'], row['cycle']), 'RUN_IDENTITY')
    first = math.floor((start - run) / 10800) * 3
    last = math.ceil((end - run) / 10800) * 3
    require(row['forecast_hours'] == list(range(first, last + 1, 3)), 'PLAN_THREE_HOUR_BRACKETS')
    require(row['split'] == split_for(row['target_date']), 'TEMPORAL_SPLIT_IDENTITY')
    require(is_sha(row['metadata_evidence_sha256']), 'STATION_METADATA_DIGEST')
    return dict(start_utc=start, end_utc_exclusive=end, run_utc=run,
                duration_hours=(end-start)/3600,
                start_forecast_hour=(start-run)/3600, end_forecast_hour=(end-run)/3600)


def coverage(hours, context):
    start, end = context['start_forecast_hour'], context['end_forecast_hour']
    inside = [h for h in hours if start <= h < end]
    require(bool(inside), 'NO_NATIVE_IN_DAY_POINTS')
    return dict(native_hours=hours, in_day_hours=inside,
                excluded_outside_day_hours=[h for h in hours if h not in inside],
                start_native=start in hours, end_native=end in hours,
                brackets_start=min(hours) <= start <= max(hours),
                brackets_end=min(hours) <= end <= max(hours),
                start_gap_hours=max(0, min(hours)-start),
                end_gap_hours=max(0, end-max(hours)),
                exact_day_extreme_supported=False,
                reason='POINT_TEMPERATURES_NOT_INTERVAL_EXTREMES', interpolation=False)


def sampled_extrema(values, hours, context, family):
    """Audit-only native samples in [midnight, next midnight); no outside values."""
    require(family in FAMILIES, 'FAMILY')
    require(len(values) == len(hours) and len(set(hours)) == len(hours), 'SAMPLE_HOURS')
    chosen = [v for h, v in zip(hours, values)
              if context['start_forecast_hour'] <= h < context['end_forecast_hour']]
    require(chosen and all(math.isfinite(v) for v in values), 'SAMPLE_VALUES')
    return (max(chosen) if family == FAMILIES[0] else min(chosen)) - 273.15


def expected_url(provider, row, hour, member):
    model = 'ifs' if provider == 'IFS' else 'aifs-ens'
    stream = 'oper' if provider == 'IFS' and member == 0 else 'enfo'
    kind = ('fc' if member == 0 else 'ef') if provider == 'IFS' else ('cf' if member == 0 else 'pf')
    d, cycle = row['run_date'].replace('-', ''), f"{row['cycle']:02}"
    return f'https://ecmwf-forecasts.s3.eu-central-1.amazonaws.com/{d}/{cycle}z/{model}/0p25/{stream}/{d}{cycle}0000-{hour}h-{stream}-{kind}.grib2'


def message_key(provider, row, hour, member):
    if provider == 'GEFS':
        return f"{row['run_date']}|{row['cycle']:02}|{hour:03}|{member:02}"
    return f"{provider}|{row['run_date']}|{row['cycle']}|{hour}|{member}"


def verify_message(m, provider, row, hour, member):
    require((m['run_date'], m['cycle'], m['hour'], m['member'], m['status'], m['error']) ==
            (row['run_date'], row['cycle'], hour, member, 'DONE', None), 'MESSAGE_IDENTITY_OR_STATUS')
    require(m['message_key'] == message_key(provider, row, hour, member), 'MESSAGE_KEY')
    require(is_sha(m['grib_sha256']) and math.isfinite(m['updated_at']) and m['updated_at'] > 0, 'MESSAGE_PROVENANCE')
    if provider == 'GEFS':
        require(is_sha(m['idx_sha256']) and 0 < m['bytes'] <= 4*1024**2, 'GEFS_PROVENANCE')
    else:
        require(m['provider'] == provider and m['public_url'] == expected_url(provider, row, hour, member), 'ECMWF_SOURCE_IDENTITY')
        require(all(is_sha(m[k]) for k in ('index_sha256', 'observed_header_sha256', 'grid_sha256')),
                'ECMWF_PROVENANCE')
        require(0 < m['byte_length'] <= 4*1024**2 and 0 < m['index_byte_length'] <= 3*1024**2
                and 0 <= m['range_start'] < 16*1024**3, 'ECMWF_RANGE')


def distance_km(lat, lon, near_lat, near_lon):
    lat, near_lat, delta = map(math.radians, (lat, near_lat, near_lon-lon))
    a = math.sin((near_lat-lat)/2)**2 + math.cos(lat)*math.cos(near_lat)*math.sin(delta/2)**2
    return 6371 * 2 * math.asin(min(1, math.sqrt(a)))


def verify_points(db, provider, row, messages, used, points=None):
    sid = row['station'] + '|' + row['target_date']
    hours = row['forecast_hours'] if provider == 'GEFS' else [h for h in row['forecast_hours'] if h % 6 == 0]
    if points is None:
        if provider == 'GEFS':
            points = list(db.execute('SELECT * FROM point_values WHERE station_day=? ORDER BY member,hour', (sid,)))
        else:
            points = list(db.execute('SELECT * FROM point_values WHERE station_day=? AND provider=? ORDER BY member,hour', (sid, provider)))
    expected = [(m, h) for m in range(PROVIDERS[provider]) for h in hours]
    require([(p['member'], p['hour']) for p in points] == expected, 'MEMBER_HOUR_COVERAGE:' + sid + ':' + provider)
    values = defaultdict(list)
    provenance = []
    for p in points:
        key = p['message_key']
        require(key in messages, 'ORPHAN_POINT')
        m = messages[key]
        verify_message(m, provider, row, p['hour'], p['member'])
        require(type(p['member']) is int and type(p['hour']) is int, 'INTEGER_MEMBER_HOUR')
        require(math.isfinite(p['value_k']) and 150 <= p['value_k'] <= 350, 'TEMPERATURE')
        require(-90 <= p['nearest_lat'] <= 90 and -180 <= p['nearest_lon'] <= 360, 'GRID_COORDINATES')
        dist = distance_km(row['latitude'], row['longitude'], p['nearest_lat'], p['nearest_lon'])
        require(0 <= p['distance_km'] <= 50 and dist <= 50 and abs(dist-p['distance_km']) < .2, 'GRID_DISTANCE')
        values[p['member']].append(p['value_k'])
        used.add((key, sid))
        provenance.append(dict(point=dict(p), message_sha256=digest(dict(m))))
    context = day_context(row)
    common = [h for h in hours if h % 6 == 0]
    native_values = [values[m] for m in range(PROVIDERS[provider])]
    common_values = [[v for h, v in zip(hours, vals) if h in common] for vals in native_values]
    return dict(provider=provider, dependence_group='ECMWF_LINEAGE' if provider != 'GEFS' else 'NOAA_GEFS',
                member_ids=list(range(PROVIDERS[provider])), native_hours=hours, values_k=native_values,
                common_six_hour_coverage=coverage(common, context), native_coverage=coverage(hours, context),
                common_six_hour_sampled_extrema_c={f: [sampled_extrema(v, common, context, f) for v in common_values]
                                                 for f in FAMILIES},
                exact_day_extrema_c=None, source_rows_sha256=digest(provenance),
                source_messages_sha256=digest(sorted({p['message_key'] for p in points})),
                historical_available_at=None,
                retrieval_completed_at=max(messages[p['message_key']]['updated_at'] for p in points),
                raw_grib_reverification='UNAVAILABLE_HASH_REFERENCES_ONLY')


def validate_event(event):
    require(event['source_family'] == 'NWS_WRH_TIMESERIES' and event['family'] in FAMILIES
            and event['unit'] in ('C', 'F') and event['complete_final_vector'] is True
            and event['exactly_one_winner'] is True, 'EVENT_SEMANTICS')
    buckets = event['buckets']
    require(len(buckets) == event['bucket_count'] and 2 <= len(buckets) <= 128, 'BUCKET_COUNT')
    require(buckets[0]['lower'] is None and buckets[-1]['upper'] is None, 'BUCKET_TAILS')
    for i, b in enumerate(buckets):
        require(b['status'] == 'FINAL' and b['yes_payout'] in (0, 1)
                and b['no_payout'] == 1-b['yes_payout'], 'PAYOUT')
        require(all(b[k] is None or (type(b[k]) in (int, float) and math.isfinite(b[k]) and b[k] == int(b[k]))
                    for k in ('lower', 'upper')), 'WHOLE_DEGREE_BUCKETS')
        if i:
            require(b['lower'] is not None and buckets[i-1]['upper'] is not None
                    and b['lower'] == buckets[i-1]['upper'] + 1, 'BUCKET_PARTITION')
        if b['lower'] is not None and b['upper'] is not None:
            require(b['lower'] <= b['upper'], 'BUCKET_ORDER')
    require(sum(b['yes_payout'] for b in buckets) == 1, 'ONE_WINNER')
    for key in ('market_id', 'condition_id', 'yes_token', 'no_token'):
        require(all(isinstance(b[key], str) and b[key] for b in buckets)
                and len({b[key] for b in buckets}) == len(buckets), 'BUCKET_IDENTITY')


def assemble(plan, catalog, gefs, ecmwf):
    require(plan['catalog_sha256'] == catalog['artifact_sha256'], 'CATALOG_PLAN_LINK')
    require(plan['financial_authority'] is False and plan['promotion_authority'] is False
            and plan['untouched_forward_holdout_claim'] is False, 'PLAN_AUTHORITY')
    days = {r['station'] + '|' + r['target_date']: r for r in plan['station_days']}
    require(len(days) == len(plan['station_days']) and 0 < len(days) <= 10000, 'DUPLICATE_STATION_DAY')
    sd_g = {r['station_day']: dict(r) for r in gefs.execute('SELECT * FROM station_days')}
    sd_e = {r['station_day']: dict(r) for r in ecmwf.execute('SELECT * FROM station_days')}
    require(set(sd_g) == set(sd_e) == set(days), 'DATABASE_STATION_DAY_SET')
    expectations = {(r['station_day'], r['provider']): dict(r) for r in ecmwf.execute('SELECT * FROM station_provider_expectations')}
    require(set(expectations) == {(sid, p) for sid in days for p in ('IFS', 'AIFS')}, 'PROVIDER_EXPECTATIONS')
    messages = {p: {r['message_key']: dict(r) for r in db.execute('SELECT * FROM messages')}
                for p, db in (('GEFS', gefs), ('ECMWF', ecmwf))}
    used = {'GEFS': set(), 'ECMWF': set()}
    # One sorted scan per immutable store; never add indexes to source databases.
    point_streams = {
        source: iter(groupby(db.execute('SELECT * FROM point_values ORDER BY station_day,member,hour,message_key'),
                            key=lambda r: r['station_day']))
        for source, db in (('GEFS', gefs), ('ECMWF', ecmwf))
    }
    panels = []
    for sid, row in sorted(days.items()):
        ctx = day_context(row)
        require({k: row[k] for k in plan['stations'][row['station']]} == plan['stations'][row['station']], 'STATION_METADATA_IDENTITY')
        require(sd_g[sid] == dict(station_day=sid, station=row['station'], target_date=row['target_date'],
                    split=row['split'], expected_values=31*len(row['forecast_hours']), status='COMPLETE'), 'GEFS_DAY_IDENTITY')
        common = [h for h in row['forecast_hours'] if h % 6 == 0]
        require(strict_json(sd_e[sid]['context_json']) == row and sd_e[sid]['split'] == row['split']
                and sd_e[sid]['expected_values'] == 102*len(common), 'ECMWF_DAY_IDENTITY')
        for p in ('IFS', 'AIFS'):
            ex = expectations[sid, p]
            require(ex['expected_values'] == 51*len(common) and strict_json(ex['required_hours_json']) == common
                    and strict_json(ex['omitted_hours_json']) == [h for h in row['forecast_hours'] if h not in common], 'ECMWF_HOUR_POLICY')
        providers = {}
        day_points = {}
        for source in point_streams:
            found, rows = next(point_streams[source], (None, ()))
            require(found == sid, 'STATION_POINT_COVERAGE_OR_ORPHAN')
            day_points[source] = list(rows)
        for p in PROVIDERS:
            source = 'GEFS' if p == 'GEFS' else 'ECMWF'
            points = [v for v in day_points[source] if p == 'GEFS' or v['provider'] == p]
            providers[p] = verify_points(gefs if p == 'GEFS' else ecmwf, p, row, messages[source], used[source], points)
        panels.append(dict(station_day=sid, city_day=sid, city_identity_basis='FROZEN_ONE_STATION_PER_CITY_COHORT',
                           context=row, local_day=ctx, providers=providers))
    for source, db in (('GEFS', gefs), ('ECMWF', ecmwf)):
        require(next(point_streams[source], None) is None, 'EXTRA_OR_ORPHAN_POINTS')
        require(len(used[source]) == db.execute('SELECT count(*) FROM point_values').fetchone()[0], 'EXTRA_OR_ORPHAN_POINTS')
        require({k for k, _ in used[source]} == set(messages[source]), 'EXTRA_OR_UNREFERENCED_MESSAGES')
    events = []
    station_cities = defaultdict(set)
    for event in catalog['events']:
        sid = event['station'] + '|' + event['target_date']
        if sid not in days or event['source_family'] != 'NWS_WRH_TIMESERIES':
            continue
        validate_event(event)
        prefix = 'highest-temperature-in-' if event['family'] == FAMILIES[0] else 'lowest-temperature-in-'
        require(event['slug'].startswith(prefix) and '-on-' in event['slug'], 'CITY_IDENTITY')
        city = event['slug'][len(prefix):].split('-on-', 1)[0]
        station_cities[event['station']].add(city)
        require(event['source_urls'] == ['https://www.weather.gov/wrh/timeseries?site=' + event['station'].lower()],
                'SETTLEMENT_SOURCE_STATION')
        events.append(dict(event=event, split=days[sid]['split'], city_day=sid,
                           event_sha256=digest(event), label_knowable_at=None,
                           label_observed_at=catalog['created_utc'], rule_fingerprint=None,
                           historical_market_metadata_available_at=None))
    require(Counter((e['city_day'], e['event']['family']) for e in events) ==
            Counter({(sid, f): 1 for sid in days for f in FAMILIES}), 'EXACT_EVENT_COHORT')
    for key in ('event_id',):
        require(len({e['event'][key] for e in events}) == len(events), 'DUPLICATE_EVENT')
    for key in ('market_id', 'condition_id', 'yes_token', 'no_token'):
        ids = [b[key] for e in events for b in e['event']['buckets']]
        require(len(set(ids)) == len(ids), 'CROSS_EVENT_TARGET_DUPLICATE')
    require(all(len(cities) == 1 for cities in station_cities.values()) and
            len({next(iter(c)) for c in station_cities.values()}) == len(station_cities),
            'FROZEN_ONE_STATION_PER_CITY_IDENTITY')
    for panel in panels:
        panel['city'] = next(iter(station_cities[panel['context']['station']]))
    for split in SPLITS:
        subset = [d for d in panels if d['context']['split'] == split]
        require(len(subset) == plan['split_counts'][split]['station_days'], 'PLAN_SPLIT_COUNTS')
    return dict(version=VERSION, confirmation_role=CONFIRMATION, station_days=panels,
                events=sorted(events, key=lambda e: (e['city_day'], e['event']['family'])),
                feature_semantics='NATIVE_POINT_PANEL_WITH_SAMPLED_EXTREMA_DIAGNOSTICS_ONLY',
                **FLAGS)


def admission_report(dataset):
    """Fail closed: neither a point panel nor unknown receipts authorize fitting."""
    slices = {}
    for split in SPLITS:
        days = [d for d in dataset['station_days'] if d['context']['split'] == split]
        events = [e for e in dataset['events'] if e['split'] == split]
        slices[split] = dict(city_days=len(days), events=len(events),
                            market_buckets=sum(e['event']['bucket_count'] for e in events),
                            stations=len({d['context']['station'] for d in days}),
                            date_blocks=len({d['context']['target_date'] for d in days}),
                            admitted_city_days=0, effective_independent_sample_count=None)
    station = {}
    for d in dataset['station_days']:
        key = d['context']['station']
        s = station.setdefault(key, dict(city_days=0, missing_start_bracket=0, missing_end_bracket=0,
                                       native_both_boundaries=0))
        c = d['providers']['IFS']['common_six_hour_coverage']
        s['city_days'] += 1
        s['missing_start_bracket'] += not c['brackets_start']
        s['missing_end_bracket'] += not c['brackets_end']
        s['native_both_boundaries'] += c['start_native'] and c['end_native']
    blockers = ['POINT_SAMPLES_DO_NOT_IDENTIFY_EXACT_DAY_EXTREMES',
                'COMMON_SIX_HOUR_PLAN_LOSES_LOCAL_DAY_BRACKETS',
                'HISTORICAL_FEATURE_AND_MARKET_AVAILABILITY_UNKNOWN',
                'LABEL_KNOWABLE_TIMES_AND_REVISION_LINEAGE_UNAVAILABLE',
                'EXACT_SETTLEMENT_RULE_FINGERPRINT_UNAVAILABLE',
                'RAW_GRIB_BYTES_UNAVAILABLE_FOR_REDECODE',
                'ECMWF_OPERATIONAL_RELEASE_NOT_REVIEWED']
    return dict(status='BLOCKED_EXACT_DAY_AND_CAUSAL_ADMISSION', training_status='NOT_FITTED',
                calibration_status='NOT_CALIBRATED', promotion_status='NO_PROMOTION',
                confirmation_role=CONFIRMATION, blockers=blockers, split_counts=slices,
                station_coverage=station, family_counts={f: {s: slices[s]['city_days'] for s in SPLITS} for f in FAMILIES},
                comparisons={p: dict(status='NOT_EVALUATED', brier=None, log_loss=None, reliability=None,
                                      sharpness=None, admitted_city_days=0)
                             for p in ('GEFS_ONLY', 'IFS_ONLY', 'AIFS_ONLY', 'MULTI_MODEL')},
                conservative_bounds_policy='VACUOUS_0_1', current_champion_comparison='NOT_PERFORMED',
                **FLAGS)


def write_once(path, raw):
    path = Path(path)
    if path.exists():
        require(path.read_bytes() == raw, 'OUTPUT_CONFLICT:' + path.name)
        return
    with path.open('xb') as stream:
        stream.write(raw)


def run(config_path, output, code_commit):
    config = strict_json(Path(config_path).read_bytes())
    inputs = config['inputs']
    output = Path(output).resolve()
    require(output.is_relative_to(ROOT / 'private-evidence'), 'OUTPUT_MUST_BE_REPOSITORY_PRIVATE_EVIDENCE')
    # Bind executable bytes to an actual committed tree, not a caller's label.
    code_files = ('tools/v11_multimodel_panel.py', 'tools/v11_multimodel_stacking.py')
    code_hashes = {}
    for name in code_files:
        raw = subprocess.check_output(['git', 'show', f'{code_commit}:{name}'], cwd=ROOT)
        require(raw == (ROOT / name).read_bytes(), 'CODE_COMMIT_BYTES_MISMATCH')
        code_hashes[name] = hashlib.sha256(raw).hexdigest()
    commit = subprocess.check_output(['git', 'rev-parse', code_commit+'^{commit}'], cwd=ROOT, text=True).strip()
    tree = subprocess.check_output(['git', 'rev-parse', commit+'^{tree}'], cwd=ROOT, text=True).strip()
    loaded = {k: artifact(v['path'], v) for k, v in inputs.items() if not k.endswith('_db')}
    plan, catalog = loaded['plan'], loaded['catalog']
    gp, ep = loaded['gefs_progress'], loaded['ecmwf_progress']
    require(gp['state'] == ep['state'] == 'COMPLETE' and gp['messages_failed'] == 0, 'BACKFILL_NOT_COMPLETE')
    require(gp['plan_sha256'] == ep['plan_artifact_sha256'] == plan['artifact_sha256'], 'PROGRESS_PLAN_LINK')
    require(ep['plan_sha256'] == inputs['plan']['file_sha256'], 'ECMWF_PLAN_BYTES')
    require(loaded['gefs_manifest']['plan_sha256'] == plan['artifact_sha256']
            and loaded['gefs_manifest']['catalog_sha256'] == catalog['artifact_sha256'], 'PRIOR_MANIFEST_LINK')
    with immutable_db(inputs['gefs_db']['path'], inputs['gefs_db']) as gefs, immutable_db(inputs['ecmwf_db']['path'], inputs['ecmwf_db']) as ecmwf:
        require(legacy_gefs_content_sha(gefs) == loaded['gefs_manifest']['gefs_database_content_sha256'], 'PRIOR_GEFS_CONTENT_IDENTITY')
        identity = strict_json(ecmwf.execute("SELECT value FROM metadata WHERE key='identity'").fetchone()[0])
        require(all(ep.get(k) == v for k, v in identity.items()), 'ECMWF_PROGRESS_METADATA_LINK')
        require(identity['plan_artifact_sha256'] == plan['artifact_sha256'] and identity['plan_sha256'] == inputs['plan']['file_sha256'], 'ECMWF_METADATA_PLAN')
        require(identity['availability_at'] is None and identity['historical_availability'] == 'UNKNOWN'
                and identity['release_binding'] == 'OBSERVED_HEADER_ONLY'
                and identity['financial_authority'] is False and identity['promotion_authority'] is False
                and identity['operational_release_reviewed'] is False
                and identity['decoder_version'] == 'alpha_v11_ecmwf_station_grib_v3'
                and identity['worker_version'] == 'alpha_v11_ecmwf_historical_v2', 'ECMWF_RESEARCH_IDENTITY')
        require(set(identity['provider_policies']) == {'IFS', 'AIFS'} and
                all(p['temporal_resolution_hours'] == 6 and p['interpolation'] is False
                    for p in identity['provider_policies'].values()), 'COMMON_SIX_HOUR_POLICY')
        dataset = assemble(plan, catalog, gefs, ecmwf)
        require(gefs.execute('SELECT count(*) FROM messages').fetchone()[0] == gp['messages_done'] == gp['messages_total'], 'GEFS_PROGRESS_COUNTS')
        for p in ('IFS', 'AIFS'):
            n = ecmwf.execute('SELECT count(*) FROM messages WHERE provider=?', (p,)).fetchone()[0]
            require(n == ep['providers'][p]['done'] == ep['providers'][p]['total'] and
                    ep['providers'][p]['failed'] == ep['providers'][p]['pending'] == 0, 'ECMWF_PROGRESS_COUNTS')
    dataset['input_pin_sha256'] = digest(config)
    result = admission_report(dataset)
    for s in SPLITS:
        n = result['split_counts'][s]['city_days']
        require(n == gp['splits'][s]['done'] == gp['splits'][s]['total'] == ep['splits'][s]['combined_complete']
                == ep['splits'][s]['total'], 'PROGRESS_SPLIT_COUNTS')
    raw = canonical(dataset)
    blob = gzip.compress(raw, mtime=0)
    result['dataset_sha256'] = hashlib.sha256(raw).hexdigest()
    from tools.v11_multimodel_stacking import POLICY
    result['frozen_evaluation_policy'] = POLICY
    result['policy_sha256'] = digest(POLICY)
    result_raw = canonical(result) + b'\n'
    manifest = dict(version=VERSION, dataset_sha256=result['dataset_sha256'],
                    compressed_sha256=hashlib.sha256(blob).hexdigest(), result_sha256=hashlib.sha256(result_raw).hexdigest(),
                    input_pin_sha256=digest(config), inputs=inputs, code_commit=commit, code_tree=tree,
                    code_file_sha256s=code_hashes, frozen_evidence_timestamp=ep['updated_at'],
                    runtime=dict(python=platform.python_version(), sqlite=sqlite3.sqlite_version,
                                 zlib=zlib.ZLIB_RUNTIME_VERSION),
                    timezone_sha256s={z: file_sha(Path('/usr/share/zoneinfo') / z)
                                     for z in sorted({d['timezone'] for d in plan['station_days']})},
                    immutable_inputs_verified_before_and_after=True, **FLAGS)
    manifest['runtime_sha256'] = digest(dict(runtime=manifest['runtime'], timezone_sha256s=manifest['timezone_sha256s']))
    for pin in inputs.values():
        require(file_sha(pin['path']) == pin['file_sha256'], 'INPUT_CHANGED_DURING_BUILD')
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    write_once(output/'panel.json.gz', blob)
    write_once(output/'result.json', result_raw)
    write_once(output/'manifest.json', canonical(manifest) + b'\n')
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pins', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--code-commit', required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.pins, args.output, args.code_commit), sort_keys=True, indent=2))


if __name__ == '__main__':
    sys.path.insert(0, str(ROOT))
    main()
