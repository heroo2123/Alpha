# B1 closure-specification checker: scope and limits

Status: **offline engineering-proposal checker; independent implementation
review pending**. Authored on main `1f993fb`, 2026-10-02, as the B1 slice
authorized by
[the A4 bootstrap design](V11_R09_GATE3_A4_BOOTSTRAP_DESIGN_20261002.md) after
its independent
[architecture-and-B1-plan review](V11_R09_GATE3_A4_BOOTSTRAP_DESIGN_REVIEW_ae0c772.md)
(`PASS_IN_SCOPE`, `docs/V11_R09_GATE3_A4_BOOTSTRAP_DESIGN_REVIEW_ae0c772.verdict.json`).
That review qualified only the architecture and this plan, explicitly not A4
itself. This document is the scope boundary for
`tools/v11_r09_gate3_a4_closure_spec.py` and
`tests/test_v11_r09_gate3_a4_closure_spec.py`; it does not re-litigate the
design, does not accept A2/A3, and does not grant A4 PASS.

## What this checker is

A pure, bounded, in-process structural validator for one kind of input: a
single bounded raw JSON byte string describing a proposed bootstrap/native
closure (the artifacts, their dependency graph, declared bootstrap/loader/
data-selection rules, unresolved obligations and A2/A3/MEMFS evidence
references). It has no filesystem discovery, no subprocess, no native load, no
import from any proposed runtime image, and no provider/network access. Given
the same bytes it always returns the same result.

It directly addresses both LOW findings from the design review:

- Raw bytes, not an already-constructed object, are the input. `parse_strict`
  rejects duplicate object keys and nonfinite numeric literals (`NaN`,
  `Infinity`, `-Infinity`) during parsing, before any structural check runs,
  so duplicate keys cannot silently collapse and infinities/NaN cannot reach
  a bound comparison.
- Declared-graph reachability (`validate_structure`'s dependency-graph walk)
  is tested, and documented, as self-declared-graph consistency only. Tests
  with dangling endpoints, unreachable declarations, and synthetic known-edge
  fixtures (including a deliberate benign cycle) probe the checker's own
  refusals. None of them, including a fully accepted synthetic proposal,
  establish that a real native transitive closure is complete.

## What it is not, and never produces

- Not a production lock, not an acceptance-token producer, not an A8/G3-L
  converter, and not a native-execution path. It does not run, fork, `dlopen`,
  or import any artifact it is told about.
- Not evidence toward A2/A3. The `evidence_refs` field accepts `A2`/`A3`/
  `MEMFS` references by shape only (category, a bounded locator string, and an
  optional SHA-256-shaped pointer) — the checker never opens, hashes, or
  otherwise verifies the referenced document.
- Every result — a parse-time rejection, a structurally invalid proposal, or
  a structurally complete synthetic proposal — unconditionally carries
  `qualification="UNQUALIFIED"`, `launchable=False`, `a4_pass=False`. There is
  no code path that produces any other value for these three fields.

## Bounds enforced

Raw input is capped at 256 KiB; container nesting at 16 (checked by a
bracket-depth pre-scan before the JSON parser runs, so a pathological deeply
nested document cannot reach the recursive parser at all); strings at 512
UTF-8 characters with no NUL byte; up to 512 artifacts, 4096 dependency edges,
and 512 entries in any other bounded list. Per-artifact size is capped at
2**40 bytes and the declared total across all artifacts at 2**45 bytes — kept
strictly below `512 * 2**40` so the total-size bound is actually reachable
within the artifact-count bound, rather than being unreachable dead logic.
Integers are checked with `isinstance(x, int) and not isinstance(x, bool)` so
a JSON `true`/`false` can never satisfy an integer field.

## Schema (`ALPHA_V11_A4_CLOSURE_SPEC_V1`)

The top-level object and every nested object use an exact, closed key set;
unknown or missing keys are rejected. Required top-level fields:

- `schema`: must equal `ALPHA_V11_A4_CLOSURE_SPEC_V1`.
- `entrypoint_id`: must reference a declared artifact whose `role` is
  `bootstrap`.
- `artifacts`: 1–512 entries, each `{id, path, role, kind, sha256,
  size_bytes}`. `id` is a bounded token; `path` must be relative with no `..`
  or empty segments (traversal refused) and no leading `/`; `role` is one of
  `bootstrap`/`runtime`/`test`; `kind` is one of `python`/`native`/`data`;
  `sha256` must be 64 lowercase hex characters (format only — never checked
  against real bytes); `size_bytes` is a bounded non-negative integer.
  Artifact `id` and `path` must each be unique; two artifacts sharing the
  same `(sha256, size_bytes)` pair are rejected as an ambiguous hash-alias
  collision, since B1 does not model legitimate content deduplication.
- `dependencies`: up to 4096 `{from, to}` edges between declared artifact
  ids. Dangling endpoints, self-loops, and exact duplicate edges are
  rejected. Genuine multi-node native cycles (e.g. two libraries that depend
  on each other) are accepted and reported via `cycles_detected`, not
  treated as errors.
- `unresolved_obligations`: 1–512 bounded non-empty strings, deduplicated —
  a proposal must explicitly name what remains unresolved; it cannot declare
  zero.
- `evidence_refs`: 1–512 entries, each `{category, locator, sha256}` with
  `category` in `A2`/`A3`/`MEMFS`. At least one reference in each of the
  three categories is required. `sha256`, if not `null`, must be
  hash-shaped.
- `bootstrap_declaration`: `{entry_artifact_id, trust_anchor, stages}`.
  `entry_artifact_id` must equal the top-level `entrypoint_id`. `stages` is
  1–7 unique values drawn from `S0`–`S6`, naming stages from the design's
  [required launch sequence](V11_R09_GATE3_A4_BOOTSTRAP_DESIGN_20261002.md#required-launch-sequence-future-implementation)
  as labels only — declaring a stage name asserts nothing about it being
  implemented or reviewed.
- `interpreter_loader_declaration`: `{interpreter_artifact_id, loader_rules}`.
  The referenced artifact must have `role=runtime` and `kind` in
  `python`/`native`. `loader_rules` is 1–64 bounded unique strings.
- `data_selection_declaration`: `{data_artifact_ids, selection_rule}`. Every
  referenced id must exist and have `kind=data` and `role=runtime`, so selected
  data remains subject to declared-graph reachability.

Declared-graph reachability is computed from the validated dependency edges
starting at `entrypoint_id`. Any declared `bootstrap`- or `runtime`-role
artifact not reachable in that graph is rejected as
`UNREACHABLE_DECLARED_ARTIFACT`; `test`-role artifacts are exempt, since a
test harness is not part of the enforced bootstrap/native closure. This
reachability check, and every other check in this module, is graph/shape
consistency against the submitted bytes only.

## Relationship to B2–B4

This slice resolves none of the open items the design assigns to later
slices: accepted A2 lineage and A3 reconstruction (B2), a reviewed trusted
bootstrap/backend build and capability feasibility (B3), or actual runtime
mappings, MEMFS selection and A7 integration under independent exact review
(B4). A2/A3 remain UNQUALIFIED, A4 remains OPEN, A8 remains UNQUALIFIED, and
G3-L remains NO-GO. No existing A4/A7/A8 file is modified by this change.
