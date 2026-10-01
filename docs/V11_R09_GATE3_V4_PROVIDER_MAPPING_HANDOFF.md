# Gate 3 V4 provider-mapping correction: candidate, not merged

This is an **author candidate only**, built in the isolated worktree
`/tmp/alpha-v11-gate3-v4-provider-mapping-20261001` (branch
`r09-gate3-v4-provider-mapping-20261001`, base `f5f6cde`). It grants no G3-L,
network, SHADOW, learner, promotion, or financial permission, and does not
touch main, slice-3 composition files, V10, AxiomTrade, credentials, or
protected authority. No provider/DNS/TLS/HTTP request was made.

## Problem addressed

The independent [launch-readiness audit](V11_GATE3_LAUNCH_READINESS_AUDIT_20261001.md)
(section 2) found that `tools/v11_r09_gate3_launch_v4.py` could not represent
real provider path layouts: it required exactly one literal `{run}`,
`{member}` and `{hour}` token per template, then formatted raw integers
directly with `str.format`. That cannot express:

- GEFS's zero-padded, control-vs-perturbed member naming (`gec00` vs.
  `gep01`..`gep30`) and zero-padded forecast hour, evidenced in
  `polymarket_scanner/v11/gefs_sources.py` `field_request()`.
- ECMWF IFS/AIFS's formatted date/cycle, a control-vs-perturbed stream and
  file-kind, and — critically — that perturbed members (1..50) are
  **multiplexed into one shared file per run/step**, with no member number
  anywhere in the path; each member is distinguished only by its own
  Range request against a shared index, evidenced in
  `polymarket_scanner/v11/ecmwf_sources.py` `ECMWFRequest.url` /
  `ECMWFRequest.selectors` / `plan_ranges`.

## What changed

Both changed files are in this worktree only: `tools/v11_r09_gate3_launch_v4.py`
and `tests/test_v11_r09_gate3_launch_v4.py`. `tools/v11_r09_gate3_launch.py`
(V3) and every other module are untouched, preserving the design's own rule
that a V4 change can never silently alter V3's already-reviewed behavior.

Every `path_template: str` field (top-level per-source, per-purpose mapping,
network allowlist mirror, and endpoint-table entry) became `path_spec: list`:
an ordered list of typed components `{kind, value}` drawn from a small closed
vocabulary (`PATH_COMPONENT_KINDS`). `value` is only used for `LITERAL`
(a bounded, regex-checked, `..`-free literal string); every other kind is a
fixed, reviewed, total function of `(provider, run_utc, member, hour)` with
no caller-supplied format string — there is no generic substitution point.

- A provider's mapping may only use its own allowed kinds
  (`PROVIDER_PATH_KINDS`): `GEFS_PATH_KINDS` has no ECMWF kind and vice
  versa, so an IFS/AIFS mapping cannot structurally reference a member
  number — the multiplexed shared object identity for perturbed members
  falls directly out of the vocabulary, not a special case layered on top.
- `REQUIRED_PATH_KIND_COUNTS` pins the exact evidenced non-literal
  component multiset for the two purposes this repository has real adapter
  code for: GEFS `FIELD`, and IFS/AIFS `FIELD`/`INDEX` (its explicit
  `.index` sibling, never a runtime-appended suffix). GEFS's directory and
  `tHHz` filename fragment both need the cycle hour (`RUN_CYCLE_HH`: 2);
  ECMWF's directory and filename both need the stream literal
  (`ECMWF_STREAM`: 2). `OBJECT_ID`/`METADATA`/`PROBE` get only the general
  kind-vocabulary and renderability checks, nothing stronger, since no real
  endpoint shape for those purposes is evidenced in this repository —
  this correction does not invent a dossier for them.
- `_render_path` renders a spec against a representative `(run, member,
  hour)` and still runs the existing `_canonical_request_path` check (no
  `..`, no `//`, exact `urljoin` round-trip) plus a whole-path charset
  check, so the previous origin/path safety invariants are preserved, not
  loosened.
- The `sources` validation loop now renders every mapping across the full
  declared member/hour domain (control, highest perturbed member, first and
  last native hour) before accepting a candidate, so a spec that fails to
  render for some in-domain value cannot pass by only being tried on a
  happy path.
- Object/index/cache identity derivation, the frozen endpoint table,
  control-domain binding, the origin allowlist (first-use order), the
  per-purpose response-contract/resource/denominator checks, and the
  crash/denial-adjacent groups (`runs_and_slots`, `network` policy,
  `limits`, `runtime`, `clocks_and_receipts`, `accounting`) are all
  byte-for-byte unchanged except for the renamed field.

This is a **representation correction**, not a loosening: the old schema's
uniform `{run}×1, {member}×1, {hour}×1` requirement is replaced by *stronger*,
provider/purpose-specific requirements where real evidence exists, and only
left general (not invented) where it does not.

## Tests

`tests/test_v11_r09_gate3_launch_v4.py`: 39 passed (was 28; the fixture and
every test that built or mutated a `path_template` string were ported to
`path_spec` lists; `request_for_slot`/`_rebind_endpoints_and_requests` now
call `_render_path`). 11 new tests added:

- `test_gefs_field_path_uses_control_vs_perturbed_naming_and_padding` — exact
  rendered strings for control (`gec00`) vs. two perturbed members
  (`gep05`, `gep30`) and padded hours (`f003`, `f072`); all three distinct.
- `test_ecmwf_field_path_distinguishes_control_but_multiplexes_perturbed_members`
  — exact rendered strings for IFS control vs. perturbed and AIFS
  control/perturbed; asserts member 1 and member 50 render **identically**
  (the multiplexed object) while control differs.
- `test_ecmwf_index_path_is_explicit_sibling_not_derived_suffix` — FIELD and
  INDEX specs share every component except the final literal suffix.
- `test_multiplexed_ecmwf_perturbed_members_share_object_identity_in_full_schedule`
  — end-to-end `validate_manifest_v4` PASS with two distinct IFS
  perturbed-member FIELD schedule requests at the same run/hour sharing one
  `object_id`/`index_id`/`cache_id`/rendered `path` (and therefore one set
  of overhead prerequisites) while keeping distinct `request_id`,
  `slot_index`, and byte range — the real shared-file/distinct-Range shape.
- `test_gefs_only_kind_rejected_in_ecmwf_mapping`,
  `test_unknown_path_component_kind_rejected`,
  `test_literal_component_cannot_encode_path_traversal`,
  `test_non_literal_component_cannot_carry_asserted_value`,
  `test_required_kind_count_enforced_for_evidenced_gefs_field_path`,
  `test_required_kind_count_enforced_for_evidenced_ecmwf_field_path`,
  `test_field_mapping_path_spec_must_match_source_top_level_spec` — rejection
  of a cross-provider kind, an unknown kind, `..` in a literal, a fabricated
  value on a non-literal component, a dropped required component on each
  evidenced provider shape, and a per-purpose FIELD mapping diverging from
  the source's own top-level spec.

Full Gate 3 family (`pytest -k "gate3 or v11_r09"`, project venv at
`/home/alphaadmin/AlphaV11_Dev/venv`): **375 passed**, 0 failed (was 364;
+11 matches the new tests, no regressions). `python3 -m py_compile` on both
changed files: clean. `git diff --check`: clean (no whitespace errors).
`git status`/`git diff --stat`: only the two files above changed.

## Open evidence gaps (unchanged by this slice, not invented here)

This slice is a schema/validation correction only. It does **not** supply,
verify, or claim verification of:

- Real source dossiers, licence/access evidence, or exact current-run
  paths/releases for GEFS, IFS, or AIFS — `sources[...]['dossier']` etc.
  remain opaque reviewed-artifact references, same as before.
- The `OBJECT_ID`/`METADATA`/`PROBE` endpoint shapes for any provider — no
  real adapter code evidences these in this repository, so they stay under
  the general (not the evidenced-exact) checks, deliberately.
- Whether the GEFS component vocabulary is accurate for the *actual* S3
  `noaa-gefs-pds` key layout the addendum proposes (as opposed to the
  NOMADS CGI directory/file naming this repository's `gefs_sources.py`
  evidences, which this slice reused as the closest available in-repo
  evidence for the equivalent S3 key convention per the addendum's own
  `pgrb2ap5` reference) — that mapping still needs independent dossier
  review before any real request.
- Decoder qualification, prior ECMWF restriction/denial resolution, clock
  or storage qualification, or any other launch blocker listed in the
  audit — all remain exactly as that document describes them.

## Next steps

1. Independent different-model exact-commit review of this candidate
   against the transport/runtime design and the audit's section 2 findings.
2. Newer-main reconciliation before any integration decision.
3. Resolve the dossier/evidence gaps above under their own review before
   this mapping is used in a real (still not yet authorized) preflight.

GATE3_MAPPING_CANDIDATE_READY
