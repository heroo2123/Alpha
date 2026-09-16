from __future__ import annotations

from dataclasses import replace

import pytest

from polymarket_scanner.weather_only_maker_shadow import PublicTradePrint, simulate_public_trade_progression
from polymarket_scanner.weather_only_maker_store import (
    WeatherMakerShadowStore,
    WeatherMakerStoreError,
)
from test_weather_only_maker_shadow import NOW, _order


def test_store_round_trips_virtual_order_with_restrictive_permissions_and_hash_chain(tmp_path):
    path = (tmp_path / "maker-shadow.sqlite").resolve()
    order = _order()
    with WeatherMakerShadowStore(path) as store:
        saved = store.save_new_order(order, recorded_at=NOW + 1.0)
        loaded = store.load_order(order.order_id)
        report = store.audit_order(order.order_id)
    assert saved == order
    assert loaded == order
    assert path.stat().st_mode & 0o777 == 0o600
    assert report["event_count"] == 1
    assert report["processed_trade_id_count"] == 0
    assert report["actual_order_placed"] is False
    assert report["actual_fill_authority"] is False
    assert report["financial_authority"] is False
    assert len(report["last_event_sha256"]) == 64


def test_processed_trade_history_survives_restart_and_prevents_replay_fill(tmp_path):
    path = (tmp_path / "maker-shadow.sqlite").resolve()
    original = _order(shares=10.0)
    first_trade = PublicTradePrint("trade-a", "YES-1", 0.24, 3.0, NOW + 1.0, "SELL")
    first, first_sim = simulate_public_trade_progression(original, [first_trade])

    with WeatherMakerShadowStore(path) as store:
        store.save_new_order(original, recorded_at=NOW)
        store.update_order(
            original,
            first,
            event_type="TRADE_PROGRESSION",
            payload=first_sim.as_dict(),
            recorded_at=NOW + 1.1,
        )

    with WeatherMakerShadowStore(path) as restarted:
        loaded = restarted.load_order(original.order_id)
        second_trade = PublicTradePrint("trade-b", "YES-1", 0.24, 2.0, NOW + 2.0, "SELL")
        second, second_sim = simulate_public_trade_progression(loaded, [first_trade, second_trade])
        restarted.update_order(
            loaded,
            second,
            event_type="TRADE_PROGRESSION",
            payload=second_sim.as_dict(),
            recorded_at=NOW + 2.1,
        )
        report = restarted.audit_order(original.order_id)

    assert second_sim.replayed_trade_ids == ("trade-a",)
    assert second_sim.new_simulated_fill_shares == pytest.approx(2.0)
    assert second.simulated_filled_shares == pytest.approx(5.0)
    assert second.processed_trade_ids == ("trade-a", "trade-b")
    assert report["event_count"] == 3
    assert report["processed_trade_id_count"] == 2
    assert report["simulated_filled_shares"] == pytest.approx(5.0)


def test_stale_writer_cannot_overwrite_newer_virtual_order_state(tmp_path):
    path = (tmp_path / "maker-shadow.sqlite").resolve()
    original = _order(shares=10.0)
    trade = PublicTradePrint("trade-a", "YES-1", 0.24, 2.0, NOW + 1.0, "SELL")
    progressed, sim = simulate_public_trade_progression(original, [trade])
    with WeatherMakerShadowStore(path) as store:
        store.save_new_order(original, recorded_at=NOW)
        store.update_order(original, progressed, event_type="TRADE_PROGRESSION", payload=sim.as_dict(), recorded_at=NOW + 1.1)
        stale_target = replace(original, queue_ahead_shares=0.0)
        with pytest.raises(WeatherMakerStoreError) as raised:
            store.update_order(original, stale_target, event_type="STALE", payload={}, recorded_at=NOW + 2.0)
    assert raised.value.code == "MAKER_STORE_STALE_WRITER"


def test_same_order_creation_is_idempotent_but_conflicting_order_id_fails_closed(tmp_path):
    path = (tmp_path / "maker-shadow.sqlite").resolve()
    order = _order()
    with WeatherMakerShadowStore(path) as store:
        store.save_new_order(order, recorded_at=NOW)
        assert store.save_new_order(order, recorded_at=NOW + 1.0) == order
        assert store.audit_order(order.order_id)["event_count"] == 1
        conflict = replace(order, shares=11.0)
        with pytest.raises(WeatherMakerStoreError) as raised:
            store.save_new_order(conflict, recorded_at=NOW + 2.0)
    assert raised.value.code == "MAKER_STORE_ORDER_ID_CONFLICT"


def test_store_rejects_fill_regression_queue_increase_and_trade_history_rewrite(tmp_path):
    path = (tmp_path / "maker-shadow.sqlite").resolve()
    original = _order(shares=10.0)
    trade = PublicTradePrint("trade-a", "YES-1", 0.24, 3.0, NOW + 1.0, "SELL")
    progressed, sim = simulate_public_trade_progression(original, [trade])
    with WeatherMakerShadowStore(path) as store:
        store.save_new_order(progressed, recorded_at=NOW + 1.0)
        with pytest.raises(WeatherMakerStoreError) as fill_regression:
            store.update_order(progressed, replace(progressed, simulated_filled_shares=1.0), event_type="BAD", payload={}, recorded_at=NOW + 2.0)
        assert fill_regression.value.code == "MAKER_STORE_FILL_REGRESSION"

        with pytest.raises(WeatherMakerStoreError) as queue_growth:
            store.update_order(progressed, replace(progressed, queue_ahead_shares=progressed.queue_ahead_shares + 1.0), event_type="BAD", payload={}, recorded_at=NOW + 2.0)
        assert queue_growth.value.code == "MAKER_STORE_QUEUE_INCREASE"

        with pytest.raises(WeatherMakerStoreError) as history:
            store.update_order(progressed, replace(progressed, processed_trade_ids=("different",)), event_type="BAD", payload={}, recorded_at=NOW + 2.0)
        assert history.value.code == "MAKER_STORE_TRADE_HISTORY_REGRESSION"


def test_state_or_event_tampering_is_detected_by_digest_chain(tmp_path):
    path = (tmp_path / "maker-shadow.sqlite").resolve()
    order = _order()
    with WeatherMakerShadowStore(path) as store:
        store.save_new_order(order, recorded_at=NOW)
        store.db.execute(
            "UPDATE weather_maker_shadow_events SET payload_json=? WHERE order_id=?",
            ('{"tampered":true}', order.order_id),
        )
        with pytest.raises(WeatherMakerStoreError) as event_error:
            store.audit_order(order.order_id)
        assert event_error.value.code == "MAKER_STORE_EVENT_PAYLOAD_DIGEST_MISMATCH"

    state_path = (tmp_path / "maker-shadow-state.sqlite").resolve()
    with WeatherMakerShadowStore(state_path) as store:
        store.save_new_order(order, recorded_at=NOW)
        store.db.execute(
            "UPDATE weather_maker_shadow_orders SET state_json=? WHERE order_id=?",
            ('{"tampered":true}', order.order_id),
        )
        with pytest.raises(WeatherMakerStoreError) as state_error:
            store.load_order(order.order_id)
        assert state_error.value.code == "MAKER_STORE_STATE_DIGEST_MISMATCH"


def test_research_event_cannot_smuggle_financial_authority(tmp_path):
    path = (tmp_path / "maker-shadow.sqlite").resolve()
    order = _order()
    with WeatherMakerShadowStore(path) as store:
        store.save_new_order(order, recorded_at=NOW)
        with pytest.raises(WeatherMakerStoreError) as raised:
            store.append_research_event(
                order.order_id,
                event_type="MARKOUT",
                payload={"financial_authority": True},
                recorded_at=NOW + 30.0,
            )
    assert raised.value.code == "MAKER_STORE_EVENT_AUTHORITY_BROKEN"
