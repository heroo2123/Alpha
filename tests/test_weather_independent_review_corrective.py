from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path

import pytest

from polymarket_scanner.weather_only_independent_review_corrective import (
    IndependentReviewAllPaperCommandController,
    IndependentReviewMakerAccountingStore,
    IndependentReviewPostReceiptStore,
)
from polymarket_scanner.weather_only_live_paper_all_signals_final import (
    FinalAllPaperSafetyInvariantError,
    FinalAllPaperWeatherLiveService,
)
from polymarket_scanner.weather_only_live_paper_all_signals_v8 import (
    AllPaperWeatherLiveV8Service,
)
from polymarket_scanner.weather_only_maker_shadow import RESTING, VirtualMakerOrder
from polymarket_scanner.weather_only_maker_store import WeatherMakerStoreError
from polymarket_scanner.weather_only_paper_commands_all import AllPaperCommandController
from polymarket_scanner.weather_only_paper_positions import WeatherPaperPositionError
from polymarket_scanner.weather_only_paper_post_receipt import PAPER_EXECUTION_PROTOCOL_V5


NOW = 1_800_000_000.0


def _signal(store: IndependentReviewPostReceiptStore, name: str, *, status: str = "POST_RECEIPT_RECHECK") -> int:
    sid = store.save_signal(
        fingerprint=f"fp-{name}",
        lane="weather_same_day_friend_lock",
        evidence_class="TEST",
        event_id=f"event-{name}",
        market_id=f"market-{name}",
        side="YES",
        token_id=f"token-{name}",
        model_probability=0.99,
        entry_cost=0.91,
        raw_gap=0.08,
        theoretical_payout=1.0,
        created_at=NOW - 1.0,
        payload={
            "paper_execution_protocol_version": PAPER_EXECUTION_PROTOCOL_V5,
            "decision_id": f"decision-{name}",
            "decision_expires_at": NOW + 30.0,
            "event_title": name,
            "financial_authority": False,
            "automatic_order_placement": False,
        },
    )
    assert sid is not None
    store.set_signal_status(sid, "PENDING_DELIVERY")
    store.mark_telegram_sent(sid, 1000 + sid, sent_at=NOW)
    store.set_signal_status(sid, status)
    return sid


def _execution(name: str) -> dict:
    return {
        "paper_execution_protocol_version": PAPER_EXECUTION_PROTOCOL_V5,
        "decision_id": f"decision-{name}",
        "decision_expires_at": NOW + 30.0,
        "post_receipt_recheck_started_at": NOW + 1.0,
        "post_receipt_recheck_finished_at": NOW + 2.0,
        "entry_cost_per_unit": 0.91,
        "visible_units": 20.0,
        "minimum_order_size": 1.0,
        "theoretical_payout_per_unit": 1.0,
        "legs": [
            {
                "market_id": f"market-{name}",
                "condition_id": f"condition-{name}",
                "token_id": f"token-{name}",
                "side": "YES",
                "ask": 0.90,
                "fee": 0.01,
                "quote_observed_at": NOW + 1.9,
                "minimum_order_size": 1.0,
                "book_hash": f"hash-{name}",
            }
        ],
    }


def test_v5_recomputes_entry_cost_from_exact_legs(tmp_path):
    store = IndependentReviewPostReceiptStore(tmp_path / "paper.sqlite")
    sid = _signal(store, "cost")
    value = _execution("cost")
    value["entry_cost_per_unit"] = 0.70
    with pytest.raises(WeatherPaperPositionError) as exc:
        store.admit_post_receipt_position(sid, 5.0, value)
    assert exc.value.code == "V5_ENTRY_COST_LEG_SUM_MISMATCH"


def test_v5_rejects_quote_outside_claimed_post_receipt_snapshot(tmp_path):
    store = IndependentReviewPostReceiptStore(tmp_path / "paper.sqlite")
    sid = _signal(store, "quote")
    value = _execution("quote")
    value["legs"][0]["quote_observed_at"] = NOW + 0.5
    with pytest.raises(WeatherPaperPositionError) as exc:
        store.admit_post_receipt_position(sid, 5.0, value)
    assert exc.value.code == "V5_EXECUTION_QUOTE_OUTSIDE_RECHECK"


def test_v5_binds_directional_leg_to_saved_signal_identity(tmp_path):
    store = IndependentReviewPostReceiptStore(tmp_path / "paper.sqlite")
    sid = _signal(store, "identity")
    value = _execution("identity")
    value["legs"][0]["token_id"] = "wrong-token"
    with pytest.raises(WeatherPaperPositionError) as exc:
        store.admit_post_receipt_position(sid, 5.0, value)
    assert exc.value.code == "V5_SIGNAL_TOKEN_IDENTITY_MISMATCH"


def test_v5_requires_post_receipt_recheck_prestate_for_first_admission(tmp_path):
    store = IndependentReviewPostReceiptStore(tmp_path / "paper.sqlite")
    sid = _signal(store, "state", status="ACKNOWLEDGED")
    with pytest.raises(WeatherPaperPositionError) as exc:
        store.admit_post_receipt_position(sid, 5.0, _execution("state"))
    assert exc.value.code == "V5_SIGNAL_PRESTATE_INVALID"


def test_v5_idempotent_retry_must_match_full_execution_identity(tmp_path):
    store = IndependentReviewPostReceiptStore(tmp_path / "paper.sqlite")
    sid = _signal(store, "retry")
    first = _execution("retry")
    opened = store.admit_post_receipt_position(sid, 5.0, first)
    assert opened["status"] == "OPEN"
    conflicting = copy.deepcopy(first)
    conflicting["visible_units"] = 19.0
    with pytest.raises(WeatherPaperPositionError) as exc:
        store.admit_post_receipt_position(sid, 5.0, conflicting)
    assert exc.value.code == "V5_EXISTING_EXECUTION_IDENTITY_CONFLICT"


def test_source_shock_dedupe_is_per_official_exclusion_episode(tmp_path):
    store = IndependentReviewPostReceiptStore(tmp_path / "paper.sqlite")

    def save(previous: float, current: float, observed_at: float):
        return store.save_signal(
            fingerprint="legacy-market-only-fingerprint",
            lane="weather_official_extreme_new_exclusion",
            evidence_class="TEST_SOURCE_SHOCK",
            event_id="event-1",
            market_id="market-1",
            side="NO",
            token_id="no-1",
            theoretical_payout=1.0,
            payload={
                "previous_official_extreme": previous,
                "new_official_extreme": current,
                "latest_official_observed_at": observed_at,
            },
        )

    first = save(92.0, 94.0, NOW)
    assert first is not None
    assert save(92.0, 94.0, NOW) is None
    second = save(93.0, 94.0, NOW + 600.0)
    assert second is not None and second != first


def _maker_order(name: str) -> VirtualMakerOrder:
    return VirtualMakerOrder(
        version="test-maker-order",
        order_id=f"maker-{name}",
        policy_id="policy",
        event_id=f"event-{name}",
        market_id=f"market-{name}",
        condition_id=f"condition-{name}",
        token_id=f"token-{name}",
        outcome="YES",
        bid_price=0.40,
        shares=5.0,
        created_at=NOW + 1.0,
        expires_at=NOW + 301.0,
        fair_model_version="uncalibrated-test",
        fair_evidence_sha256="a" * 64,
        fair_as_of=NOW,
        contract_evidence_sha256="b" * 64,
        source_generation="forecast-sha|ws_generation=1",
        market_parameter_sha256="c" * 64,
        created_book_hash="d" * 64,
        created_book_received_at=NOW + 0.9,
        queue_ahead_shares=2.0,
        simulated_filled_shares=0.0,
        processed_trade_ids=(),
        status=RESTING,
    )


def _maker_signal(store: IndependentReviewPostReceiptStore, name: str) -> int:
    sid = store.save_signal(
        fingerprint=f"maker-fp-{name}",
        lane="weather_maker_virtual_bid",
        evidence_class="TEST_MAKER",
        event_id=f"event-{name}",
        market_id=f"market-{name}",
        side="YES",
        token_id=f"token-{name}",
        theoretical_payout=1.0,
        payload={"order_id": f"maker-{name}", "token_id": f"token-{name}"},
    )
    assert sid is not None
    store.set_signal_status(sid, "PENDING_DELIVERY")
    store.mark_telegram_sent(sid, 700 + sid, sent_at=NOW)
    return sid


def test_maker_idempotent_retry_binds_exact_linkage_evidence(tmp_path):
    path = tmp_path / "paper.sqlite"
    signals = IndependentReviewPostReceiptStore(path)
    sid = _maker_signal(signals, "link")
    maker = IndependentReviewMakerAccountingStore(path)
    order = _maker_order("link")
    maker.activate_after_telegram(
        order,
        signal_id=sid,
        telegram_message_id=700 + sid,
        telegram_sent_at=NOW,
        post_delivery_exact_finished_at=NOW + 1.0,
        recorded_at=NOW + 1.0,
    )
    with pytest.raises(WeatherMakerStoreError) as exc:
        maker.activate_after_telegram(
            order,
            signal_id=sid,
            telegram_message_id=700 + sid,
            telegram_sent_at=NOW,
            post_delivery_exact_finished_at=NOW + 2.0,
            recorded_at=NOW + 2.0,
        )
    assert exc.value.code == "MAKER_ACTIVATION_LINK_IDENTITY_CONFLICT"


def _safe_status() -> dict:
    return {
        "cycle_ok": True,
        "errors": [],
        "financial_delivery": False,
        "financial_authority": False,
        "automatic_order_placement": False,
        "wallet_or_order_api_loaded": False,
        "pws_enabled": False,
        "maker_healthy": True,
        "maker_stream_degraded": False,
        "same_day_three_layer": {"financial_authority": False},
    }


def test_final_wrapper_refuses_to_mask_inherited_financial_authority(tmp_path, monkeypatch):
    status = _safe_status()
    status["financial_authority"] = True

    async def inherited(_self):
        return dict(status)

    monkeypatch.setattr(AllPaperWeatherLiveV8Service, "run_cycle", inherited)
    service = object.__new__(FinalAllPaperWeatherLiveService)
    service.status_path = tmp_path / "status.json"
    with pytest.raises(FinalAllPaperSafetyInvariantError) as exc:
        asyncio.run(service.run_cycle())
    assert exc.value.code == "FINAL_INHERITED_FINANCIAL_AUTHORITY_NOT_FALSE"
    persisted = json.loads(service.status_path.read_text(encoding="utf-8"))
    assert persisted["financial_authority"] is True
    assert persisted["inherited_safety_boundary_verified"] is False
    assert persisted["cycle_ok"] is False


def test_final_wrapper_marks_all_lanes_unhealthy_when_maker_is_degraded(tmp_path, monkeypatch):
    status = _safe_status()
    status["maker_healthy"] = False
    status["maker_stream_degraded"] = True

    async def inherited(_self):
        return dict(status)

    monkeypatch.setattr(AllPaperWeatherLiveV8Service, "run_cycle", inherited)
    service = object.__new__(FinalAllPaperWeatherLiveService)
    service.status_path = tmp_path / "status.json"
    result = asyncio.run(service.run_cycle())
    assert result["cycle_ok"] is True
    assert result["operator_all_lanes_healthy"] is False
    assert result["inherited_safety_boundary_verified"] is True


def test_operator_status_read_fails_green_headline_closed_on_maker_degradation(monkeypatch):
    monkeypatch.setattr(
        AllPaperCommandController,
        "_read_status",
        lambda _self: {
            "cycle_ok": True,
            "maker_healthy": False,
            "maker_stream_degraded": True,
        },
    )
    controller = object.__new__(IndependentReviewAllPaperCommandController)
    status = controller._read_status()
    assert status["cycle_ok"] is False
    assert status["operator_all_lanes_healthy"] is False


def test_cutover_scripts_require_host_approved_exact_previous_release_database_and_venv():
    root = Path(__file__).resolve().parents[1]
    snapshot_wrapper = (root / "deploy/snapshot-all-paper-rollback.sh").read_text(encoding="utf-8")
    snapshot_compat = (root / "deploy/snapshot-all-paper-rollback-v2.sh").read_text(encoding="utf-8")
    host_snapshot = (root / "deploy/weather-paper-host-snapshot.sh").read_text(encoding="utf-8")
    prepare_wrapper = (root / "deploy/prepare-all-paper-candidate.sh").read_text(encoding="utf-8")
    prepare = (root / "deploy/prepare-all-paper-candidate-v3.sh").read_text(encoding="utf-8")
    start = (root / "deploy/start-all-paper-candidate.sh").read_text(encoding="utf-8")
    restore_wrapper = (root / "deploy/restore-all-paper-rollback.sh").read_text(encoding="utf-8")
    restore_compat = (root / "deploy/restore-all-paper-rollback-v2.sh").read_text(encoding="utf-8")
    host_restore = (root / "deploy/weather-paper-host-recovery.sh").read_text(encoding="utf-8")
    gate = (root / "deploy/weather-paper-host-release-gate.py").read_text(encoding="utf-8")

    # Candidate-owned compatibility scripts only delegate to immutable host authority.
    assert "/usr/local/libexec/polymarket-weather-paper/snapshot-rollback.sh" in snapshot_wrapper
    assert "/usr/local/libexec/polymarket-weather-paper/snapshot-rollback.sh" in snapshot_compat
    assert "/usr/local/libexec/polymarket-weather-paper/restore-rollback.sh" in restore_wrapper
    assert "/usr/local/libexec/polymarket-weather-paper/restore-rollback.sh" in restore_compat
    assert "systemctl stop" not in snapshot_wrapper
    assert "systemctl start" not in snapshot_wrapper
    assert "systemctl enable" not in snapshot_wrapper

    # Root-owned V3 generation binds exact source tree, ledger and virtualenv bytes.
    for token in (
        "previous-release.sha",
        "previous-tree.sha",
        "previous-venv.tar",
        "previous-venv.json",
        "previous-db.sha256",
        "previous-db-present",
        "snapshot-generation-v3",
        "all-paper-rollback-v3-host-authority",
        "/usr/bin/python3",
        '"${GATE}" verify-checkout',
        '"${VENV_HELPER}" verify-tree',
    ):
        assert token in host_snapshot

    # Candidate preparation cannot mutate source/dependencies without that approved
    # host generation and exact current-vdev tree proof.
    assert "prepare-all-paper-candidate-v3.sh" in prepare_wrapper
    assert "previous-release.sha" in prepare
    assert "previous-venv-release.sha" in prepare
    assert "snapshot-generation-v3" in prepare
    assert "all-paper-rollback-v3-host-authority" in prepare
    assert '"${HOST_VENV}" verify' in prepare
    assert '"${HOST_VENV}" verify-tree' in prepare
    assert '"${GATE}" verify-object' in prepare
    assert 'HOST_RECOVERY="${LIBEXEC}/restore-rollback.sh"' in prepare

    # Any active acceptance failure uses host-owned recovery, never candidate-owned
    # restoration logic.
    assert 'HOST_RECOVERY="${LIBEXEC}/restore-rollback.sh"' in start
    assert "previous-venv.tar" in start
    assert "snapshot-generation-v3" in start
    assert "host-owned rollback" in start

    # Host recovery proves the approved previous object/tree before checkout, restores
    # DB + exact venv, restores unit state, then re-attests the checkout.
    assert '"${GATE}" verify-object' in host_restore
    assert 'checkout --detach "${PREVIOUS_SHA}"' in host_restore
    assert "previous-tree.sha" in host_restore
    assert "previous-weather-paper.sqlite3" in host_restore
    assert '"${VENV_HELPER}" restore' in host_restore
    assert '"${VENV_HELPER}" verify-tree' in host_restore
    assert "/usr/bin/python3" in host_restore
    assert 'systemctl enable "${UNIT}"' in host_restore
    assert 'systemctl start "${UNIT}"' in host_restore
    assert '"${GATE}" verify-checkout' in host_restore

    # The gate itself binds an approved SHA to an approved Git tree and rejects a dirty
    # checkout instead of trusting the candidate release marker alone.
    assert "HOST_RELEASE_TREE_IDENTITY_MISMATCH" in gate
    assert "HOST_RELEASE_SHA_NOT_APPROVED" in gate
    assert "HOST_RELEASE_CHECKOUT_DIRTY" in gate
