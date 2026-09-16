# Weather paper v4 operator states

These labels are deliberately separated so Telegram never uses one overloaded
"OPEN" concept for delivery, execution and economic lifecycle.

- `PENDING_DELIVERY` — decision saved locally; Telegram receipt not yet known.
- `DELIVERY_UNCERTAIN` — transport failed after acceptance became unknowable; do not
  retry blindly and do not create a paper fill.
- `DELIVERY_FAILED` — known failure; no paper fill.
- `EXPIRED` — quote or future-day eligibility expired; no paper fill.
- `ACKNOWLEDGED` — Telegram returned a concrete message receipt; position creation may
  proceed only under the already frozen v4 execution protocol.
- `OPEN` — validated simulated position is economically unresolved.
- `NO_FILL` — signal was acknowledged but executable constraints/captured capacity did
  not support a simulated fill.
- `WON`, `LOST`, `RESOLVED_PARTIAL` — only after v4 finality validation.
- `QUARANTINED` / `UNVERIFIED` — retained for audit but excluded from validated wins,
  losses, capital, P&L and ROI.
- `SKIPPED` — a research candidate failed the final semantic/temporal/price/exposure
  recheck before a validated paper position was opened.

The Telegram commands summarize these groups directly instead of exposing database
implementation details.
