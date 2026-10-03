# Gate 3 readiness boundary implementation handoff

Status: **PROPOSED OFFLINE CANDIDATE**. No package was executed or qualified.
The independent design review permits only an offline proposal tool. A
different-model review of the exact code commit is still required before
integration; the design review did not approve this implementation or a live
admission policy.

## Exact code identity and scope

- Code and test commit: 91514ce35eee486ff3dfa0916cdd9f693be7d7e6.
- Code and test tree: 57e1a127b3999687186e184e2141044e73459837.
- Starting implementation worktree HEAD: faca37bf77f68d328564fa6208fd7e918f9f5cbf.
- Changed code: tools/v11_gate3_readiness_boundaries.py and
  tests/test_v11_gate3_readiness_boundaries.py. This handoff is a separate
  documentation commit so it can cite the exact code commit and tree.
- No existing checker, planner, caller, frozen constant or date binding was
  changed. The validator imports only hashlib, json, re and datetime. It
  has no file, socket, subprocess, provider, service or wall-clock access.

The only successful validator status is PROPOSAL_VALID_NOT_EXECUTABLE. Refusals
are PROPOSAL_REFUSED with bounded codes and no partial proposal. Every result,
including the output-bounds fallback, has execution_authority=false,
capture_eligibility=false, qualification_credit=0, g3l=NO_GO and
clock_qualification=false.

## Closed proposed schema and policy

validate(raw) accepts exact built-in bytes containing JSON or an exact
built-in dict tree. The root has exactly schema, mode, declared_build_ref,
evaluation, p1, capture, resources and clock. The schema is
ALPHA_V11_GATE3_READINESS_PROPOSAL_V1. Modes are P1_PREFLIGHT_PROPOSAL and
CAPTURE_PLAN_PROPOSAL. No arbitrary policy object or extension keys exist.

Evaluation has exactly utc, monotonic_us, host_id and boot_id. UTC uses
canonical YYYY-MM-DDTHH:MM:SS[.ffffff]Z. Fractional zero suffixes, leap
seconds, offsets, naive times and invalid dates refuse. Monotonic values are
exact nonnegative signed-64 integers. IDs use at most 128 ASCII letters,
digits, dot, underscore, colon or hyphen, starting with a letter or digit.
All these values are caller declarations, never acquired observations.

P1 requires exactly run_utc, start_utc, end_utc, campaign_id and
restriction_lineage_id; capture must be null. It checks evaluation < start <
end, width strictly greater than 60 seconds and at most 12,600 seconds, end
at most 86,400 seconds after evaluation, exact 00Z run on the start UTC date,
and run no later than evaluation. The 86,400-second horizon is **new proposed
planning policy**, not the capture run-age rule or proof of release. The
canonical proposal SHA-256 changes when its window changes. Campaign and
restriction lineage IDs remain in the digest and report, so a date change
cannot reset accounting or a hold.

Capture requires exactly target_local_date, campaign_id and
restriction_lineage_id; p1 and resources must be null. The validator
reproduces the public freeze_checklist schedule: preceding UTC date 00Z run,
14:00 capture start, 17:00 end, 18:00 decision and denominator 2713. Tests
compare those values with freeze_checklist and slot_inventory. Evaluation
must be before start and no earlier than the derived run. The common
24-hour proposal horizon is redundant for otherwise valid capture requests
under that same-day run rule. Capture does not use P1 resources, shrink its
denominator or change capture semantics.

P1 resources has exactly selected_fs_id, snapshot and reservation_view.
Snapshot fields are disk_total_bytes, disk_available_bytes,
memory_total_bytes, mem_available_bytes, fs_id, host_id, boot_id,
observed_utc, monotonic_us and observation_kind (SCENARIO or
LOCAL_DECLARED). Available cannot exceed total; filesystem, host and boot
must match; monotonic age must be 0–60,000,000 microseconds; observed UTC
cannot follow evaluation UTC. The 60-second age cap is **new proposed
policy**. Neither observation kind qualifies measurement or custody.

Reservation view has exactly complete=true, scope_ref and entries. Each
entry has distinct id, nonempty consumer_id, state, disk_bytes, memory_bytes
and covered_worst_case. MATERIALIZED_BEFORE_SNAPSHOT requires
covered_worst_case=true and is already in available bytes.
OUTSTANDING_NOT_IN_SNAPSHOT requires covered_worst_case=false and is debited
in full. Other or uncertain states, duplicate IDs, missing consumers,
incomplete views and mismatched references refuse. A nonempty supplied
scope reference is required even for zero entries; an empty file is not
proof of zero shared reservations. The view remains caller supplied and
unqualified. It cannot prove release, allocation, overlap, quota or
enforcement.

Exact built-in byte magnitudes are limited to 0..2**63-1. Sums and products
beyond that **new representation ceiling** refuse. P1 debits all outstanding
commitments, a new 64 MiB disk reservation and a conservative 128 MiB memory
working reservation. It requires at least 2 GiB disk and 512 MiB memory
afterward. Disk below 3 GiB is only a target shortfall. These computations
cannot establish host capacity or enlarge runtime limits.

Clock is null or a closed dossier: host/boot, recorder source/build
references, method version, raw calibration/sample references, parsed
calibration and ordered samples, and a detached review-link object. The
review link contains method/custody/report/terminal byte references, exact
host/boot, validity domain and raw/source digest bindings. Every reference
has exactly sha256, byte_length and bytes_hex. Lowercase hex decodes to
explicitly supplied immutable bytes and must match length and SHA-256.
The raw calibration and sample references additionally bind canonical JSON
of the parsed fields. Each sample has event kind, sequence, monotonic/UTC/
uncertainty microseconds, calibration ID and original calibration monotonic
time. The parser checks one original anchor, increasing sequence and
monotonic values, no future or over-60-second sample, uncertainty at most
one second, and wholly backwards UTC steps. It does not rebase samples or
refresh original calibration age.

Matching hashes and even supplied PASS text yield only
STRUCTURALLY_LINKED_UNQUALIFIED. There is no accepted drift envelope or
independent trust record in this slice. Forward drift, including cumulative
drift that passes adjacent checks, stays unqualified. A dossier adds
CLOCK_DRIFT_MODEL_UNSUPPORTED to blockers. The illustrative
conservative_stage_fit helper uses strict less-than for dispatch upper +
60 seconds + caller margin versus end; it cannot qualify that margin or
authorize dispatch. A later admission consumer must resolve independent
reviewer identity, accepted method, anchor, custody, raw bytes and drift
model outside this tool.

The report retains a caller-declared build digest and length only as
unqualified metadata. It does not attest loaded validator bytes. A null or
forged declaration leaves BUILD_UNATTESTED as a blocker.

The parser caps aggregate raw input at 1 MiB, nesting at 8, nodes at 4096,
array/dict items at 64, UTF-8 strings at 4096 bytes, reasons at 64 and
serialized output at 64 KiB. References share the input cap. Duplicate or
unknown keys, nonfinite/foreign types, subclasses, hostile mappings, huge
integers and oversized/deep input refuse. Explicit refusal codes include
SCHEMA, JSON_SYNTAX, DUPLICATE_KEY, INPUT_BOUNDS, TIME_SYNTAX, TIME_ORDER,
WINDOW_WIDTH, HORIZON, RUN_SCOPE, FUTURE_RUN, REPRESENTATION_OVERFLOW,
RESOURCE_INCONSISTENCY, RESOURCE_CONTEXT, RESOURCE_AGE, RESOURCE_FLOOR,
RESERVATION_VIEW_UNQUALIFIED, CLOCK_LINKAGE, CLOCK_CONTEXT,
CLOCK_CALIBRATION, CLOCK_MONOTONIC, CLOCK_REBASE, CLOCK_STEP, BUILD_BINDING
and OUTPUT_BOUNDS.

## Test record

All commands ran from this isolated worktree. The exact common command prefix
was PYTHONPATH=/tmp/alpha-v11-gate3-readiness-boundary-implementation-20261003
followed by /home/alphaadmin/AlphaV11_Dev/venv/bin/python. The exact
arguments and counts were:

1. -m pytest -q tests/test_v11_gate3_readiness_boundaries.py:
   **11 passed**.
2. -O -m pytest -q tests/test_v11_gate3_readiness_boundaries.py:
   **11 passed**, with pytest's expected warning that ordinary test assertions
   are ignored under -O. The suite includes an explicit exception-based
   contract check that survives -O.
3. -m pytest -q followed by these exact node IDs:
   tests/test_v11_gate3_evidence_preflight_checker.py::test_check_result_schema_rejects_third_outcome_value
   tests/test_v11_gate3_evidence_preflight_checker.py::test_check_result_schema_requires_reasons_iff_refused
   tests/test_v11_gate3_evidence_preflight_checker.py::test_json_parser_refuses_lone_surrogate_scalar_values_and_keys
   tests/test_v11_gate3_evidence_preflight_checker.py::test_nested_duplicate_and_nonfinite_json_are_rejected
   tests/test_v11_gate3_evidence_preflight_checker.py::test_rejects_json_exponent_overflow_as_nonfinite:
   **11 passed**.
4. -O -m pytest -q followed by the same five exact node IDs:
   **11 passed**, with the same pytest warning.

No package was installed; no service, provider or network request ran.

The new suite also calls the frozen checker with a self-contained synthetic
shifted-date input and confirms its changed-date refusals. Other existing
checker tests were excluded because their fixture helpers read an audit file
or private package outside this task's allowed inputs. No full suite or
private package test is claimed.

## Public source bindings and remaining gates

Design SHA-256:
87281d9fc67a4165c4aacd69288a17d3458f17ea9e236514fdbd8950461f3e7d.
Source manifest SHA-256:
13db7aaa71ef42c0e56ba0177ed9c051e3e9d441da4143206ffe724b18f9c23d.
Independent public design review SHA-256:
2552489eead5aac3a7aae6467165efd434e4879570a9b840cc8642118846fc76.
The review binds accepted design commit
13a9f785537c9ebb4842290f1d5aa23b37e1bfa7 and tree
abcf103f5114dc1d206b50e2f2ee225fc8247550.
The five public manifest inputs are:

| Public path | Bytes | SHA-256 |
| --- | ---: | --- |
| docs/V11_R09_GATE3_EVIDENCE_PREFLIGHT_PROTOCOL_20261002.md | 20274 | ae59812fa58ec41895b87408f0ea175988ae6d20a1f4d5dabcd96a01c2dd3a68 |
| docs/V11_R09_GATE3_COLLECTION_PROTOCOL.md | 20041 | da3144c558134e7bd6a06b5c3f298740e86cee13be7c54a9932204362c269524 |
| tools/v11_gate3_evidence_preflight_checker.py | 45681 | 6df49d57b6807d1b6d47075520d45c493f33550baaee1317d416e4d2d6347355 |
| tools/v11_r09_gate3_g3l_prep.py | 31205 | a5051a65aa219d40f5ac67acf9ca6f1e72839eb1d988ae5d08b2ea0565e89481 |
| tools/v11_gate3_preflight_attempt_model.py | 42781 | 4734e9a1386e5cb96a73fa3ba8959977f9756026d1adace2b985ba3cd388a463 |

The implementation cannot supply reviewed clock provenance, an accepted
drift model, trustworthy resources/reservations, storage custody, provider
release/access/path rights, original restriction reconciliation, review
cutoff, a qualified package or a live execution decision. A different-model
**exact-commit implementation review** is required before offline code
integration. Any executable policy/package, independent observations and
detached execution review require separate future work. There is no G3-L,
G3-E, SHADOW, score or funding progress here.
