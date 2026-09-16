"""Adjacent continuation checks after the first independent safety corrections."""
import asyncio

from polymarket_scanner.production.controller import Controller
from polymarket_scanner.production.io import atomic_json
from polymarket_scanner.production.signals import SignalStore
from test_operator_executor import op, activate, request
from test_operator_panel import Bot, command


def test_fresh_stop_cannot_be_lost_to_concurrent_status_revision(op, monkeypatch):
    activate(op)
    atomic_json(op[0].execution_status_path, op[-1].status())
    signals = SignalStore(op[0].signal_db)
    bot = Bot()
    controller = Controller(op[0], signals, op[2], bot)
    original = controller.panel.settings
    reads = 0

    def concurrent_status():
        nonlocal reads
        observed = original()
        reads += 1
        if reads == 1:
            # Already accepted settings are processed on the executor while the
            # fresh textual /stop is being handled in the controller process.
            request(op, 'SET', {'key': 'per_order', 'value': '4'})
            atomic_json(op[0].execution_status_path, op[-1].status())
        return observed

    monkeypatch.setattr(controller.panel, 'settings', concurrent_status)
    bot.incoming = [command('/stop', 101)]
    try:
        asyncio.run(controller.commands())
        assert op[2].state('safety_epoch') == '1', 'Fresh /stop rejected after second settings read'
        assert not op[-1].authority()
    finally:
        signals.close()
