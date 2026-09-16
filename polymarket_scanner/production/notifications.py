"""Durable execution audit -> bounded controller outbox -> Telegram.

The transfer cursor advances only with the outbox commit. Ambiguous sends are
UNKNOWN and never retried automatically. Explicit rejections may be retried.
"""
import json
import time

from .config import canonical, digest
from .control import ControlError
from .service import bounded_reply, units

EVENT_FIELDS = {
    "SUBMISSION_ACKNOWLEDGED": (), "SUBMISSION_UNKNOWN": (), "SUBMISSION_REJECTED": (),
    "CONFIRMED_FILL": ("order", "quantity", "cost", "fee", "matched", "ordered"),
    "CANCEL_REQUESTED": ("reason",), "ORDER_TERMINAL": ("cancelled", "matched", "exchange_status"),
    "OPPORTUNITY_REJECTED": ("reason",), "RECONCILIATION_FAULT": ("code",),
    "ACTUAL_LIMIT_BREACH_CANCEL_QUEUED": ("fault",), "FILL_LIMIT_UPGRADE_BREACH": (),
    "OPERATOR_CONTROL": ("result", "operation", "revision", "paused"),
    "EMERGENCY_PAUSE": ("reason",),
    "ACTUAL_POSITION_SETTLED_CLAIMABLE": ("claim_value",),
    "VERIFIED_REDEMPTION": ("proceeds", "asset"), "SETTLEMENT_CHAIN_CORRECTION": (),
    "SETTLEMENT_PROOF_UNAVAILABLE": (), "SETTLEMENT_LATE_FILL_RECONCILED": (),
}


def sanitized_events(rows):
    events = []
    for row in rows:
        if row["kind"] not in EVENT_FIELDS:
            continue
        raw = json.loads(row["data"])
        data = {key: raw[key] for key in EVENT_FIELDS[row["kind"]] if key in raw}
        event = {"seq": row["seq"], "kind": row["kind"], "identity": row["identity"], "data": data, "created": row["created"]}
        events.append(dict(event, id=digest(event)))
    return events


def event_batch(ledger, after):
    with ledger.connect() as db:
        rows = db.execute("SELECT * FROM execution_audit WHERE seq>? ORDER BY seq LIMIT 100", (after,)).fetchall()
    return {"after": after, "through": rows[-1]["seq"] if rows else after, "events": sanitized_events(rows)}


def import_events(store, batch):
    with store.transaction() as db:
        row = db.execute("SELECT value FROM operator_state WHERE key='notification_cursor'").fetchone()
        cursor = int(row[0]) if row else 0
        if not isinstance(batch, dict) or batch.get("after") != cursor:
            return False  # stale status file; wait for an acknowledged new batch
        if type(batch.get("through")) is not int or batch["through"] < cursor:
            raise ControlError("NOTIFICATION_CURSOR_INVALID")
        events = batch.get("events")
        if not isinstance(events, list) or len(events) > 100:
            raise ControlError("NOTIFICATION_BATCH_INVALID")
        pending = db.execute("SELECT COUNT(*) FROM operator_notifications WHERE state!='SENT'").fetchone()[0]
        if pending + len(events) > 500:
            return False  # bounded hot queue; durable audit remains at the executor
        previous = cursor
        for event in events:
            if (not isinstance(event, dict) or set(event) != {"id","seq","kind","identity","data","created"}
                or type(event["seq"]) is not int or not previous < event["seq"] <= batch["through"]
                or event["kind"] not in EVENT_FIELDS or digest({k:v for k,v in event.items() if k!="id"}) != event["id"]):
                raise ControlError("NOTIFICATION_EVIDENCE_INVALID")
            previous = event["seq"]
            db.execute("INSERT OR IGNORE INTO operator_notifications(id,event,state,due) VALUES(?,?,'PENDING',?)", (event["id"],canonical(event),time.time()))
        db.execute("INSERT OR REPLACE INTO operator_state VALUES('notification_cursor',?)", (str(batch["through"]),))
    return True


def event_text(event):
    kind, data = event["kind"], event["data"]
    if kind == "CONFIRMED_FILL":
        state = "Full fill" if data.get("matched") == data.get("ordered") and data.get("ordered") else "Partial fill"
        detail = f"{state}: {units(data['quantity'])} shares; actual cost ${units(data['cost'])}; actual fee ${units(data['fee'])}."
    elif kind == "SUBMISSION_ACKNOWLEDGED":
        detail = "Order submitted and acknowledged. This is not a fill."
    elif kind == "SUBMISSION_UNKNOWN":
        detail = "Submission UNKNOWN. No blind resubmission; capital remains reserved pending reconciliation."
    elif kind == "CANCEL_REQUESTED":
        detail = "Cancellation requested. Confirmation is pending; fills and held inventory remain."
    elif kind == "EMERGENCY_PAUSE":
        detail = "Emergency pause: new openings disabled. Existing orders require reconciliation/cancellation; held positions remain. " + str(data.get("reason",""))
    elif kind == "ORDER_TERMINAL":
        detail = ("Order expired" if data.get("exchange_status") == "EXPIRED" else "Cancellation confirmed" if data.get("cancelled") else "Order fully filled") + f". Confirmed shares: {units(data['matched'])}."
    elif kind == "ACTUAL_POSITION_SETTLED_CLAIMABLE":
        detail = f"Settlement claim value ${units(data['claim_value'])}. Not redeemed cash or spendable collateral."
    elif kind == "VERIFIED_REDEMPTION":
        detail = f"Verified redemption: {units(data['proceeds'])} units of asset {data['asset']}. Spendable collateral is checked separately."
    else:
        detail = kind + "\n" + canonical(data)
    return "EXECUTION EVENT\n" + detail + "\nID: " + str(event["identity"]) + "\nEvent: " + str(event["seq"])


async def send_pending(store, telegram):
    # One per call keeps UI and invalidation responsive; caller runs independently.
    with store.transaction() as db:
        row = db.execute("SELECT * FROM operator_notifications WHERE state='PENDING' AND due<=? ORDER BY due,id LIMIT 1", (time.time(),)).fetchone()
        if not row:
            return
        db.execute("UPDATE operator_notifications SET state='SENDING',attempts=attempts+1 WHERE id=?", (row["id"],))
    try:
        result = await telegram.send_result(bounded_reply(event_text(json.loads(row["event"]))))
    except Exception:
        result = {"state":"UNKNOWN"}
    state = result.get("state", "UNKNOWN")
    due = time.time()
    if state == "RETRY":
        state = "PENDING" if row["attempts"] + 1 < 8 else "FAILED"
        due += min(3600, max(1, result.get("retry_after",60)))
    if state not in {"SENT","UNKNOWN","PENDING","FAILED"}:
        state = "UNKNOWN"
    with store.transaction() as db:
        db.execute("UPDATE operator_notifications SET state=?,due=?,message_id=? WHERE id=?", (state,due,result.get("message_id"),row["id"]))


def notification_summary(store):
    with store.connect() as db:
        return {row[0]:row[1] for row in db.execute("SELECT state,COUNT(*) FROM operator_notifications GROUP BY state")}
