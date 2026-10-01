# Exact-commit provider mapping repair review: PASS

Reviewed base: `15f054fb1eb622a07b57aef1c68d93804aa48664`.
Candidate: `23c11e059048257a284d514d3454b811b3866515`.
Candidate tree: `979440fec2daaf5e7802d0c3d5afad3639d3ab9d`.
Worktree: `/tmp/alpha-v11-gate3-v4-mapping-repair-20261001`.
Independent Astra review of the Sol repair; read-only candidate inspection.

No blocking findings in this three-file repair. R1 and R2 from the prior mapping review are closed for the offline scope. No merge, release, provider qualification, or live launch is accepted by this verdict.

## R1: exact provider/purpose path grammar — closed

`tools/v11_r09_gate3_launch_v4.py` now defines exact ordered component specifications and compares each evidenced mapping with its specification in `_check_mapping_spec`. Literal content, ordering, ECMWF model directory, `/forecasts` prefix, and FIELD versus INDEX suffix are fixed. The renderer agrees with the inspected `ECMWFRequest.url` and explicit `.index` sibling construction in `polymarket_scanner/v11/ecmwf_sources.py`. GEFS matches the inspected CGI directory/file grammar in `gefs_sources.py`; the code correctly does not claim this is a qualified direct GET endpoint.

Independently replayed the predecessor review's scrambled GEFS layout, IFS-to-AIFS model substitution, and INDEX-as-GRIB counterexamples against this exact commit. Endpoint IDs, request paths/object/index/cache identities, purpose totals, and schedule digest were rebound through the fixture helper and canonical manifest bytes regenerated. All three fail with `SOURCE_MAPPING_EXACT_PATH`. The IFS cases contain a scheduled IFS perturbed FIELD. The focused suite also rejects altered component order. Adapter comparison tests cover IFS/AIFS controls and perturbed members. The full-schedule shared-object test now assigns distinct ranges 0–4194303 and 4194304–8388607 to IFS members 1 and 2 while preserving shared object/index/cache identities; it passes.

## R2: unsupported purpose/origin admission — closed

The identity requires `mapping_scope`. Synthetic admission requires `SYNTHETIC_OFFLINE_ONLY`, a `synthetic_` pilot prefix, and exclusively `.example.invalid` origins. Unsupported GEFS INDEX and OBJECT_ID/METADATA/PROBE mappings have exact synthetic placeholder paths. Arbitrary paths fail even when endpoint/request/manifest identities are rebuilt. A real-origin mapping scope refuses GEFS direct GET and unsupported purpose contracts; opaque dossier references cannot override that decision.

Independent predecessor probes for arbitrary scheduled OBJECT_ID/METADATA/PROBE and guessed GEFS S3 now fail (`SOURCE_MAPPING_UNSUPPORTED` and `SYNTHETIC_ORIGIN`). Additional probes refuse an invented qualified scope, an unmarked synthetic pilot, a real ECMWF origin under synthetic scope, a suffix-lookalike origin, and real-origin provider scope with either normal or reordered source insertion. A scheduled fixed synthetic PROBE remains accepted as a positive control.

The current mandatory three-provider/all-purpose schema means **no `PROVIDER_REVIEW_REQUIRED` manifest can pass**. This is a deliberate fail-closed implementation of R2 pending separately reviewed contracts. It is not a usable provider launch package. Synthetic placeholders and paths must remain unqualified; future real-source work must change and independently review the necessary exact contracts and launch enforcement.

## Executed evidence and boundaries

- Focused command: `PYTHONDONTWRITEBYTECODE=1 /home/alphaadmin/AlphaV11_Dev/venv/bin/python -m pytest -q -p no:cacheprovider tests/test_v11_r09_gate3_launch_v4.py --basetemp=/tmp/alpha-v11-mapping-review-23c11e0-pytest`: **58 passed in 5.90s**.
- Independent inline adaptation of the preserved predecessor probes plus seven scope/origin cases: **13 expected outcomes**, comprising **11 refusals and 2 positive controls**. Five previously accepted counterexamples all refuse.
- `git diff --check 15f054f..23c11e059048257a284d514d3454b811b3866515`: passed.
- Candidate HEAD/tree stayed exact and `git status --porcelain=v1` stayed empty after verification. No candidate or main file was edited. Test fixtures were disposable `/tmp` files; bytecode/cache writes were disabled. The author's 394-test family result was read but not independently repeated or claimed here.

Read authoritative main checkpoint, requirements matrix and engineering progress current entries, the complete prior mapping review and its preserved probes, runtime design sections 1–2 and relevant runtime obligations, launch-readiness audit, candidate handoff/diff, and actual source adapters. Scope is this mapping repair only; the independent slice-3 repair and combined newer-main reconciliation remain separate gates.

All probes were local and synthetic. No DNS/TLS/HTTP/provider request, merge, publication, service, authority, credential, V10 or AxiomTrade action occurred. G3-L remains NO-GO; 91/200 (45.5%), formal 1/50, NOT_READY_TO_FUND remain unchanged. External exact-manifest launch review and genuine source/restriction/clock/storage/decoder evidence remain required.
