"""Independent frozen-022 operator review, temporary SQLite and mocked peers only."""
import asyncio
import time

from polymarket_scanner.production.config import digest
from polymarket_scanner.production.controller import Controller
from polymarket_scanner.production.io import atomic_json
from polymarket_scanner.production.signals import SignalStore
from test_operator_executor import op, activate, request
from test_operator_panel import Bot, command, panel, flush, callback


def test_stop_followed_by_cancel_in_same_poll_must_cancel_outstanding(op):
    activate(op)
    asyncio.run(op[-1].tick())
    assert op[3].orders()[0]['status'] == 'ACKNOWLEDGED'
    atomic_json(op[0].execution_status_path, op[-1].status())
    signals = SignalStore(op[0].signal_db)
    bot = Bot()
    controller = Controller(op[0], signals, op[2], bot)
    bot.incoming = [command('/stop', 101), command('/cancel_open', 102)]
    try:
        asyncio.run(controller.commands())
        assert op[2].state('safety_epoch') == '2'
        asyncio.run(op[-1].safety_tick())
        with op[3].connect() as db:
            receipts = [dict(r) for r in db.execute('SELECT * FROM execution_controls ORDER BY seq')]
        assert op[4].cancels, receipts
        assert op[3].orders()[0]['status'] == 'CANCEL_REQUESTED'
    finally:
        signals.close()


def test_cancel_recorded_before_restart_survives_request_expiry(op, monkeypatch):
    activate(op)
    asyncio.run(op[-1].tick())
    request(op, 'CANCEL', process=False)
    future = time.time() + 121
    monkeypatch.setattr(time, 'time', lambda: future)
    asyncio.run(op[-1].safety_tick())
    assert op[4].cancels, 'Accepted durable safety request expired without queuing cancellation'


def test_navigation_preview_backlog_cannot_block_emergency_stop(op):
    activate(op)
    atomic_json(op[0].execution_status_path, op[-1].status())
    signals = SignalStore(op[0].signal_db)
    bot = Bot()
    controller = Controller(op[0], signals, op[2], bot)
    try:
        for _ in range(500):
            op[2].action(42, 'NAV', {'screen': 'HOME'}, op[-1].control.settings['revision'])
        bot.incoming = [command('/stop', 101)]
        asyncio.run(controller.commands())
        assert op[2].state('safety_epoch') == '1', 'UI preview capacity rejected authenticated /stop'
        assert not op[-1].authority()
    finally:
        signals.close()


def test_trade_preview_cannot_rebind_to_newer_more_permissive_revision(panel, op, monkeypatch):
    ui, bot = panel
    activate(op, 'CONFIRM')
    request(op, 'SET', {'key': 'per_order', 'value': '2'})
    atomic_json(op[0].execution_status_path, op[-1].status())
    original_settings = ui.settings
    reads = 0

    def settings_with_concurrent_publication():
        nonlocal reads
        observed = original_settings()
        reads += 1
        if reads == 1:
            # Another already authorized settings request is processed by the
            # separate executor after the preview reads, before button creation.
            request(op, 'SET', {'key': 'per_order', 'value': '5'})
            atomic_json(op[0].execution_status_path, op[-1].status())
        return observed

    monkeypatch.setattr(ui, 'settings', settings_with_concurrent_publication)
    asyncio.run(ui.preview(42, 'CONFIRM_TRADE', {
        'signal_id': op[1]['id'], 'signal_hash': digest(op[1])}))
    flush(ui)
    assert 'Per-order ceiling $2;' in bot.messages[-1]['text']
    asyncio.run(ui.handle(callback(bot, 'Confirm one attempt', uid=101)))
    op[-1].control.process()
    asyncio.run(op[-1].tick())
    assert not op[4].posts, 'Old $2 preview acquired a newer $5 revision and submitted'


def test_full_reply_queue_does_not_drop_later_safety_commands(op):
    activate(op); asyncio.run(op[-1].tick())
    atomic_json(op[0].execution_status_path,op[-1].status())
    signals=SignalStore(op[0].signal_db); bot=Bot(); controller=Controller(op[0],signals,op[2],bot)
    try:
        for _ in range(100): controller.panel.enqueue({'text':'fixture','markup':None,'ids':[]})
        bot.incoming=[command('/status',100),command('/stop',101),command('/cancel_open',102)]
        asyncio.run(controller.commands()); asyncio.run(op[-1].safety_tick())
        assert op[4].cancels and not op[-1].authority()
        assert op[2].state('telegram_offset')=='103'
    finally: signals.close()
