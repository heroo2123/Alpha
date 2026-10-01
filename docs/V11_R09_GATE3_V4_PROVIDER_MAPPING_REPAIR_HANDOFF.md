# Gate 3 V4 provider mapping repair candidate

Branch: `r09-gate3-v4-mapping-repair-20261001`, based on `15f054f`. This is
an offline, nonfinancial validation candidate. It grants no G3-L or network
authority. Main and other worktrees were not changed.

The independent `15f054f` review found two defects. R1 is closed by exact
ordered component specifications: GEFS's adapter CGI directory/file grammar,
and ECMWF's `/forecasts/{date}/{cycle}z/{ifs|aifs-ens}/0p25/` model layout,
with separate `.grib2` FIELD and `.index` INDEX paths. Recomputed manifest
identities cannot make wrong literals, order, model, or suffix pass. Tests
compare ECMWF paths directly with `ECMWFRequest.url` and the index sibling;
GEFS paths are checked by the adapter's `validate_params` query grammar.
The shared IFS perturbed-object schedule test now uses two distinct byte
ranges for members 1 and 2 while preserving object/index/cache identities.

R2 is closed fail-closed. The fixture declares `SYNTHETIC_OFFLINE_ONLY`,
uses `.example.invalid` origins, and has fixed `/synthetic/` placeholders for
unsupported purposes. Arbitrary OBJECT_ID/METADATA/PROBE or GEFS INDEX paths
are rejected, even with endpoint/request/hash rebinding. Any real-origin
scope rejects GEFS's unproven direct GET mapping and unsupported purpose
contracts. The GEFS adapter uses a NOMADS CGI endpoint with query parameters;
this code does not claim the same layout works as an S3 object key. A separate
reviewed mapping and GET contract is needed before a real schedule can pass.
The existing dossier references remain opaque; they do not qualify origins.

Verification: focused V4 tests, 58 passed; explicit Gate 3 family, 394 passed.
`py_compile` for both changed Python files and `git diff --check` passed.
A broad `pytest -k 'gate3 or v11_r09'` collection imported an unrelated stale
`/tmp` worktree before tests ran, so the explicit Gate 3 files were used.
No provider, DNS, TLS, or HTTP request was made. Progress remains 91/200;
formal gate remains 1/50 NOT_READY_TO_FUND. Independent exact-commit review
and external G3-L review remain mandatory before any live use.
