#!/usr/bin/env python3
"""Read-only observed per-provider message-size evidence for R09 Gate 3 feasibility.

`tools.v11_r09_gate3_collector.estimate_feasibility` requires REAL OBSERVED
per-provider message sizes, not nominal figures. This tool derives them from the
already-retrieved historical backfill stores (public anonymous byte-range
retrievals recorded with their byte lengths) and compares every observed size
against the collector's pinned ceilings. It performs no network access, opens
every store read-only, and writes only the JSON summary. Historical retrieval is
research evidence for sizing; it is not availability, release or launch proof.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.v11_r09_gate3_collector import (  # noqa: E402
    _EXISTING_MAX_FIELD_BYTES, _EXISTING_MAX_INDEX_BYTES, _GEFS_S3_FULL_FIELD_MAX_BYTES,
    VALID_PROVIDERS)

VERSION = 'alpha_v11_r09_gate3_observed_message_sizes_v1'
# Declared by the historical workers, recorded here only as the path the sizes describe.
ACQUISITION_PATHS = {
    'GEFS': 'noaa-gefs-pds S3 pgrb2ap5 (0p50) .idx sidecar + single TMP:2 m byte range',
    'IFS': 'ecmwf-forecasts S3 0p25 oper/enfo .index sidecar + single 2t byte range',
    'AIFS': 'ecmwf-forecasts S3 0p25 aifs-ens .index sidecar + single 2t byte range',
}
CEILINGS = {
    'index': {p: _EXISTING_MAX_INDEX_BYTES for p in VALID_PROVIDERS},
    'field': {p: (_GEFS_S3_FULL_FIELD_MAX_BYTES if p == 'GEFS' else _EXISTING_MAX_FIELD_BYTES)
              for p in VALID_PROVIDERS},
}


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _readonly(path):
    return sqlite3.connect(f'file:{Path(path).resolve()}?mode=ro', uri=True)


def _stats(values, ceiling):
    invalid = sum(type(v) is not int or v <= 0 for v in values)
    values = sorted(v for v in values if type(v) is int and v > 0)
    if not values:
        result = {'count': 0, 'ceiling_bytes': ceiling, 'exceeding_ceiling': 0}
        if invalid:
            result['invalid_observations'] = invalid
        return result
    pick = lambda q: values[min(len(values) - 1, int(len(values) * q))]  # noqa: E731
    result = {'count': len(values), 'min': values[0], 'p50': pick(0.5), 'p95': pick(0.95),
            'p99': pick(0.99), 'max': values[-1], 'ceiling_bytes': ceiling,
            'exceeding_ceiling': sum(v > ceiling for v in values)}
    if invalid:
        result['invalid_observations'] = invalid
    return result


def observe(ecmwf_sqlite, gefs_sqlite):
    providers = {}
    with _readonly(ecmwf_sqlite) as db:
        for provider in ('IFS', 'AIFS'):
            rows = db.execute(
                "SELECT byte_length, index_byte_length FROM messages "
                "WHERE provider=? AND status='DONE'", (provider,)).fetchall()
            providers[provider] = {
                'field_bytes': _stats([r[0] for r in rows if r[0] is not None], CEILINGS['field'][provider]),
                'index_bytes': _stats([r[1] for r in rows if r[1] is not None], CEILINGS['index'][provider]),
                'done_messages': len(rows)}
    with _readonly(gefs_sqlite) as db:
        rows = db.execute("SELECT bytes FROM messages WHERE status='DONE'").fetchall()
        providers['GEFS'] = {
            'field_bytes': _stats([r[0] for r in rows if r[0] is not None], CEILINGS['field']['GEFS']),
            'index_bytes': {'count': 0, 'ceiling_bytes': CEILINGS['index']['GEFS'],
                            'exceeding_ceiling': 0, 'note': 'store records .idx sha256 only, not its length'},
            'done_messages': len(rows)}
    for provider, entry in providers.items():
        entry['acquisition_path'] = ACQUISITION_PATHS[provider]
    return providers


def build(ecmwf_sqlite, gefs_sqlite):
    providers = observe(ecmwf_sqlite, gefs_sqlite)
    # A feasibility estimate must use the observed maximum, never the mean, so an
    # under-sized ceiling can never be hidden by averaging.
    estimate = {p: providers[p]['field_bytes'].get('max') for p in VALID_PROVIDERS}
    findings = [
        f"{p}: {providers[p]['field_bytes']['exceeding_ceiling']} of "
        f"{providers[p]['field_bytes']['count']} observed field messages exceed the "
        f"pinned {providers[p]['field_bytes']['ceiling_bytes']}-byte ceiling"
        for p in VALID_PROVIDERS if providers[p]['field_bytes']['exceeding_ceiling']]
    findings.extend(
        f"{p}: no usable positive integer observed field message sizes"
        for p in VALID_PROVIDERS if estimate[p] is None)
    findings.extend(
        f"{p}: {providers[p]['field_bytes']['invalid_observations']} invalid field byte observations"
        for p in VALID_PROVIDERS if providers[p]['field_bytes'].get('invalid_observations'))
    findings.sort()
    return {
        'version': VERSION,
        'evidence_class': 'HISTORICAL_PUBLIC_RETRIEVAL_SIZE_ONLY',
        'network_access': False,
        'financial_authority': False, 'promotion_authority': False, 'launch_authority': False,
        'sources': {
            'ecmwf_sqlite': {'path': str(ecmwf_sqlite), 'sha256': _sha256(ecmwf_sqlite)},
            'gefs_sqlite': {'path': str(gefs_sqlite), 'sha256': _sha256(gefs_sqlite)}},
        'providers': providers,
        'provider_message_size_estimate_bytes': estimate,
        'ceiling_findings': findings,
        'feasibility_input_usable': not findings,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--ecmwf-sqlite', required=True)
    parser.add_argument('--gefs-sqlite', required=True)
    parser.add_argument('--out', required=True)
    args = parser.parse_args(argv)
    report = build(Path(args.ecmwf_sqlite), Path(args.gefs_sqlite))
    Path(args.out).write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
    print(json.dumps({'out': args.out, 'estimate': report['provider_message_size_estimate_bytes'],
                      'ceiling_findings': report['ceiling_findings']}, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
