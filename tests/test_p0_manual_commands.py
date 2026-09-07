import command_worker_trade_only as trade_worker


def test_took_requires_actual_execution_cost():
    assert trade_worker._parse_took_command("/took 137 50") is None
    assert trade_worker._parse_took_command("/took 137 50 0") is None


def test_took_parses_actual_execution_cost():
    parsed = trade_worker._parse_took_command("/took 137 50 0.943")
    assert parsed == (137, 50.0, 0.943)


def test_took_parser_is_strict_about_extra_or_missing_fields():
    assert trade_worker._parse_took_command("/took") is None
    assert trade_worker._parse_took_command("/took 137 50 0.943 extra") is None
    assert trade_worker._parse_took_command("/took -1 50 0.943") is None


def test_trade_only_install_no_longer_disables_store_actual_fill_accounting(monkeypatch):
    calls = {}

    async def fake_poll(self):
        return None

    # Installation should replace the Telegram command parser/transport only. It
    # must not monkeypatch Store.record_manual back to the old P0 blanket rejection.
    original_record_manual = trade_worker.worker.Store.record_manual
    trade_worker.install_trade_only_policy()
    calls["same"] = trade_worker.worker.Store.record_manual is original_record_manual
    assert calls["same"] is True
