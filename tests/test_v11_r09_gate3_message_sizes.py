import json
import sqlite3

from tools import v11_r09_gate3_message_sizes as sizes
from tools.v11_r09_gate3_collector import (
    _EXISTING_MAX_FIELD_BYTES, _GEFS_S3_FULL_FIELD_MAX_BYTES,
    VALID_PROVIDERS)


def _stores(tmp_path, gefs_bytes, ifs_bytes):
    ecmwf = tmp_path / 'ecmwf.sqlite'
    with sqlite3.connect(ecmwf) as db:
        db.execute("CREATE TABLE messages(message_key TEXT PRIMARY KEY, provider TEXT, status TEXT, "
                   "byte_length INTEGER, index_byte_length INTEGER)")
        for i, size in enumerate(ifs_bytes):
            db.execute("INSERT INTO messages VALUES(?,?,?,?,?)", (f'IFS|{i}', 'IFS', 'DONE', size, 40000))
        db.execute("INSERT INTO messages VALUES('AIFS|0','AIFS','DONE',600000,1400000)")
        db.execute("INSERT INTO messages VALUES('IFS|pending','IFS','PENDING',NULL,NULL)")
    gefs = tmp_path / 'gefs.sqlite'
    with sqlite3.connect(gefs) as db:
        db.execute("CREATE TABLE messages(message_key TEXT PRIMARY KEY, status TEXT, bytes INTEGER)")
        for i, size in enumerate(gefs_bytes):
            db.execute("INSERT INTO messages VALUES(?,?,?)", (f'g{i}', 'DONE', size))
        db.execute("INSERT INTO messages VALUES('gf','FAILED',NULL)")
    return ecmwf, gefs


def test_observed_maximum_is_the_estimate_and_pending_rows_are_ignored(tmp_path):
    ecmwf, gefs = _stores(tmp_path, [1000, 2000, 60000], [650000, 660000])
    report = sizes.build(ecmwf, gefs)
    assert set(report['provider_message_size_estimate_bytes']) == set(VALID_PROVIDERS)
    assert report['provider_message_size_estimate_bytes'] == {'GEFS': 60000, 'IFS': 660000, 'AIFS': 600000}
    assert report['providers']['IFS']['done_messages'] == 2
    assert report['providers']['GEFS']['field_bytes']['count'] == 3
    assert report['ceiling_findings'] == [] and report['feasibility_input_usable'] is True
    assert report['network_access'] is False and report['financial_authority'] is False


def test_sizes_over_pinned_ceiling_are_reported_not_hidden(tmp_path):
    ecmwf, gefs = _stores(tmp_path, [_GEFS_S3_FULL_FIELD_MAX_BYTES, _GEFS_S3_FULL_FIELD_MAX_BYTES + 1],
                          [_EXISTING_MAX_FIELD_BYTES + 1])
    report = sizes.build(ecmwf, gefs)
    assert report['providers']['GEFS']['field_bytes']['exceeding_ceiling'] == 1
    assert report['providers']['IFS']['field_bytes']['exceeding_ceiling'] == 1
    assert len(report['ceiling_findings']) == 2 and report['feasibility_input_usable'] is False


def test_cli_writes_deterministic_json_and_hashes_sources(tmp_path):
    ecmwf, gefs = _stores(tmp_path, [1000], [650000])
    out = tmp_path / 'out.json'
    assert sizes.main(['--ecmwf-sqlite', str(ecmwf), '--gefs-sqlite', str(gefs), '--out', str(out)]) == 0
    first = out.read_text()
    sizes.main(['--ecmwf-sqlite', str(ecmwf), '--gefs-sqlite', str(gefs), '--out', str(out)])
    assert out.read_text() == first
    report = json.loads(first)
    assert len(report['sources']['ecmwf_sqlite']['sha256']) == 64
    assert report['sources']['ecmwf_sqlite']['sha256'] == sizes._sha256(ecmwf)
