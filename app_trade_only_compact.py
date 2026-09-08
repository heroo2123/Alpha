from __future__ import annotations

"""Controlled shadow entrypoint for the 2026 large-universe production runtime.

Install complete-Gamma filtered materialisation and Gamma-BBO screening before the
existing trade-only wrapper captures runtime hooks.  All trade-only containment,
execution confirmation, atomic outbox and delivery-time revalidation logic remains
owned by app_trade_only.
"""

import app_stable_v2 as stable_v2
from polymarket_scanner.production_gamma_bbo import install_production_gamma_runtime

install_production_gamma_runtime(stable_v2)

import app_trade_only as trade_only

app = trade_only.app
