# Gamma keyset schema compatibility for V11 development Shadow — 2026-10-04

Scope: nonfinancial V11 discovery only. This does not grant station certification, model authority, G3-L credit, live execution, or funding authority.

Observed live response from the already-reviewed public endpoint `https://gamma-api.polymarket.com/events/keyset` added one metadata member:
`"$schema": "https://gamma-api.polymarket.com/schemas/EventsKeysetListResponse.json"`.

The prior strict parser admitted only `events` and `next_cursor`, so it failed closed with `DISCOVERY_PAGE_ENVELOPE_OR_SCOPE`.

Compatibility rule:
- `$schema` is optional;
- if present, its value must exactly equal the observed Gamma EventsKeysetListResponse schema URL;
- every other unreviewed response member remains rejected.

Verification:
- `tests/test_v11_discovery.py`: 25 passed.
- Live anonymous Gamma replay on this exact worktree persisted one 10-event page, processed all 10 events, and wrote 57 total V11 records.
- The live replay remained `financial_authority=false` and stopped at the deliberately configured one-page bound, not due to parser or transport failure.
