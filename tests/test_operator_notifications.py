import asyncio
from decimal import Decimal
import json
import sqlite3
import time

import pytest

from polymarket_scanner.production.control import ControlStore
from polymarket_scanner.production.notifications import event_batch, import_events, send_pending, notification_summary, event_text
from test_operator_executor import op, activate, request
from test_operator_panel import Bot


def transfer(op):
    cursor=int(op[2].state("notification_cursor"))
    batch=event_batch(op[3],cursor)
    assert import_events(op[2],batch)
    return batch


def test_notifications_are_from_durable_events_not_delivery_or_ack_fills(op):
    activate(op); asyncio.run(op[-1].tick())
    batch=transfer(op)
    kinds=[row["kind"] for row in batch["events"]]
    assert "SUBMISSION_ACKNOWLEDGED" in kinds and "CONFIRMED_FILL" not in kinds
    assert all("signed_payload" not in json.dumps(row) and "signature" not in json.dumps(row) for row in batch["events"])
    assert not import_events(op[2],batch)  # stale batch cannot duplicate notifications
    op[4].fill(Decimal(".5")); asyncio.run(op[-1].reconcile())
    text="\n".join(event_text(row) for row in transfer(op)["events"])
    assert "Partial fill" in text and "actual fee" in text


def test_cancel_request_is_distinct_from_confirmation_and_full_fill(op):
    activate(op); asyncio.run(op[-1].tick()); transfer(op)
    request(op,"CANCEL"); asyncio.run(op[-1].safety_tick())
    text="\n".join(event_text(row) for row in transfer(op)["events"])
    assert "Cancellation requested" in text and "Cancellation confirmed" not in text
    op[4].fill(); asyncio.run(op[-1].reconcile())
    text="\n".join(event_text(row) for row in transfer(op)["events"])
    assert "Full fill" in text


@pytest.mark.parametrize("outcome,label",[("UNKNOWN","SUBMISSION_UNKNOWN"),("REJECTED","SUBMISSION_REJECTED")])
def test_unknown_and_rejected_submission_notifications(op,outcome,label):
    activate(op); op[4].outcome=outcome; asyncio.run(op[-1].tick())
    assert label in [row["kind"] for row in transfer(op)["events"]]


def test_settlement_and_redemption_do_not_claim_spendable_cash(op):
    activate(op); asyncio.run(op[-1].tick()); op[4].fill(); asyncio.run(op[-1].reconcile()); transfer(op)
    op[3].settle("123","1",{"fixture":True})
    text="\n".join(event_text(row) for row in transfer(op)["events"])
    assert "Not redeemed cash" in text
    claim=op[3].summary()["claimable_positions"][0]
    assert claim["unredeemed_value_micros"]>0
    assert op[3].summary()["verified_redemption_proceeds_by_asset_micros"]=={}
    assert "Spendable collateral is checked separately" in event_text({"kind":"VERIFIED_REDEMPTION","data":{"proceeds":1000000,"asset":"USDC.e"},"identity":"fixture","seq":1})


def test_crash_after_outbox_commit_preserves_queue_and_ambiguous_send_never_repeats(op):
    op[3].fault("FIXTURE_FAULT"); transfer(op)
    bot=Bot()
    async def unknown(*args): return {"state":"UNKNOWN"}
    bot.send_result=unknown
    asyncio.run(send_pending(op[2],bot))
    assert notification_summary(op[2])=={"UNKNOWN":1}
    restarted=ControlStore(op[0])
    try:
        async def must_not_send(*args): raise AssertionError("ambiguous send retried")
        bot.send_result=must_not_send
        asyncio.run(send_pending(restarted,bot))
        assert notification_summary(restarted)=={"UNKNOWN":1}
    finally: restarted.close()


def test_crash_during_send_becomes_unknown_on_restart(op):
    op[3].fault("FIXTURE_FAULT"); transfer(op)
    with op[2].connect() as db: db.execute("UPDATE operator_notifications SET state='SENDING'")
    restarted=ControlStore(op[0])
    try: assert notification_summary(restarted)=={"UNKNOWN":1}
    finally: restarted.close()


def test_known_rate_limit_retries_are_bounded(op):
    op[3].fault("FIXTURE_FAULT"); transfer(op); bot=Bot()
    async def rate_limit(*args): return {"state":"RETRY","retry_after":1}
    bot.send_result=rate_limit
    for _ in range(10):
        with op[2].connect() as db: db.execute("UPDATE operator_notifications SET due=0")
        asyncio.run(send_pending(op[2],bot))
    assert notification_summary(op[2])=={"FAILED":1}
    with op[2].connect() as db: assert db.execute("SELECT attempts FROM operator_notifications").fetchone()[0]==8


def test_failed_db_transfer_does_not_advance_cursor(op):
    op[3].fault("FIXTURE_FAULT")
    with op[2].connect() as db:
        db.execute("CREATE TRIGGER fail_outbox BEFORE INSERT ON operator_notifications BEGIN SELECT RAISE(ABORT,'injected'); END")
    with pytest.raises(sqlite3.Error): transfer(op)
    assert op[2].state("notification_cursor")=="0"


def test_failed_receipt_write_leaves_sending_unknown_not_auto_resend(op):
    op[3].fault("FIXTURE_FAULT"); transfer(op); bot=Bot()
    with op[2].connect() as db:
        db.execute("CREATE TRIGGER fail_sent BEFORE UPDATE ON operator_notifications WHEN NEW.state='SENT' BEGIN SELECT RAISE(ABORT,'injected'); END")
    with pytest.raises(sqlite3.Error): asyncio.run(send_pending(op[2],bot))
    assert len(bot.messages)==1
    restarted=ControlStore(op[0])
    try: assert notification_summary(restarted)=={"UNKNOWN":1}
    finally: restarted.close()


def test_notification_hot_queue_is_bounded_and_backpressure_keeps_audit_cursor(op):
    for _ in range(501): op[3].fault("FIXTURE_FAULT")
    for _ in range(5): transfer(op)
    before=op[2].state("notification_cursor")
    assert not import_events(op[2],event_batch(op[3],int(before)))
    assert op[2].state("notification_cursor")==before
    assert notification_summary(op[2])["PENDING"]<=500


def test_local_emergency_stop_has_durable_notification_without_inventing_cancellation(op):
    activate(op); asyncio.run(op[-1].tick()); transfer(op)
    op[0].stop_file.write_text("operator fixture stop")
    asyncio.run(op[-1].safety_tick()); asyncio.run(op[-1].safety_tick())
    events=[e for e in transfer(op)["events"] if e["kind"]=="EMERGENCY_PAUSE"]
    assert len(events)==1 and "held positions remain" in event_text(events[0])
    assert not op[-1].authority() and not op[4].cancels
    assert op[-1].status()["opening_disabled_reason"]=="LOCAL_EMERGENCY_STOP_FILE"


def test_exchange_expiry_is_visible_without_erasing_accounting(op):
    activate(op); asyncio.run(op[-1].tick())
    row=op[3].orders()[0]
    op[3].confirm_terminal(row["id"],cancelled=True,matched=0,exchange_status="EXPIRED")
    assert op[3].summary()["recent_orders"][0]["exchange_terminal_status"]=="EXPIRED"
    assert "Order expired" in "\n".join(event_text(e) for e in transfer(op)["events"])
