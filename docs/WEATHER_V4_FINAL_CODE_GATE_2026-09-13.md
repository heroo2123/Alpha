# Weather corrective final code gate — 2026-09-13

This is a code-side release gate only. It is not a deployment record and it grants no real-money authority.

Current code gate:

- full repository CI must pass on Python 3.11 and 3.12;
- structural delivery remains disabled;
- indicative closing prices cannot settle paper positions;
- same-day three-layer captures remain excluded from validated P&L and Telegram trade delivery;
- deployment renderer and runtime attestation must agree on the canonical corrective entrypoint;
- database backup/restore verification must pass before host activation;
- actual VM unit/process/release identity must be attested after installation before the paper service is accepted.

The same-day three-layer research collector is allowed to run silently. Promotion of same-day alerts is a separate scientific acceptance decision and is not implied by deploying the paper runtime.
