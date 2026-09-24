import json
import os

from polymarket_scanner.v11 import control_health as health


def test_probe_redacts_messages_secrets_financial_data_and_unknown_fields():
    d = health.summarize(dict(started_at=90., finished_at=100., cycle_ok=True, errors=['private error'],
                  wallet_private_key='secret', balance='private', arbitrary='secret', financial_authority=False), now=110.)
    assert d['current_success_within_one_interval'] and d['reported_error_count'] == 1
    assert 'secret' not in json.dumps(d) and 'private' not in json.dumps(d)
    assert not d['financial_authority']


def test_stale_true_success_flag_does_not_claim_fresh_cycles():
    d = health.summarize(dict(started_at=90, finished_at=100, cycle_ok=True), now=1000)
    assert not d['current_success_within_one_interval'] and not d['current_success_within_two_intervals']
    assert d['successful_status_age_seconds'] == 900


def test_failed_or_future_cycle_cannot_be_current_success():
    for finished, ok in [(120,True), (100,False), (True,True), (float('nan'),True)]:
        d = health.summarize(dict(started_at=90, finished_at=finished, cycle_ok=ok), now=110)
        assert d['successful_status_age_seconds'] is None and not d['current_success_within_two_intervals']


def test_status_read_is_bounded_and_preserves_source_bytes(tmp_path):
    p = tmp_path/'status.json'; raw = b'{"started_at":90,"finished_at":100,"cycle_ok":true}'
    p.write_bytes(raw)
    d = health.read_status(p, now=110)
    assert d['readable'] and d['summary']['current_success_within_one_interval'] and p.read_bytes() == raw
    p.write_bytes(b' '* (health.MAX_STATUS_BYTES+1))
    assert health.read_status(p, now=110)['error'] == 'STATUS_SIZE_BOUND'


def test_missing_or_symlink_status_is_not_assumed_healthy(tmp_path):
    p = tmp_path/'status.json'
    assert not health.read_status(p, now=110)['readable']
    other = tmp_path/'other'; other.write_text('{}'); p.symlink_to(other)
    assert not health.read_status(p, now=110)['readable']


def test_in_place_status_race_is_not_reinterpreted_as_consistent(tmp_path, monkeypatch):
    p = tmp_path/'status.json'; p.write_text('{"started_at":90,"finished_at":100,"cycle_ok":true}')
    original = os.fstat; calls = [0]
    def race(fd):
        calls[0] += 1
        if calls[0] == 2:
            p.write_text('{}')
        return original(fd)
    monkeypatch.setattr(health.os, 'fstat', race)
    assert health.read_status(p, now=110)['error'] == 'STATUS_CHANGED_DURING_READ_RECHECK_SEPARATELY'


def test_journal_probe_requests_only_metadata_and_reports_permission_error(monkeypatch):
    from types import SimpleNamespace
    def run(cmd, **kwargs):
        assert '--output-fields=__REALTIME_TIMESTAMP,PRIORITY' in cmd
        assert '--lines=20' in cmd and kwargs['timeout'] == 5
        return SimpleNamespace(returncode=1, stdout='', stderr='insufficient permissions')
    monkeypatch.setattr(health.subprocess, 'run', run)
    d = health.journal_metadata()
    assert d['permission_denied'] and not d['raw_messages_read'] and d['records'] == []
