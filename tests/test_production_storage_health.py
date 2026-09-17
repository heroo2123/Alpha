"""Capacity failures close new openings without deleting financial state."""
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from polymarket_scanner.production import storage_health as storage
from test_production_preflight_completion import ready


def fs(available=30, **kwargs):
    values = dict(f_blocks=100, f_bavail=available, f_frsize=4096,
                  f_files=100, f_favail=50)
    values.update(kwargs)
    return SimpleNamespace(**values)


@pytest.mark.parametrize("available,allowed,warning", [
    (31, True, False), (30, True, True), (16, True, True),
    (15, False, True), (0, False, True)])
def test_exact_storage_thresholds(monkeypatch, available, allowed, warning):
    monkeypatch.setattr(storage.os, "statvfs", lambda p: fs(available))
    result = storage.check_storage({"execution": Path("/unused")})
    assert result["openings_allowed"] is allowed
    assert result["filesystems"][0]["warning"] is warning
    assert result["filesystems"][0]["available_bytes"] == available * 4096


@pytest.mark.parametrize("value,reason", [
    (fs(-1), "STORAGE_HEALTH_UNAVAILABLE"),
    (fs(f_blocks=0), "STORAGE_HEALTH_UNAVAILABLE"),
    (fs(f_frsize=0), "STORAGE_HEALTH_UNAVAILABLE"),
    (fs(f_favail=0), "STORAGE_INODES_EXHAUSTED")])
def test_invalid_or_exhausted_storage(monkeypatch, value, reason):
    monkeypatch.setattr(storage.os, "statvfs", lambda p: value)
    assert storage.check_storage({"execution": Path("/")})["reason"] == reason


def test_filesystem_read_error_is_fail_closed(monkeypatch):
    def failed(path):
        raise PermissionError("do not echo filesystem details")
    monkeypatch.setattr(storage.os, "statvfs", failed)
    assert storage.check_storage({"execution": Path("/")})["reason"] == "STORAGE_HEALTH_UNAVAILABLE"


def test_disk_pressure_closes_authority_without_sticky_financial_fault(tmp_path, monkeypatch):
    cfg, ledger, ex, engine = ready(tmp_path)
    monkeypatch.setattr(storage.os, "statvfs", lambda p: fs(15))
    assert engine.base_authority_reason() == "STORAGE_CAPACITY_OPENING_STOP"
    assert not engine.authority()
    assert ledger.state("fault") is None and ex.posts == []
    assert ledger.summary()["confirmed_fill_count"] == 0


def test_missing_configured_filesystem_remains_fail_closed(tmp_path, monkeypatch):
    from polymarket_scanner.production.config import ConfigurationError
    cfg, ledger, ex, engine = ready(tmp_path)
    def missing(path):
        if path == cfg.signal_db.parent:
            raise FileNotFoundError("unprovisioned state directory")
        return fs(30)
    monkeypatch.setattr(storage.os, "statvfs", missing)
    with pytest.raises(ConfigurationError, match="STORAGE_HEALTH_UNAVAILABLE"):
        asyncio.run(engine.validate_preflight_completion(allow_unfunded=True))
    assert ex.posts == [] and ex.cancels == []
    assert not engine.account_reconciled
