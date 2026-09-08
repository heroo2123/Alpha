from polymarket_scanner.manual_fills import ensure_structural_fill_schema
from polymarket_scanner.outbox import TelegramOutbox
from polymarket_scanner.schema_contract import (
    DATABASE_SCHEMA_VERSION,
    attest_database_schema,
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


def test_partially_initialized_database_fails_closed_with_missing_tables(tmp_path):
    db = str(tmp_path / "signals.db")
    Store(db)

    attestation = attest_database_schema(db)
    assert attestation["exists"] is True
    assert attestation["compatible"] is False
    assert "telegram_outbox" in attestation["missing_tables"]
    assert "manual_structural_trades" in attestation["missing_tables"]
    assert "manual_structural_legs" in attestation["missing_tables"]


def test_missing_database_is_explicitly_unattested(tmp_path):
    attestation = attest_database_schema(tmp_path / "missing.db")
    assert attestation["exists"] is False
    assert attestation["compatible"] is False
    assert "missing" in attestation["reason"]
