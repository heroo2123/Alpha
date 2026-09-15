from __future__ import annotations

import json

import pytest

from polymarket_scanner import weather_only_maker_live_semantics as live


COND_A = "0x" + "a" * 64
COND_B = "0x" + "b" * 64
TX_A = "0x" + "1" * 64


def _recent_row(index: int, *, side: str = "BUY", condition: str = COND_A):
    return {
        "asset": str(10_000 + index),
        "conditionId": condition,
        "side": side,
        "transactionHash": "0x" + f"{index:064x}",
        "timestamp": 1_800_000_000 + index,
    }


def _observed(index: int, *, side: str = "SELL"):
    return {
        "transaction_hash": "0x" + f"{index:064x}",
        "token_id": str(20_000 + index),
        "condition_id": COND_A,
        "side": side,
        "price": 0.25,
        "size": 2.0,
        "executed_at": 1_800_000_000.0 + index,
        "received_at": 1_800_000_000.2 + index,
        "trade_id": f"ws:{index}",
    }


def test_recent_target_selection_uses_valid_unique_public_taker_rows_only():
    rows = [
        {"asset": "", "conditionId": COND_A, "side": "BUY"},
        {"asset": "123", "conditionId": "bad", "side": "BUY"},
        {"asset": "124", "conditionId": COND_A, "side": "UNKNOWN"},
        _recent_row(1, side="sell", condition=COND_A),
        _recent_row(2, side="BUY", condition=COND_B),
        _recent_row(1, side="SELL", condition=COND_A),
    ]
    selected = live.select_recent_trade_targets(rows, max_tokens=2)
    assert selected == {
        "10001": COND_A,
        "10002": COND_B,
    }


def test_recent_target_selection_fails_on_same_token_condition_conflict():
    rows = [
        _recent_row(1, condition=COND_A),
        {**_recent_row(1, condition=COND_B), "asset": "10001"},
    ]
    with pytest.raises(live.MakerLiveSemanticsError) as exc:
        live.select_recent_trade_targets(rows, max_tokens=8)
    assert exc.value.code == "LIVE_SEMANTICS_TOKEN_CONDITION_CONFLICT"


def test_correlation_requires_exact_transaction_token_and_condition_identity():
    observed = {
        "transaction_hash": TX_A,
        "token_id": "123",
        "condition_id": COND_A,
        "side": "SELL",
    }
    rows = [
        {
            "transactionHash": TX_A,
            "asset": "123",
            "conditionId": COND_A,
            "side": "SELL",
        },
        {
            "transactionHash": TX_A,
            "asset": "different",
            "conditionId": COND_A,
            "side": "BUY",
        },
        {
            "transactionHash": TX_A,
            "asset": "123",
            "conditionId": COND_B,
            "side": "BUY",
        },
    ]
    assert live.correlation_sides(observed, rows) == {"SELL"}


def test_correlation_surfaces_conflicting_taker_sides_instead_of_picking_one():
    observed = {
        "transaction_hash": TX_A,
        "token_id": "123",
        "condition_id": COND_A,
        "side": "SELL",
    }
    rows = [
        {
            "transactionHash": TX_A,
            "asset": "123",
            "conditionId": COND_A,
            "side": "SELL",
        },
        {
            "transactionHash": TX_A,
            "asset": "123",
            "conditionId": COND_A,
            "side": "BUY",
        },
    ]
    assert live.correlation_sides(observed, rows) == {"BUY", "SELL"}


def test_failed_live_gate_still_writes_diagnostic_report(monkeypatch, tmp_path):
    recent = [_recent_row(index) for index in range(8)]
    monkeypatch.setattr(live, "_json_get", lambda *args, **kwargs: recent)

    async def fake_observe(*args, **kwargs):
        row = _observed(1)
        return {row["token_id"]}, [row], 7, True

    monkeypatch.setattr(live, "_observe_ws_trades", fake_observe)
    monkeypatch.setattr(live, "_correlate", lambda *args, **kwargs: ({
        (_observed(1)["transaction_hash"], _observed(1)["token_id"]): {
            "side": "SELL",
            "condition_id": COND_A,
        }
    }, []))

    report_path = tmp_path / "report.json"
    rc = live.run_live_certification(
        report_path=report_path,
        observe_seconds=1.0,
        correlate_seconds=1.0,
        target_ws_trades=3,
        min_correlated_trades=3,
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert rc == 2
    assert report["certified"] is False
    assert report["failure_code"] == "LIVE_SEMANTICS_INSUFFICIENT_CORRELATED_TRADES"
    assert report["production_parsed_unique_trade_frames"] == 1
    assert report["data_api_taker_only_correlations"] == 1
    assert report["financial_authority"] is False
    assert report["automatic_order_placement"] is False


def test_live_gate_certifies_only_after_required_correlations(monkeypatch, tmp_path):
    recent = [_recent_row(index) for index in range(8)]
    monkeypatch.setattr(live, "_json_get", lambda *args, **kwargs: recent)
    observed = [_observed(index, side="SELL" if index % 2 else "BUY") for index in (1, 2, 3)]

    async def fake_observe(*args, **kwargs):
        return {row["token_id"] for row in observed}, observed, 20, True

    correlated = {
        (row["transaction_hash"], row["token_id"]): {
            "side": row["side"],
            "condition_id": row["condition_id"],
        }
        for row in observed
    }
    monkeypatch.setattr(live, "_observe_ws_trades", fake_observe)
    monkeypatch.setattr(live, "_correlate", lambda *args, **kwargs: (correlated, []))

    report_path = tmp_path / "report.json"
    rc = live.run_live_certification(
        report_path=report_path,
        observe_seconds=1.0,
        correlate_seconds=1.0,
        target_ws_trades=3,
        min_correlated_trades=3,
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert rc == 0
    assert report["certified"] is True
    assert report["failure_code"] is None
    assert report["data_api_taker_only_correlations"] == 3
    assert report["side_semantics"] == "WS_SIDE_MATCHES_DATA_API_TAKER_SIDE"
    assert report["authenticated"] is False
    assert report["actual_fill_authority"] is False
