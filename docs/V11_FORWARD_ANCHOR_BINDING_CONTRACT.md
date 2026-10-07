# V11 forward anchor binding: unused offline contract, version 1

`check_anchor_binding(anchor_bytes, request_bytes, snapshot_bytes)` compares three
separately supplied, synthetic byte strings. It has no I/O and returns only
`Refusal(code)`. Even an exact structural match returns `ANCHOR_UNAVAILABLE`.
This envelope version is independent of journal format version 1. No caller
field, fixture, digest, or exact match commissions an anchor.

## Canonical bytes and bounds

Each argument is one UTF-8 JSON object encoded exactly as
`json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
allow_nan=False).encode("ascii")`, with no newline or trailing bytes. Duplicate
keys, noncanonical ordering, whitespace, alternate Unicode or number spellings,
invalid JSON, and unknown or missing keys refuse. Booleans are not integers.
All ID values use 1–96 ASCII characters from `[A-Za-z0-9._:-]`. Digests are
exactly 64 lowercase hexadecimal characters. `version` is integer `1`.
The anchor, request and snapshot caps are respectively 16,384, 8,192 and
32,768 bytes. There are 1–16 ledgers and 0–32 child IDs. Dependency bytes are
0–4,096 bytes represented by lowercase even-length `dependency_hex`. Journal
sequence is 1–128; each ledger sequence is 0–256; custody generation is
1–(2⁵³−1). Child IDs and ledger IDs are unique and sorted by ASCII value.

## Exact objects

Every listed field is required. No other field is accepted.

* **Anchor:** `version`, `anchor_id`, `issuer_id`, `custodian_id`,
  `executable_sha256`, `policy_sha256`, `enrollment_id`,
  `authentication_format` (literal `external-v1`), `verification_root_id`,
  `request_sha256`, `snapshot_sha256`, and all frontier fields below.
  The format and root ID are descriptive placeholders; no signature or key is
  verified. A `commissioned` flag, public key, or self-attesting claim is an
  unknown field and refuses.
* **Expected request:** `version`, `request_id`, `namespace`, `plan_id`,
  `scope_key`, `event_id`, `interval_id`, `operation_id`, `admission_id`,
  `capture_id`, `child_ids`, `verifier_executable_sha256`,
  `verifier_policy_sha256`, `snapshot_sha256`, `expected_anchor_id`, and
  `expected_issuer_id`, `expected_custodian_id`,
  `expected_executable_sha256`, `expected_policy_sha256`,
  `expected_enrollment_id`, `expected_authentication_format`,
  `expected_verification_root_id`, and
  `expected_` forms of `boot_id`, `session_id`, `custody_generation`,
  `journal_id`, `journal_seq`, `journal_head`, `catalog_sha256`,
  `membership_sha256`. The expected request must be supplied independently;
  deriving it from the claimed anchor defeats the request-crossing check.
* **Snapshot:** `version`, all request target fields from `request_id` through
  `child_ids`, all frontier fields, `dependency_hex`, and
  `witness_history_sha256`. The dependency bytes are retained exactly. The
  witness digest is an opaque binding, not witness validation.
* **Frontier fields:** `boot_id`, `session_id`, `custody_generation`,
  `journal_id`, `journal_seq`, `journal_head`, `catalog_sha256`,
  `membership_sha256`, `ledgers`. Each ledger has exactly `ledger_id`,
  `genesis_hash`, `seq`, `prefix_hash`, `view_sha256`, `archive_sha256`.
  For sequence zero, prefix equals genesis. The membership digest equals the
  SHA-256 of canonical JSON for the sorted list of objects containing just
  each `ledger_id` and `genesis_hash`. This checks internal consistency only.
  The archive digest is always required, including for an empty archive;
  no missing sentinel or implied completeness is accepted.

The anchor's request and snapshot digests must equal the exact supplied byte
strings. The request's snapshot digest must equal those same snapshot bytes.
Every expected frontier field must equal the anchor's field; every full
frontier field, including the complete ledger list, must equal the snapshot's.
Every target field in the request must equal the snapshot's. Distinct boot,
session, custody generation, journal, catalog, ledger and target domains are
compared as distinct data; none is inferred from another. A changed byte
requires a new exact digest, but recomputing hashes cannot confer authority.

## Refusal order and limits

The parser checks anchor, then request, then snapshot, followed by anchor
schema, frontier structure, request and snapshot structure, digest bindings,
expected frontier equality, full snapshot frontier equality, and target
equality. The implementation's exact codes are `ANCHOR_BOUND`,
`REQUEST_BOUND`, `SNAPSHOT_BOUND`, `DUPLICATE_KEY`, `MALFORMED_JSON`,
`SCHEMA_KEYS`, `NONCANONICAL_ENVELOPE`, `UNSUPPORTED_VERSION`,
`SCHEMA_TYPE`, `GENERATION_INVALID`, `FRONTIER_INVALID`, `LEDGER_BOUND`,
`CATALOG_MEMBERS`, `MEMBERSHIP_MISMATCH`, `CHILD_BOUND`, `CHILD_ORDER`,
`DEPENDENCY_BOUND`, `DEPENDENCY_ENCODING`, `AUTH_FORMAT_UNSUPPORTED`,
`REQUEST_BINDING`, `SNAPSHOT_BINDING`, `EXPECTED_FRONTIER_MISMATCH`,
`SNAPSHOT_FRONTIER_MISMATCH`, `TARGET_MISMATCH`, and
`ANCHOR_UNAVAILABLE`. These codes describe syntax and equality only.

No journal or ledger bytes are supplied here. Their heads, view and catalog
digests are declarations, so this comparator cannot detect a jointly
re-hashed deleted tail or sibling, or prove completeness, semantic history,
clock bounds, lease continuity, or a CAS race. A separately trusted custodian
would have to produce one pinned complete snapshot and frontier; a separately
trusted verifier would have to derive and authenticate a reply bound to the
request, exact view, both frontiers and verifier executable/policy. The owner
would have to provision an authentication root outside caller control, verify
an actual signature or equivalent, retain immutable commitments outside the
ledger failure domain, and supply an independently authoritative current
high-water selection rule across reboot, restart and replacement. An old
authentic commitment alone cannot prove it is current. Complete relevant
catalog membership and dependency/witness history, trusted time and semantic
qualification need their own reviewed mechanisms. None exists in this slice.

There is no runtime integration, Coverage result, positive protected interval,
forward qualification, sample credit or financial authority. The existing
`verify_interval` and unconditional `_require_protected_interval` gate remain
separate and unchanged. Different-model exact review is required before any
integration; custody and owner commissioning require separate work.
