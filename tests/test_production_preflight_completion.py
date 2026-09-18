"""Preflight acceptance must remain valid at the end, not just its first read."""
import asyncio
from dataclasses import replace
import time

import pytest

from polymarket_scanner.production.config import ConfigurationError
from test_production_deposit_session_engine import make_engine


def ready(tmp_path, **kw):
    cfg, sig, store, ledger, exchange, engine = make_engine(tmp_path, **kw)
    cfg.activation_file.chmod(0o600)
    asyncio.run(engine.reconcile(allow_exchange_mutation=False))
    return cfg, ledger, exchange, engine


def test_readonly_completion_accepts_unfunded_without_activation(tmp_path):
    cfg, ledger, ex, engine = ready(tmp_path)
    cfg.activation_file.unlink()
    engine.funding_ready = engine.reconciled = False
    asyncio.run(engine.validate_preflight_completion(allow_unfunded=True))
    assert engine.account_reconciled and not engine.authority()
    assert ex.posts == [] and ex.cancels == []
    with pytest.raises(ConfigurationError, match="PREFLIGHT_RECONCILIATION_INCOMPLETE"):
        asyncio.run(engine.validate_preflight_completion())


@pytest.mark.parametrize("field", ["session_valid_until", "session_exclusive_until"])
def test_completion_rechecks_window_after_last_network_read(tmp_path, monkeypatch, field):
    cfg, ledger, ex, engine = ready(tmp_path)
    now = time.time()
    engine.config = replace(cfg, **{field: now + 301})
    clock = [now]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    def late_eligibility(**kwargs):
        clock[0] += 2
        return {"openings_allowed": True}
    ex.eligibility = late_eligibility
    with pytest.raises(ConfigurationError, match="PREFLIGHT_SESSION_WINDOW_CLOSED"):
        asyncio.run(engine.validate_preflight_completion(allow_unfunded=True))
    assert not engine.reconciled and not engine.account_reconciled
    assert ex.posts == [] and ex.cancels == []


@pytest.mark.parametrize("delta", [30, 31, -1])
def test_completion_rejects_aged_or_future_reconciliation(tmp_path, delta):
    cfg, ledger, ex, engine = ready(tmp_path)
    engine.last_reconcile = time.time() - delta
    with pytest.raises(ConfigurationError, match="PREFLIGHT_EVIDENCE_STALE"):
        asyncio.run(engine.validate_preflight_completion(allow_unfunded=True))
    assert not engine.authority() and ex.posts == []


@pytest.mark.parametrize("result", [None, {}, {"openings_allowed": False}])
def test_completion_rejects_current_ineligibility(tmp_path, result):
    cfg, ledger, ex, engine = ready(tmp_path)
    ex.eligibility = lambda **kwargs: result
    with pytest.raises(ConfigurationError, match="PREFLIGHT_ACCOUNT_OPENINGS_RESTRICTED"):
        asyncio.run(engine.validate_preflight_completion(allow_unfunded=True))
    assert ex.posts == [] and not engine.account_reconciled


def test_completion_rpc_failure_is_sanitized_and_not_sticky_account_loss(tmp_path):
    cfg, ledger, ex, engine = ready(tmp_path)
    def failure(**kwargs):
        raise RuntimeError("transport detail must not escape into status")
    ex.eligibility = failure
    with pytest.raises(ConfigurationError, match="PREFLIGHT_COMPLETION_FAILED_CLOSED"):
        asyncio.run(engine.validate_preflight_completion(allow_unfunded=True))
    assert engine.last_error == "VALIDATION_FAILED"
    assert ledger.state("fault") is None and ex.posts == []


def test_completion_changed_configuration_rejected(tmp_path):
    cfg, ledger, ex, engine = ready(tmp_path)
    source = tmp_path / "changed.json"
    source.write_text("{}")
    engine.config = replace(cfg, source_path=source)
    with pytest.raises(ConfigurationError, match="PREFLIGHT_CONFIGURATION_CHANGED"):
        asyncio.run(engine.validate_preflight_completion(allow_unfunded=True))


def test_wall_clock_cannot_hide_old_monotonic_reconciliation(tmp_path):
    cfg, ledger, ex, engine = ready(tmp_path)
    engine.last_reconcile = time.time()
    engine.last_reconcile_monotonic = time.monotonic() - 31
    with pytest.raises(ConfigurationError, match="PREFLIGHT_EVIDENCE_STALE"):
        asyncio.run(engine.validate_preflight_completion(allow_unfunded=True))
    assert not engine.account_reconciled and ex.posts == []


def test_unfunded_preflight_does_not_waive_disk_pressure(tmp_path, monkeypatch):
    from polymarket_scanner.production import storage_health
    from test_production_storage_health import fs
    cfg, ledger, ex, engine = ready(tmp_path)
    monkeypatch.setattr(storage_health.os, "statvfs", lambda p: fs(15))
    with pytest.raises(ConfigurationError, match="STORAGE_CAPACITY_OPENING_STOP"):
        asyncio.run(engine.validate_preflight_completion(allow_unfunded=True))
    assert not engine.account_reconciled and ex.posts == [] and ex.cancels == []
