from __future__ import annotations

from polymarket_scanner.weather_only_maker_paper_accounting_v5 import (
    MAKER_RESTART_ORPHAN_STATUS,
    MakerPaperAccountingStoreV5,
)
from polymarket_scanner.weather_only_maker_shadow import RESTING, VirtualMakerOrder
from polymarket_scanner.weather_only_paper_post_receipt import PostReceiptWeatherPaperStore

NOW = 1_800_000_000.0


def _order(name: str) -> VirtualMakerOrder:
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


def _signal(path, name: str) -> tuple[PostReceiptWeatherPaperStore, int]:
    store = PostReceiptWeatherPaperStore(path)
    sid = store.save_signal(
        fingerprint=f"fp-{name}",
        lane="weather_maker_virtual_bid",
        evidence_class="TEST_MAKER",
        event_id=f"event-{name}",
        market_id=f"market-{name}",
        side="YES",
        token_id=f"token-{name}",
        model_probability=0.7,
        entry_cost=0.40,
        raw_gap=0.2,
        theoretical_payout=1.0,
        created_at=NOW,
        payload={
            "order_id": f"maker-{name}",
            "token_id": f"token-{name}",
            "financial_authority": False,
            "automatic_order_placement": False,
        },
    )
    assert sid is not None
    store.set_signal_status(sid, "PENDING_DELIVERY")
    store.mark_telegram_sent(sid, 700 + sid, sent_at=NOW)
    return store, sid


def test_atomic_maker_activation_links_order_and_signal(tmp_path):
    path = tmp_path / "paper.sqlite"
    signal_store, sid = _signal(path, "one")
    maker = MakerPaperAccountingStoreV5(path)
    order = _order("one")
    saved = maker.activate_after_telegram(
        order,
        signal_id=sid,
        telegram_message_id=700 + sid,
        telegram_sent_at=NOW,
        post_delivery_exact_finished_at=NOW + 1.0,
        recorded_at=NOW + 1.0,
    )
    assert saved == order
    assert maker.load_order(order.order_id) == order
    with signal_store._conn() as db:
        assert db.execute(
            "SELECT status FROM weather_paper_signals WHERE id=?", (sid,)
        ).fetchone()[0] == "MAKER_RESTING"
    events = maker.db.execute(
        "SELECT event_type FROM weather_maker_shadow_events WHERE order_id=? ORDER BY seq",
        (order.order_id,),
    ).fetchall()
    assert [str(row[0]) for row in events] == ["ORDER_CREATED", "TELEGRAM_SIGNAL_LINKED"]


def test_atomic_maker_activation_is_idempotent(tmp_path):
    path = tmp_path / "paper.sqlite"
    _store, sid = _signal(path, "idem")
    maker = MakerPaperAccountingStoreV5(path)
    order = _order("idem")
    for _ in range(2):
        assert maker.activate_after_telegram(
            order,
            signal_id=sid,
            telegram_message_id=700 + sid,
            telegram_sent_at=NOW,
            post_delivery_exact_finished_at=NOW + 1.0,
            recorded_at=NOW + 1.0,
        ) == order
    assert maker.db.execute(
        "SELECT COUNT(*) FROM weather_maker_shadow_events WHERE order_id=?",
        (order.order_id,),
    ).fetchone()[0] == 2


def test_receipted_but_unactivated_maker_signal_is_not_reconstructed_after_restart(tmp_path):
    path = tmp_path / "paper.sqlite"
    signal_store, sid = _signal(path, "orphan")
    maker = MakerPaperAccountingStoreV5(path)
    assert maker.reconcile_unactivated_receipts_after_restart(recorded_at=NOW + 2.0) == 1
    with signal_store._conn() as db:
        assert db.execute(
            "SELECT status FROM weather_paper_signals WHERE id=?", (sid,)
        ).fetchone()[0] == MAKER_RESTART_ORPHAN_STATUS
    assert maker.reconcile_unactivated_receipts_after_restart(recorded_at=NOW + 3.0) == 0
    assert maker.db.execute(
        "SELECT COUNT(*) FROM weather_maker_shadow_orders"
    ).fetchone()[0] == 0
