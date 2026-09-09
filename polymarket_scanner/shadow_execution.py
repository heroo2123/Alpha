"""Exercise the exact CLOB planner without granting Telegram delivery permission."""
from __future__ import annotations

import asyncio
import copy
import time

from .execution_certificate import build_execution_certificate
from .trade_only import _delivery_market_ids, _open_market_state
from .safe_logging import redact_secret_text

SHADOW_EXECUTION_VERSION = "shadow_exact_clob_preview_v1_no_delivery_permission"


async def record_shadow_execution(signal, poly) -> None:
    if signal.confidence != "ACTIONABLE":
        return
    started = time.monotonic()
    result = {"version": SHADOW_EXECUTION_VERSION, "delivery_permission": False,
              "payout_is_detector_assumption": True, "observed_at": time.time()}
    try:
        async with asyncio.timeout(20):
            ids = _delivery_market_ids(signal)
            if not ids or len(ids) > 6:
                raise ValueError("market identity missing or unsupported leg count")
            raw = await asyncio.gather(*(poly.market_by_id(mid) for mid in ids))
            if not all(_open_market_state(row) for row in raw):
                raise ValueError("current market lifecycle is not executable")
            preview = copy.deepcopy(signal)
            result["certificate"] = await build_execution_certificate(preview, poly, raw)
            result["status"] = "EXACT_EXECUTION_PREVIEW_ONLY"
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        result.update(status="REJECTED", error_type=type(exc).__name__, reason=redact_secret_text(exc)[:400])
    result["elapsed_seconds"] = time.monotonic() - started
    signal.metadata["shadow_execution"] = result
    signal.metadata["trade_ready"] = False
