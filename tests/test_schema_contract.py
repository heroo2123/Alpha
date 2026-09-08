from pathlib import Path

import pytest

from polymarket_scanner.manual_fills import ensure_structural_fill_schema
from polymarket_scanner.outbox import TelegramOutbox
from polymarket_scanner.schema_contract import (
    DATABASE_SCHEMA_VERSION,
    attest_database_schema,
    require_database_schema,
)
from polymarket_scanner.store import Store


def test_fully_initialized_production_database_satisfies_schema_contract(tmp_path):
    db = str(tmp_path / "signals.db")
    store = Store(db)
    TelegramOutbox(db)
    ensure_structural_fill_schema(store)

    attestation = attest_database_schema(db)
    assert attestation["version"] == DATABASE_SCHEMA_VERSION
    assert attestation["exists"] is True
    assert attestation["compatible"] is True
    assert attestation["missing_tables"] == []
    assert attestation["missing_columns"] == {}
    assert require_database_schema(db) == attestation


def test_partially_initialized_database_fails_closed_with_missing_tables(tmp_path):
    db = str(tmp_path / "signals.db")
    Store(db)

    attestation = attest_database_schema(db)
    assert attestation["exists"] is True
    assert attestation["compatible"] is False
    assert "telegram_outbox" in attestation["missing_tables"]
    assert "manual_structural_trades" in attestation["missing_tables"]
    assert "manual_structural_legs" in attestation["missing_tables"]

    with pytest.raises(RuntimeError, match="production database schema incompatible") as exc:
        require_database_schema(db)
    assert "telegram_outbox" in str(exc.value)
    assert "manual_structural_trades" in str(exc.value)


def test_missing_database_is_explicitly_unattested_and_required_gate_raises(tmp_path):
    missing = tmp_path / "missing.db"
    attestation = attest_database_schema(missing)
    assert attestation["exists"] is False
    assert attestation["compatible"] is False
    assert "missing" in attestation["reason"]

    with pytest.raises(RuntimeError, match=DATABASE_SCHEMA_VERSION):
        require_database_schema(missing)


def test_trade_only_startup_enforces_schema_before_runtime_authority_is_published():
    source = Path("app_trade_only.py").read_text(encoding="utf-8")
    guard = "db_schema = await asyncio.to_thread(require_database_schema, base.settings.db_path)"
    manifest = "runtime_manifest = await asyncio.to_thread("
    assert guard in source
    assert manifest in source
    assert source.index(guard) < source.index(manifest)
    assert 'runtime_manifest["database_schema"] = db_schema' in source
    assert 'base.state["database_schema"] = db_schema' in source
    assert 'base.state["production_runtime_authority_complete"]' in source
