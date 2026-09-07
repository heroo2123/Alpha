from __future__ import annotations

"""Production runtime: silent research, Telegram reserved for explicitly promoted TRADE NOW only.

Builds on the stable v2 scanner. Detector/research behavior and storage stay intact;
only delivery policy changes. WATCH/experimental signals remain stored/scored in the
background. During the P0 containment phase the promoted registry is intentionally
empty, so no financial alert may be queued for Telegram.
"""

import app as base
import app_stable_v2 as stable_v2
from polymarket_scanner.trade_only import is_trade_ready, mark_trade_readiness, promoted_detectors

app = stable_v2.app

_original_confirm_actionable = base.confirm_actionable
_original_enqueue_alert = base.enqueue_alert


async def _trade_only_confirm(signal):
    confirmed = await _original_confirm_actionable(signal)
    if confirmed is None:
        return None
    mark_trade_readiness(confirmed)
    return confirmed


def _trade_only_enqueue(signal_id, signal):
    if not is_trade_ready(signal):
        return False
    return _original_enqueue_alert(signal_id, signal)


async def _silent_scanner_push(*_args, **_kwargs):
    # The standalone command service owns user-facing control/status messages.
    # Scanner startup/paper-result chatter stays silent in trade-only mode.
    return None


# app.py resolves these globals dynamically from its own module namespace.
base.confirm_actionable = _trade_only_confirm
base.enqueue_alert = _trade_only_enqueue
base.tg.send = _silent_scanner_push


async def _mark_trade_only_runtime() -> None:
    promoted = promoted_detectors()
    base.state["telegram_delivery_mode"] = "TRADE_NOW_ONLY"
    base.state["silent_research_enabled"] = True
    base.state["trade_now_promoted_detectors"] = list(promoted)
    base.state["trade_now_promotion_count"] = len(promoted)
    base.state["p0_containment"] = len(promoted) == 0


app.add_event_handler("startup", _mark_trade_only_runtime)
