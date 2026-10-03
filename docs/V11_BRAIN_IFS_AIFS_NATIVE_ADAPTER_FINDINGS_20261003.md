# Brain/R09 IFS-AIFS native sampled-trajectory adapter — offline findings, no code changes

Worker scope: SAFE NONFINANCIAL OFFLINE, no network/provider requests, no
credentials, no real source bytes/rights, no V10/Axiom/service/authority/order
changes. Base: `a1f8a57`. This document records a findings handoff only; it
grants no admission, review, or implementation credit and changes no code.

## Question investigated

Whether a distinct **source-native IFS/AIFS sampled-trajectory adapter** (code
that decodes actual IFS/AIFS bytes into the `R09_NATIVE_2T_TRAJECTORY_V1`
contract's typed objects) is genuinely missing, as opposed to already covered
by the accepted Gate 2 synthetic validator.

## What exists today (read-only inspection)

1. **Gate 2 — offline synthetic contract and validator**
   (`tools/v11_trajectory_contract.py`, reviewed PASS at `1ab551d`, see
   `docs/V11_R09_GATE2_REVIEW_1ab551d.md`). `validate_example` hard-requires
   `evidence_class == 'SYNTHETIC'` (`REAL_ADAPTER_NOT_IMPLEMENTED`), and its
   `verify_synthetic_decoding` explicitly decodes a small JSON wire format,
   "not a GRIB parser or a real provider attestation." No real adapter is
   implemented or claimed here, by design.

2. **Gate 3 — main weather track, already building exactly this adapter
   surface, under active independent review:**
   - `tools/v11_r09_gate3_collector.py` (G3-I): a bounded, offline,
     network-free capture-manifest/attempt-ledger/budget-tracker, whose own
     docstring states it builds "a native IFS/AIFS three-hour request path
     kept separate from the production `ECMWFRequest`."
   - `polymarket_scanner/v11/ecmwf_grib.py`: a real, native ECMWF GRIB
     decoder — template 5.42 CCSDS path, ECMWF-specific
     run/member/step/2m-temperature/grid checks, release-header signature,
     and exact index/byte-range selection distinguishing IFS/AIFS
     control vs. perturbed layouts (confirmed in
     `docs/V11_R09_GATE3_DECODER_SOURCE_OFFLINE_ASSESSMENT_20261001.md`).
   - `docs/V11_R09_GATE3_COLLECTION_PROTOCOL.md` defines the explicit gate
     sequence: **G3-P protocol → G3-I collector → G3-L launch → G3-E corpus
     → Gate 4 real adapter/fit**, and states plainly: "G3-E does not admit
     anything through Gate 2's synthetic validator or authorize Gate 4
     adapter/fit. ... A later Gate 4 review must resolve the
     feature-capture-to-example bridge, actual labels, split clocks and any
     schema extension before real learner admission."
   - Dozens of independent review rounds already exist under
     `docs/V11_R09_GATE3_*` (A7 decoder qualification, decoder-build,
     decoder-point-of-use, collector, G3-L reconciliation/identity audits),
     most dated 2026-10-01/02, i.e. in-flight as of this investigation.

3. **Current main Gate 3 status** (per `a1f8a57`'s own diff, read-only):
   `G3-L NO-GO`, zero real examples/captures, no provider request made,
   `91/200, formal 1/50; NOT_READY_TO_FUND`. The decoder/source assessment is
   explicitly `BLOCKED for CCSDS integration and G3-L` pending ecCodes
   build/resource qualification and source-dossier review — i.e. the native
   decoder exists but is not yet qualified for real use, and that
   qualification work is actively owned elsewhere.

4. No file outside `tools/v11_trajectory_contract.py` and its test
   constructs `ExtractionEvidence`/`TrajectoryPoint` from real bytes — the
   "feature-capture-to-example bridge" the protocol reserves for Gate 4 does
   not exist yet. That is intentional, not an oversight: the protocol forbids
   it before G3-E produces real eligible captures.

## Conclusion

A distinct source-native IFS/AIFS adapter is **not an unaddressed gap
separate from current work** — it is precisely the real decoder/collector
surface (`ecmwf_grib.py`, `v11_r09_gate3_collector.py`) that the main weather
Gate 3 track is already building and independently reviewing, gated by its
own frozen sequence (G3-P/G3-I/G3-L/G3-E before Gate 4). Building a second,
parallel "adapter" in this isolated worker would either:

- **Duplicate** the actively in-flight, higher-priority main Gate 3 decoder
  and collector work on the identical contract (`R09_NATIVE_2T_TRAJECTORY_V1`)
  and identical decoder file (`polymarket_scanner/v11/ecmwf_grib.py`), risking
  a second conflicting implementation of the same surface reviewed under a
  different worktree/commit lineage; or
- **Require unavailable real source bytes/rights** to do honestly — a
  genuine "source-native" decoder must ultimately decode real ECMWF
  GRIB bytes and bind real operational-release evidence, which Gate 3 (G3-L
  launch, G3-E corpus) has not yet produced, and this worker is explicitly
  barred from network/provider access and real evidence; or
- **Jump the frozen gate sequence**, building Gate 4's
  feature-capture-to-example bridge before Gate 3 establishes any real
  eligible example, which `docs/V11_R09_GATE3_COLLECTION_PROTOCOL.md`
  explicitly forbids ("G3-E does not ... authorize Gate 4 adapter/fit").

No implementation gap exists that this offline, no-network, no-real-bytes,
bounded-resource worker can safely and non-duplicatively fill. Per this
task's own branching instructions, no code changes are made.

## Recommendation

- Do not spin up a second parallel "native adapter" implementation effort;
  route any further source-native decoder/adapter work through the existing
  main Gate 3 owner and its established G3-P/G3-I/G3-L/G3-E review lineage
  (`docs/V11_R09_GATE3_*`), so there is exactly one reviewed implementation of
  `polymarket_scanner/v11/ecmwf_grib.py` and the collector.
- The only legitimately open, distinctly-scoped item visible from this
  inspection is the Gate 4 "feature-capture-to-example bridge" itself
  (binding real decoded `ExtractionEvidence`/`TrajectoryPoint` objects from
  `ecmwf_grib.py` output into Gate 2's `ExampleInputs`/`validate_example`).
  That bridge is explicitly **not implementable or reviewable before Gate 3
  G3-E produces real eligible captures** (zero exist today), so it is not
  actionable yet either, by the protocol's own terms.
- If a future worker is assigned this area again, first check
  `docs/V11_R09_GATE3_COLLECTION_PROTOCOL.md` Gate status and
  `docs/V11_R09_GATE3_*` for whether G3-E has produced real eligible
  captures; only then would a Gate 4 bridge task become genuinely actionable.

## Commands run (read-only; no code changed)

```sh
grep -rln "ExtractionEvidence\|TrajectoryPoint" --include="*.py" .
grep -rln "GRIB\|grib2\|real_adapter\|REAL_ADAPTER\|source[_-]native" --include="*.py" tools/ tests/
git show a1f8a57 -- docs/V11_ENGINEERING_PROGRESS.md docs/V11_WORK_CHECKPOINT.md
```

No tests were run because no code was changed; the existing Gate 2 suite
(`tests/test_v11_trajectory_contract.py`) and Gate 3 suites remain untouched
and their prior review status stands.

## No claims made

No real admission, Gate 3 PASS, forward SHADOW, or score credit is claimed.
No status docs (`V11_ENGINEERING_PROGRESS.md`, `V11_REQUIREMENTS_MATRIX.md`,
`V11_WORK_CHECKPOINT.md`) were edited. No network, credentials, private
retained evidence, V10, Axiom, or financial/service/authority action occurred.
