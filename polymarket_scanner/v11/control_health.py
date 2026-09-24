"""Bounded, read-only V10 operational probe; also runnable with isolated Python.

No database connection, backup, service mutation, credential read, network request
or raw journal message output. This does not grant any V11 deployment authority.
"""
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import time


VERSION = 'alpha_v11_control_health_read_only_v1'
STATUS = Path('/var/lib/alpha-paper-demo/status.json')
DATABASE = Path('/var/lib/alpha-paper-demo/weather-paper.sqlite')
CONFIGURED_INTERVAL_SECONDS = 180.0  # Read from the pinned control launcher.
MAX_STATUS_BYTES = 262144
BOOLEANS = ('cycle_ok', 'paper_tracker_ok', 'operator_all_lanes_healthy', 'gamma_census_complete',
            'global_weather_recall_fresh', 'weather_semantic_coverage_complete', 'financial_authority',
            'automatic_order_placement', 'wallet_or_order_api_loaded')
COUNTS = ('total_active_events_scanned', 'discovered_weather_events', 'strict_supported_weather_events',
          'eligible_forecast_events_evaluated', 'forecast_events_evaluated', 'forecast_candidate_count',
          'paper_positions_created_this_cycle', 'maker_new_signals_this_cycle', 'source_shock_new_signals')


def numeric(value):
    return type(value) in (int, float) and math.isfinite(value)


def utc(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def summarize(data, *, now):
    """Whitelist operational fields; never echo unknown keys or user text."""
    if not isinstance(data, dict) or not numeric(now):
        raise ValueError('STATUS_OBJECT_AND_CLOCK_REQUIRED')
    finished = data.get('finished_at'); started = data.get('started_at')
    clock_valid = (numeric(finished) and numeric(started) and 0 <= started <= finished <= now)
    result = {k:data[k] for k in BOOLEANS if type(data.get(k)) is bool}
    result.update({k:data[k] for k in COUNTS if type(data.get(k)) is int and data[k] >= 0})
    result.update(timestamp_order_valid=clock_valid, configured_interval_seconds=CONFIGURED_INTERVAL_SECONDS,
                  finished_at_utc=utc(finished) if clock_valid else None,
                  successful_status_age_seconds=now-finished if clock_valid and data.get('cycle_ok') is True else None,
                  current_success_within_one_interval=bool(clock_valid and data.get('cycle_ok') is True
                                                          and now-finished <= CONFIGURED_INTERVAL_SECONDS),
                  current_success_within_two_intervals=bool(clock_valid and data.get('cycle_ok') is True
                                                           and now-finished <= 2*CONFIGURED_INTERVAL_SECONDS))
    if numeric(data.get('cycle_seconds')) and data['cycle_seconds'] >= 0:
        result['reported_cycle_seconds'] = data['cycle_seconds']
    if isinstance(data.get('errors'), list):
        result['reported_error_count'] = len(data['errors'])
    if isinstance(data.get('release_sha'), str) and re.fullmatch('[0-9a-f]{40}', data['release_sha']):
        result['release_marker'] = data['release_sha']
    result['single_status_record_is_not_proof_of_continuous_cycles'] = True
    result['raw_messages_and_financial_aggregates_excluded'] = True
    return result


def read_status(path, *, now):
    try:
        # No-follow and descriptor checks avoid mixing an atomic status replacement
        # or an in-place writer with a false stable-file claim.
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, 'rb') as stream:
            before = os.fstat(stream.fileno())
            if before.st_size > MAX_STATUS_BYTES:
                return {'readable':True, 'error':'STATUS_SIZE_BOUND'}
            raw = stream.read(MAX_STATUS_BYTES+1)
            after = os.fstat(stream.fileno())
        current = Path(path).stat()
        fields = lambda s:(s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
        if len(raw) > MAX_STATUS_BYTES or fields(before) != fields(after) or fields(after) != fields(current):
            return {'readable':True, 'error':'STATUS_CHANGED_DURING_READ_RECHECK_SEPARATELY'}
        data = json.loads(raw)
        return {'readable':True, 'bytes':len(raw), 'sha256':hashlib.sha256(raw).hexdigest(),
                'mtime_utc':utc(after.st_mtime), 'summary':summarize(data, now=now)}
    except (OSError, ValueError, OverflowError) as exc:
        return {'readable':False, 'error':type(exc).__name__}


def file_metadata(path):
    try:
        s = Path(path).stat()
        return dict(bytes=s.st_size, mtime_utc=utc(s.st_mtime), inode=s.st_ino)
    except OSError as exc:
        return dict(error=type(exc).__name__)


def journal_metadata():
    # Output omits MESSAGE before the subprocess returns: private log text never
    # enters this report. Journal recency is liveness, not proof of cycle success.
    command = ['/usr/bin/journalctl', '--unit=alpha-paper-demo.service', '--since=-2h', '--lines=20',
               '--no-pager', '--output=json', '--output-fields=__REALTIME_TIMESTAMP,PRIORITY']
    try:
        p = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=5)
        rows = []
        for line in p.stdout.splitlines()[:20]:
            value = json.loads(line)
            stamp = value.get('__REALTIME_TIMESTAMP'); priority = value.get('PRIORITY')
            if isinstance(stamp, str) and re.fullmatch('[0-9]{1,20}', stamp):
                rows.append(dict(realtime_us=stamp, priority=priority if priority in tuple(map(str,range(8))) else None))
        return dict(returncode=p.returncode, records=rows, raw_messages_read=False,
                    permission_denied='permission' in p.stderr.lower(), query_window_seconds=7200)
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        return dict(error=type(exc).__name__, raw_messages_read=False)


def main():
    now = time.time()
    report = dict(version=VERSION, checked_at=utc(now), effective_uid=os.geteuid(), read_only=True,
          status=read_status(STATUS, now=now), files={str(p):file_metadata(p) for p in
                 (STATUS, DATABASE, Path(str(DATABASE)+'-wal'), Path(str(DATABASE)+'-shm'))},
          journal=journal_metadata(), database_opened=False, existing_snapshot_changed=False,
          service_or_permissions_changed=False, v11_deployment_authorized=False, financial_authority=False)
    print(json.dumps(report, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
