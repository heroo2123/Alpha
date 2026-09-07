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


def test_old_blanket_manual_accounting_disable_hook_is_gone():
    # Production now captures the user's actual execution cost, so there must be no
    # hook left that globally replaces Store.record_manual with a blanket rejection.
    assert not hasattr(trade_worker, "_disable_legacy_manual_accounting")
