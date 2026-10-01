# Exact-commit provider-mapping review: CHANGES_REQUIRED

Reviewed range: `f5f6cde..15f054fb1eb622a07b57aef1c68d93804aa48664`.
Candidate tree: `1a851c30971c563c19857a6a32297611fcb5e7f3`.
Worktree: `/tmp/alpha-v11-gate3-v4-provider-mapping-20261001`.
Independent review agent; candidate author reported Sonnet/high.

Read authoritative main `docs/V11_R09_GATE3_TRANSPORT_RUNTIME_DESIGN.md` (the actual design filename), main launch-readiness audit, candidate handoff, diff, and GEFS/ECMWF source adapters. This is an architecture/safety review of the mapping correction, not acceptance of live dossiers or a launch envelope.

## R1 — P2: Component counts do not constrain provider/purpose path structure

Location: candidate `tools/v11_r09_gate3_launch_v4.py:160` (`_check_required_kinds`, lines 160–168), called at lines 397–404. The check ignores literal content and component ordering. Accordingly, equal nonliteral counts certify arbitrary layouts, wrong model directories and wrong purpose suffixes. Full-validator probes, with ordinary endpoint/request/hash rebinding and unchanged evidence artifacts, ACCEPT:

- GEFS FIELD `/wrong/000/00/c00/00/20270228`.
- A scheduled IFS perturbed FIELD `/20270228/00z/aifs-ens/0p25/enfo/20270228000000-0h-enfo-ef.grib2`.
- A scheduled IFS INDEX ending `.grib2`, identical to its field object instead of the evidenced `.index` sibling.

Adapter evidence: `polymarket_scanner/v11/ecmwf_sources.py:79` includes the source model directory (`ifs` or `aifs-ens`); line 205 selects the `.index` sibling. GEFS filename/directory grammar is at `polymarket_scanner/v11/gefs_sources.py:94` and lines 113–114. Candidate test helper `tests/test_v11_r09_gate3_launch_v4.py:58` instead hardcodes `/forecast/0p25/` for both ECMWF models; assertions at line 701 reproduce this invented directory, so the suite does not compare exact adapter layouts.

Required correction: use reviewed provider/purpose layouts with fixed literal structure and ordering (or equivalently validated structured mappings), binding the ECMWF model and FIELD/INDEX suffix relationship. Test rendered paths against actual adapter construction and reject altered literals/order/model/suffix even when all manifest identities are recomputed. This is an incomplete mapping correction, not an origin escape or proof that the validator grants launch permission.

## R2 — P2: Unsupported source/purpose mappings are accepted rather than refused

Location: candidate `tools/v11_r09_gate3_launch_v4.py:104` (the deliberate general-validation policy through line 110) and lines 162–163 (`required is None: return`), with general acceptance at lines 392–404. The candidate explicitly leaves GEFS INDEX and every OBJECT_ID/METADATA/PROBE unconstrained beyond the general vocabulary. It also treats the GEFS NOMADS CGI directory/file naming as eligible for arbitrary origins, despite the absence of evidence establishing an S3 object layout.

Full-validator probes ACCEPT scheduled `/unreviewed/object_id`, `/unreviewed/metadata`, and `/unreviewed/probe` endpoints, and ACCEPT moving all GEFS mappings to `https://noaa-gefs-pds.s3.amazonaws.com` without changing any evidence artifact. These probes make no network calls. The GEFS adapter actually targets the CGI endpoint (`gefs_sources.py:26`) with directory/file query parameters (lines 113–117); it does not establish that direct S3 GET mapping. The candidate handoff itself acknowledges both evidence gaps.

The design's section 2 requires explicitly reviewed purpose contracts and permits refusal when evidence cannot support GET-only contracts. The audit says current source/purpose dossiers are absent. Opaque digest references and consistent self-authored endpoint tables do not enforce the requested unsupported-mapping refusal. This gap is partly carried forward from the prior generic schema; the new code explicitly retains it, so this candidate cannot be accepted as satisfying that review criterion.

Required correction: keep unsupported mappings explicitly unqualified and refuse their admission into a usable schedule until a separately reviewed mapping/contract is available. Do not invent S3/object/metadata/probe layouts to make fixtures pass. Preserve the distinction between a synthetic/offline fixture and a provider-qualified mapping, and keep external launch review mandatory.

## Passed checks and limits

- `tests/test_v11_r09_gate3_launch_v4.py`: **39 passed in 7.14s**, using project venv, `PYTHONDONTWRITEBYTECODE=1`, disabled pytest cache, and a `/tmp/alpha-v11-mapping-review-15f054f.*` basetemp.
- `git diff --check f5f6cde..15f054f`: clean.
- Shared ECMWF perturbed paths and object/index/cache identities are structurally preserved: allowed ECMWF value kinds do not embed member numbers, and control/perturbed stream/file-kind selection matches adapter code. The author's full schedule test confirms shared identities. However, contrary to its handoff claim, its two member requests both use the same `0..4194303` range (`request_for_slot` lines 339–340; test lines 758–763), so it does not demonstrate distinct ranges.
- Independent probes: baseline plus five counterexamples all returned a validator digest. The IFS counterexamples include scheduled IFS requests, and the unsupported-purpose counterexample includes an actual scheduled PROBE.
- All checks were local and synthetic. Candidate working tree remained clean. No provider/DNS/TLS/HTTP request, merge, publication, live authority or service changes occurred; V10/AxiomTrade were not touched.

Artifacts:
- `/tmp/alpha-v11-mapping-review-15f054f.probes.py`
- `/tmp/alpha-v11-mapping-review-15f054f.results.json`
- `/tmp/alpha-v11-mapping-review-15f054f.review.md`
