# Brain/R09 IFS-AIFS native sampled-trajectory adapter — offline findings

Worker scope: SAFE NONFINANCIAL OFFLINE, no network/provider requests, no
credentials, no real source bytes/rights, no V10/Axiom/service/authority/order
changes. Base: `a1f8a57`. This document records a findings handoff only; it
grants no admission, review, or implementation credit and changes no code.

## Question investigated

Whether a distinct **source-native IFS/AIFS sampled-trajectory adapter** is
missing, and which part is already covered by the accepted Gate 2 synthetic
validator and Gate 3 decoder/collector. Here “adapter” can mean either raw
GRIB decoding or the later bridge from reviewed feature captures to typed
`R09_NATIVE_2T_TRAJECTORY_V1` examples; they have different gates.

## What exists today (read-only inspection)

1. **Gate 2 — offline synthetic contract and validator**
   (`tools/v11_trajectory_contract.py`, reviewed PASS at `1ab551d`, see
   `docs/V11_R09_GATE2_REVIEW_1ab551d.md`). `validate_example` hard-requires
   `evidence_class == 'SYNTHETIC'` (`REAL_ADAPTER_NOT_IMPLEMENTED`), and its
   `verify_synthetic_decoding` explicitly decodes a small JSON wire format,
   "not a GRIB parser or a real provider attestation." No real adapter is
   implemented or claimed here, by design.

2. **Gate 3 — main weather track, building the upstream capture and decoder
   surfaces:**
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
   - `docs/V11_R09_GATE3_COLLECTION_PROTOCOL.md` separates **G3-P protocol →
     G3-I collector → G3-L launch → G3-E corpus** from a later Gate 4 review.
     It says G3-E does not admit through the synthetic validator or authorize
     Gate 4 adapter/fit; Gate 4 must resolve the feature-capture-to-example
     bridge, actual labels, split clocks and any schema extension before real
     learner admission.
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

4. No file outside `tools/v11_trajectory_contract.py` and its tests
   constructs `ExtractionEvidence`/`TrajectoryPoint` from real bytes. The
   Gate 4 feature-capture-to-example bridge does not exist yet. The protocol
   reserves real learner admission for a later review after G3-E establishes
   eligible feature captures.

## Conclusion

A distinct real feature-capture-to-example adapter **is missing**, as the
synthetic validator and Gate 3 protocol state. The existing decoder and
collector are upstream inputs to that bridge, not the bridge itself. The
present offline task cannot truthfully complete or qualify the real bridge:

- Duplicating the actively reviewed Gate 3 decoder or collector would create a
  conflicting implementation of their upstream surfaces.
- The real bridge needs eligible capture bytes, operational-release and clock
  evidence, labels and split semantics. G3-L remains NO-GO and G3-E has not
  produced those inputs. Synthetic fixtures could test an interface but could
  not validate or independently qualify real admission.
- Gate 4 needs its own review after G3-E. Gate 3's frozen protocol gives no
  authority to admit real examples through the synthetic validator.

The real bridge remains an open Gate 4 implementation task. This worker made
no code changes because its required real inputs and review boundary are not
yet available.

## Recommendation

- Do not spin up a second parallel "native adapter" implementation effort;
  route any further source-native decoder/adapter work through the existing
  main Gate 3 owner and its established G3-P/G3-I/G3-L/G3-E review lineage
  (`docs/V11_R09_GATE3_*`), so there is exactly one reviewed implementation of
  `polymarket_scanner/v11/ecmwf_grib.py` and the collector.
- The distinct later item is the Gate 4 "feature-capture-to-example bridge"
  (binding real decoded `ExtractionEvidence`/`TrajectoryPoint` objects from
  `ecmwf_grib.py` output into Gate 2's `ExampleInputs`/`validate_example`).
  A real-admission implementation cannot be qualified before Gate 3 G3-E
  produces eligible captures (zero exist today). Keep interface ideas offline
  and separate from any real-admission claim.
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

The worker reran `tests/test_v11_trajectory_contract.py` with the development
venv and `PYTHONDONTWRITEBYTECODE=1`: **123 passed in 11.22s**, according to its
terminal-bound log. No code changed; this is regression evidence only.

## No claims made

No real admission, Gate 3 PASS, forward SHADOW, or score credit is claimed.
No status docs (`V11_ENGINEERING_PROGRESS.md`, `V11_REQUIREMENTS_MATRIX.md`,
`V11_WORK_CHECKPOINT.md`) were edited. No network, credentials, private
retained evidence, V10, Axiom, or financial/service/authority action occurred.
