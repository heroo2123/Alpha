import asyncio

import command_worker_trade_only as policy


class FakeResponse:
    status_code = 200

    def __init__(self, updates):
        self._updates = updates

    def raise_for_status(self):
        return None

    def json(self):
        return {"ok": True, "result": self._updates}


class FakeHTTP:
    def __init__(self, updates):
        self.updates = updates

    async def get(self, *_args, **_kwargs):
        return FakeResponse(self.updates)


class FakeStore:
    def __init__(self, signal=None):
        self.state = {"telegram_offset": "0"}
        self.signal = signal
        self.manual_calls = []

    def get_state(self, key, default=""):
        return self.state.get(key, default)

    def set_state(self, key, value):
        self.state[key] = value

    def get_signal(self, _signal_id):
        return self.signal

    def record_manual(self, *args):
        self.manual_calls.append(args)
        raise AssertionError("record_manual must not be used for structural alerts")


class FakeTelegram:
    token_enabled = True
    token = "bot-token"
    chat_id = "42"
    _commands_registered = True
    last_command_poll_at = None
    last_command_error = None

    def __init__(self, updates, signal=None):
        self.command_http = FakeHTTP(updates)
        self.store = FakeStore(signal=signal)
        self.sent = []

    async def send(self, text, *_args, **_kwargs):
        self.sent.append(str(text))

    async def send_to(self, _chat, text, *_args, **_kwargs):
        self.sent.append(str(text))

    async def send_status(self):
        self.sent.append("status")

    async def send_stats(self):
        self.sent.append("stats")

    async def send_manual_stats(self):
        self.sent.append("mystats")


def _update(text, *, chat="42", update_id=10):
    return {
        "update_id": update_id,
        "message": {
            "chat": {"id": chat, "type": "private"},
            "text": text,
        },
    }


def test_authorized_filled_command_records_exact_parsed_leg_vector(monkeypatch):
    captured = {}

    def fake_record(store, signal_id, fills):
        captured["store"] = store
        captured["signal_id"] = signal_id
        captured["fills"] = fills
        return {
            "id": 7,
            "signal_id": signal_id,
            "legs": [{"leg": 1}, {"leg": 2}],
            "shares": 50.0,
            "total_cash_cost": 46.52,
            "bundle_cost": 0.9304,
            "within_cert_limits": True,
            "within_cert_capacity": True,
        }

    monkeypatch.setattr(policy, "record_structural_fills", fake_record)
    tg = FakeTelegram([
        _update("/filled 137 1=50@0.470+0.02 2=50@0.460+0")
    ])

    asyncio.run(policy._safe_poll_commands(tg))

    assert captured["store"] is tg.store
    assert captured["signal_id"] == 137
    assert captured["fills"] == [
        {"leg": 1, "shares": 50.0, "avg_price": 0.47, "fee_usd": 0.02},
        {"leg": 2, "shares": 50.0, "avg_price": 0.46, "fee_usd": 0.0},
    ]
    assert any("Recorded structural trade #7" in text for text in tg.sent)
    assert any("user-reported fill evidence" in text for text in tg.sent)
    assert tg.store.state["telegram_offset"] == "11"


def test_wrong_chat_cannot_record_structural_fills(monkeypatch):
    called = False

    def fake_record(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("unauthorized chat reached structural ledger")

    monkeypatch.setattr(policy, "record_structural_fills", fake_record)
    tg = FakeTelegram([
        _update("/filled 137 1=50@0.470+0 2=50@0.460+0", chat="999")
    ])

    asyncio.run(policy._safe_poll_commands(tg))

    assert called is False
    assert tg.sent == []
    assert tg.store.state["telegram_offset"] == "11"


def test_structural_took_is_redirected_to_per_leg_fill_command():
    tg = FakeTelegram(
        [_update("/took 137 50 0.93")],
        signal={"detector": "binary_buy_both"},
    )

    asyncio.run(policy._safe_poll_commands(tg))

    assert tg.store.manual_calls == []
    assert any("cannot use <code>/took</code>" in text for text in tg.sent)
    assert any("/filled ALERT_ID" in text for text in tg.sent)
