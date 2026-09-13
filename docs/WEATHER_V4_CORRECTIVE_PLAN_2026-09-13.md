# Weather LIVE PAPER v4 corrective pass — 13 Sep 2026

Base reviewed SHA: `c991f2632abe80d54046abd7e5d325a5382fd0a0`.

This branch is paper/research only. It does not deploy, start services, place orders,
load wallet authority, or promote real-money delivery.

## Implemented in the first corrective slice

- Strict live-paper temperature-contract admission gate: title/rule date consistency,
  exact trusted WRH host/station identity, supported inclusive bucket grammar,
  child-statistic consistency, unique token/condition identities and integer-domain
  partition checking.
- Structural guaranteed-basket Telegram emission disabled pending a replacement
  common-resolution proof.
- Future-day raw GEFS kept future-local-day only, with station-local eligibility
  rechecked again at dispatch and a midnight safety margin.
- Forecast cache keyed to immutable semantic identity rather than event ID alone.
- Returned forecast grid must remain near the settlement station.
- Exact CLOB token-to-YES/NO meaning and provider book timestamps checked at the
  v4 decision boundary; exact ask/tick/minimum order metadata is frozen.
- Final dispatch performs a new exact CLOB reprice; stale/moved candidates become
  explicit SKIPPED decisions rather than paper fills.
- Paper stake, quote/fill time, expiry and execution protocol are frozen before
  Telegram delivery. Pre-v4 history defaults to unverified/excluded performance.
- Captured top-of-book capacity is consumed once; a station-day exposure cap prevents
  repeated model votes from masquerading as independent trades.
- Ambiguous Telegram transport becomes DELIVERY_UNCERTAIN rather than blind retry.
- Settlement requires explicit resolved/settled status plus a coherent exact binary
  payout vector and exact condition/token/side meaning. Closed/proposed prices remain
  unresolved.
- Settlement scanning uses a persisted rotating cursor and lookup failures surface.
- `/status`, `/stats`, `/positions`, `/history` and `/recent` are rewritten around
  operator questions: what is open, what finished, wins/losses, money used, P&L,
  no-fill, skipped, uncertain and excluded rows.
- Three-layer same-day mathematical foundation added: accepted official extreme so
  far + non-authoritative near-term official/PWS diagnostic + remaining-hours member
  extreme. Live same-day emission remains disabled until source and remaining-hours
  adapters pass acceptance.

## Still gated before any trustworthy long prospective run

- Corrective WRH local-day request/parser integration and its six-zone/DST acceptance.
- Remaining-hours (not daily aggregate) ensemble/nowcast adapter and end-to-end
  observation-conditioned replay.
- Replacement proof for structural baskets if that lane is to return.
- Runtime/deployment attestation and paper-DB backup/restore acceptance on the actual
  VM, only when separately authorized.
- Fresh independent adversarial re-review of the final corrective SHA.
