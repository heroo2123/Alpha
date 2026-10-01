# Gate 3 decoder/source compatibility: offline assessment

**Status: BLOCKED for CCSDS integration and G3-L.** This assessment uses only
repository code, documents, synthetic GRIB fixtures and the local test venv.
It neither changes the Gate 3 decoder nor qualifies a provider response.

## What the local code proves

- `polymarket_scanner/v11/ecmwf_grib.py` has an ECMWF-specific template 5.42
  path. `_ccsds_values` uses ecCodes, checks edition, template, value count,
  `Ni`/`Nj`, absent bitmap and `grid_ccsds`, fully decodes at most one bounded
  field, then requires byte-for-byte re-encoding before selecting a value.
  `decode_stations` separately checks ECMWF run/member/step, 2 m temperature,
  regular 0.25-degree grid, release-header signature and source request shape.
- `tools/v11_r09_gate3_offline_io.py` has a separate full-grid station decoder.
  It checks the frozen section 1–6 SHA-256 map, envelope, count, grid and
  absent bitmap, but currently supports only simple and IEEE packing. Its
  docstring correctly keeps semantic source pins outside the function.
- The existing GEFS adapter's `grib_fields.py` accepts only 25-point CGI
  subregions. It is not a full-field S3 decoder or evidence for changing the
  full-grid GEFS path. The ECMWF adapter uses exact index selection and byte
  ranges, with distinct IFS/AIFS control and shared perturbed-file layouts.
- Local synthetic tests show that genuine template 5.42 bytes from ecCodes
  decode through the ECMWF path for IFS and AIFS, while Gate 3 rejects the same
  bytes even with matching fixture-derived section hashes. A mismatched
  section-5 hash and a bitmap also fail before the packing gate. Existing model
  panel tests cover self-consistent truncated CCSDS and native-library failure.

Those checks establish a reusable algorithm and a fail-closed boundary. They
do not establish that the ecCodes build, its data/definitions, resource usage,
or current-run source identities are qualified for Gate 3. The local test venv
reports Python `eccodes` 2.48.0, `eccodeslib` 2.49.0.30 and ecCodes API 2.49.0;
its definitions and samples report `/MEMFS` paths. These are observations, not
frozen build identities. The repository requirements do not pin ecCodes. The
test uses `pytest.importorskip`, so a skip is never a qualification result.

## Evidence and work needed before an integration candidate

1. **Source dossier and exact run.** Independently review operational release
   documentation and effective run interval for IFS and AIFS. Bind dataset,
   centre/subcentre, tables, process/status, product template/type, 2 m
   temperature parameter/level, member numbering, native hours, grid and
   packing signatures separately for control and perturbed fields. Compare
   these decoded fields to exact GRIB headers; a release-signature hash alone
   cannot attest the vendor release. Include licence/access and restriction
   lineage. For each selected run, freeze index bytes/hash, object path/ETag,
   object size, selected row and exact single range before acquisition.
2. **Independent section pins.** Supply section 1–6 SHA-256 values in the
   reviewed, immutable launch package, independently of the body being decoded.
   Keep all six checks ahead of CCSDS invocation. Deriving pins from a received
   field, as tests do for fixture construction, would void the provenance gate.
   Explain and review how exact current-run pins are obtained without assuming
   a later capture is already authorized; use the protocol's separate preflight
   approval if new provider evidence is required.
3. **Exact decoder/dependency build.** Freeze actual repository commit/tree and
   decoder source-file SHA-256, plus the Python wrapper, native ecCodes library,
   all loaded native dependencies and definitions/samples data with reproducible
   lock/build artifacts and hashes. Verify the loaded artifacts against those
   frozen identities at the point of use; an API version or wheel version string
   alone is insufficient, especially with embedded `/MEMFS` definitions.
   Independently review platform/ABI and the decoded capacity/reservation under
   the Gate 3 memory floor. No package install or build change is implied here.
4. **Small Gate 3 integration after 1–3.** Permit template 5.42 only for the
   separately qualified ECMWF source signatures, after current section,
   bitmap, grid, count, distance and resource checks. Reuse the full-decode and
   exact-reencode integrity path; map every native/metadata/integrity failure to
   an explicit Gate 3 failure. Keep GEFS CCSDS, complex/JPEG packing, bitmap,
   unknown templates and changed release/grid signatures fail closed. Add
   adversarial tests for wrong build, source and section pins; malformed CCSDS,
   count/metadata mismatch, self-consistent truncation, re-encode mismatch,
   native absence/failure, resource exhaustion and cross-provider substitution.
5. **Separate review.** Independently review exact integration commit/tree,
   build/source artifacts, tests and resource measurements. Bind their digests
   in the eventual G3-L package. Synthetic passing tests or this note do not
   grant launch, capture, learner, SHADOW or financial authority.

The [launch-readiness audit](V11_GATE3_LAUNCH_READINESS_AUDIT_20261001.md),
[collection protocol](V11_R09_GATE3_COLLECTION_PROTOCOL.md),
[launch addendum](V11_R09_GATE3_LAUNCH_CONTRACT_ADJUDICATION.md) and
[offline I/O handoff](V11_R09_GATE3_OFFLINE_IO_HANDOFF.md) remain the governing
requirements. Slice-3 repairs and provider mapping are separate work.
