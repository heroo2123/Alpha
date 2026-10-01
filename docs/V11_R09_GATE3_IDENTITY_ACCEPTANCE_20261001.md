# Gate 3 source, build and runtime identity acceptance criteria

**Decision: criteria defined; no source/build/runtime qualification granted.
G3-L remains NO-GO.** Astra/high offline analysis against clean main
`e754c6cad5ee0f667af9fc440193838b986e597e`, requested by the coordinator
handoff. This document resolves what the accepted MEMFS observation and pending
repair can contribute, and specifies the remaining evidence. It is not an
independent review of its own criteria, a replacement protocol, or permission
to change a safety gate. [Machine evidence](V11_R09_GATE3_IDENTITY_ACCEPTANCE_20261001.json)
pins the inspected inputs and fresh checks.

## 1. Existing evidence and the exact remaining distinction

| Evidence | Accepted contribution | Still unproved |
| --- | --- | --- |
| Build observation `82e1619`, locally integrated | Reproducible installed-file hashes and RECORD/cache discrepancies; read with review errata | Authenticated upstream lineage, complete reproducible dependency closure, pre-use enforcement and realistic resource qualification |
| Static MEMFS `cccc5d0`, merge `34f19da` | Exact 7,073 path-to-storage-range/hash mappings in the pinned library; review checks 14,153 relocations | Upstream definition revision/authenticity, runtime selection, source/model semantics |
| Point-of-use `93cd43c` | Reproduced but **CHANGES_REQUIRED**: 86 opens, 90 existence checks and two synthetic 290.0 K outputs | Trace completion/write integrity and pre-execution sibling verification; all broader build/runtime qualification |
| Slice 3 `6340cb4` and its reconciliation | Accepted offline injected runtime, durable accounting/restrictions and RAW scope | Concrete transport/clock/resource adapters, qualified decoder execution and feature admission |
| Provider mapping `23c11e0` | Exact evidenced grammar and refusal of unsupported real mappings | Real GEFS direct GET and missing purpose contracts; current mandatory schema admits **no** `PROVIDER_REVIEW_REQUIRED` manifest |

The containing MEMFS library is 39,767,864 bytes, SHA-256
`f984057845fd0a569dd775907d4629b83b09434959436863790492aae32e5fb9`.
Static acceptance closes the **local enumeration subproblem** for these
exact bytes. A MEMFS-disabled rebuild is therefore not intrinsically required
just to enumerate embedded bytes. It remains an alternative build requiring
its own qualification. Neither a storage-range hash nor a trailing-zero count
proves upstream identity or a binary sample's logical length. The future
runtime need not open every inventory entry, but must bind every artifact it
can use and reject unreviewed overrides.

The pending repair can close observation integrity only. Do not respond to a
successful repaired trace by filling `code.dependency_build_lock`, any
`sources.*_grib_identity_decoder_build`, or `code.runtime_entrypoint_review`.
Those are compound evidence requirements, not counters advanced by one probe.

## 2. Acceptance criteria and required counterexamples

All rows below require a separate exact artifact and independent reviewer
record. A failed or missing conjunct leaves the affected identity unqualified.

| ID | Positive evidence required | Required refusal / limit |
| --- | --- | --- |
| A1: repaired observer | Fresh clean commit/tree, four allowed artifact hashes and terminal; independently reproduce complete trace, raw stream bytes and presence results against accepted static inventory; successful completion/count binding with contiguous unique typed records; source blob/hash checked before import/execution | Failed trace sink including `/dev/full`; missing suffix/completion, duplicate/gapped/invalid records; harmless changed sibling demonstrably never executes; wrong library/no interposition controls. Do not hardcode 176 calls. Narrow instrumentation, network and resource claims to demonstrated scope |
| A2: authenticated build lineage | Reviewed original package/source artifacts and independently authenticated provenance, exact hashes, build recipe/toolchain/options/patches and platform/ABI; a per-file explanation and review verdict for all 25 `eckitlib.libs` RECORD discrepancies, binding actual loaded payloads | Same-version cached wheel and matching installed payload are local agreement only. Even authenticated archive equality does not explain its internal RECORD mismatch. No guessed benign-patch verdict, rewritten RECORD, in-place reinstall, or version-string acceptance |
| A3: complete dependency lock | Reproducible installation/build from pinned artifacts in a separate unprivileged environment; include Python/interpreter, wrappers/transitive modules, CFFI/NumPy where used, native loader and complete native dependency closure, definitions/samples and environment/search rules; record exact source and output hashes and reconstruction result | The four-package list and filtered 42-library map are insufficient. Classify reviewer/test overhead separately without omitting actual runtime dependencies. Reject absent/changed/transitively substituted artifacts and uncontrolled data paths; unhashed RECORD rows confer no trust |
| A4: runtime verification | Exact reviewed entrypoint and transitive executable bytes agree with Git commit/tree and source SHA-256s; verify locked inputs before import, `dlopen`/constructors or decode; bind actual mappings/data selection to the same lock through use, with an immutable snapshot or a reviewed equivalent that prevents substitution races | Test changed wrapper, native dependency, MEMFS, decoder source, loader/environment override, path/symlink replacement between verification and use, unexpected lazy load and restart under a different build. Fail before executing unverified bytes; post-decode path hashes and an LD_PRELOAD observer alone do not supply this protection |
| A5: source/model semantics | For GEFS, IFS and AIFS separately: original operational release document bytes and retrieval provenance, effective run interval, licence/access and restriction lineage, exact origin/purpose mappings; control/perturbed member layout, native hours, parameter/level, centre/subcentre/tables/process/status/product, grid and packing signatures. Compare independently reviewed semantic pins with retained real GRIB headers and object/index/range evidence | Wrong model, control/perturbed stream, member, release interval, run, level, parameter, grid or packing refuses. A definition named template 5.42, an opaque dossier digest, or a release-signature hash alone does not attest the provider's operational release |
| A6: current-run pins and causality | Selected run has genuine readiness evidence within the frozen age rule; pin index bytes/hash, exact selected row, object path/size/ETag and coherent range identity. Independently frozen required section hashes precede using the field as evidence; preserve request-start, body-receipt, decode-complete and durable-seal bounds | Never derive expected section hashes from the same candidate response under validation or label historical/synthetic receipts current. Missing current-run evidence stays null. Later decode/seal and earlier collection clocks must not be collapsed into one timestamp |
| A7: decoder and resources | Exact qualified integration, after A2–A6: retain envelope, section, source, bitmap, grid/count and distance checks before native decode; for permitted ECMWF CCSDS use bounded full decode and exact re-encode integrity. Measure representative retained real full fields and adversarial maxima on the locked ABI, with explicit process CPU/wall/memory/output bounds and host headroom | Wrong section/build/source, truncation, bitmap, count, re-encode mismatch, native failure/absence and resource exhaustion refuse. Keep unqualified GEFS CCSDS/complex/JPEG/unknown packing closed. Tiny self-generated ecCodes fixtures and import skips are not qualification; fail safely when capacity is insufficient |
| A8: concrete launch composition | Separately accepted real purpose contracts, transport/clock/storage/resource adapters and decoder integration; validated canonical V4 plan drives exactly the reviewed runtime; actual host/storage/time evidence and physical reservations match the package | No real-origin use of synthetic placeholders, FakeClock, synthetic transport, opaque pins or validation digest as authority. Review exact integration commit/tree after reconciliation; compare actual launch bytes/identities again at use |

A3 needs a complete lock, not necessarily a rebuild of every dependency from
source if reviewed authenticated binary artifacts provide reproducible exact
installation. If a replacement build is chosen, all affected observations,
MEMFS mappings, ABI/resource measurements and reviews must be regenerated;
evidence for the old binary cannot be transferred by package version.

A4 is future runtime acceptance work. The queued four-file observation repair
is not expected or authorized to implement that runtime boundary. Source
blob checking in its synthetic harness is a narrower A1 control.

## 3. Map the criteria to actual inventory and validator requirements

`tools/v11_r09_gate3_g3l_prep.py` has **79** identities: **77 PRE_REVIEW**
inputs and two later detached-review outputs. The current all-null inventory
reports missing entries even where historical reviewed artifacts already
exist. Thus 77 is a count of **unassembled/unfilled entries**, not proof that
77 distinct investigations or new implementations are needed.

| Inventory group | PRE_REVIEW entries | Assembly/closure rule |
| --- | ---: | --- |
| `code` | 10 | Actual component commit/tree/source identities, complete A2–A4 lock, A8 runtime review; existing slice-3/mapping exact reviews may support their named entries only in their accepted offline scope |
| `protocol` | 9 | Reuse original/addendum/design/collector/bound/composition reports and completed terminals only after verifying exact objects, hashes and scope; no new protocol PASS inferred |
| `sources` | 24 | Eight identities per provider. A2–A7 support build/GRIB identity; A5/A6 still need provider/release/access/control/purpose/current-run evidence. Preserve allowed explicit publication-attestation absence with its reason |
| `network` | 6 | Exact purpose allowlist, DNS/TLS/anonymous policy, full restriction history and explicit ECMWF 503/429 resumption adjudication; A1–A4 do not close these |
| `cohort` | 8 | Pre-weather station/event selection, rules/buckets, settlement/tzdata and contemporaneous metadata with exact Gate-2 mapping |
| `storage` | 6 | Genuine private-root identity, custody, retained denial history, session/report identities, lock/seal/persistence and contemporaneous quota evidence; no authority-root installation |
| `clocks` | 4 | Concrete unprivileged recorder build plus raw calibration/uncertainty, host/boot/monotonic identity and age policy, valid for the selected window |
| `schedule` | 8 | Real readiness, full 2,713-slot denominator, frozen subset/paths/ranges/shared-object bindings, reservations/reasons/receipt schema and reviewed size evidence |
| `review` | 2 | Concrete canonical private V4 bytes and digest, assembled only when their underlying evidence is genuine; detached report/terminal remain null until later FINAL stage |

Every assembled entry needs distinct sealed `ref` and `review_ref` objects,
each with SHA-256, byte length, media type and safe relative path, plus truthful
`observed_utc` and `scope`. Preserve static/run/window scopes and freshness;
repackaging old bytes cannot refresh observation time. A reviewer must actually
accept the claimed scope: two different object hashes alone are not independence.

The prep checker validates references, dates and shape; it does not adjudicate
their semantic sufficiency. V4 `_validate_code` verifies actual Git objects,
source bytes and a dependency-lock reference, but that reference is not a
review of the lock's contents. `FrozenPlan` binds source/decoder digests to
validated manifest references; this does not turn those digests into qualified
source/decoder implementations. Current provider-mapping refusals are deliberate
and must remain until a separately reviewed implementation supplies the real
contracts. Do not remove them just to make a filled inventory pass.

## 4. Correct the date and capacity evidence

The 21:59 screen `fbbdcbbe...` was described in the handoff/checkpoint as
October 2 acquisition / October 3 target. Its **actual bytes** say target
October 2, acquisition **October 1 14:00–17:00 UTC**, decision 18:00 UTC.
Its extra `EXPIRED` finding is `time.review_before_window`, not an expired
decoder/build identity. Preserve that screen and correct the interpretation.

A fresh **proposal-only** screen at **2026-10-01 22:17:08 UTC** uses target
**October 3**, acquisition **October 2 14:00–17:00 UTC** (17:00–20:00 Kuwait),
decision October 2 18:00 UTC. It has **77 MISSING, zero EXPIRED, zero planned
slots**, `launchable=false`. This is not a reviewed date/cohort freeze or a
roll-forward of the expired launch permission. Raw file and SHA-256 are in the
machine evidence; no private launch manifest was created.

At that snapshot free disk is **2,202,046,464 bytes**, available memory
**1,071,845,376 bytes**. The existing conservative planner's first GEFS slot
(four unshared requests) requires at least **2,584,739,840 free disk bytes**
and **637,534,208 available memory bytes**. Disk is short **382,693,376 bytes**.
The calculation retains the 2 GiB disk floor, 512 MiB memory floor, full
journals/report/object/body/decoded reservations and 32 MiB additional headroom.
Exact-minimum positive and one-byte-below disk/memory negative controls passed.
These are planner thresholds, not realistic-decoder or physical-storage
qualification; even one proposed slot establishes no three-model readiness.
No scratch/worktree/evidence cleanup or resource-bound reduction is authorized
by this calculation. Fresh reservations and measurements remain necessary.

## 5. Next actions without bypassing the bootstrap or duplicating work

1. Preserve the sole queued Sonnet/high observer repair: PID **1925151**,
   not before **23:21 UTC October 1**, same clean `93cd43c` author worktree.
   Queue/process/HEAD were checked, no corrected terminal exists. Recover its
   actual terminal/diff and conduct fresh different-model A1 exact-commit review
   when it completes; reconcile newer main only after acceptance. Do not give
   it A2–A8 work or start a second observer writer.
2. The next independent offline implementation prerequisite is an **A2/A3
   complete dependency/provenance dossier**, using existing local artifacts,
   separating authenticated evidence from observations and identifying the
   precise missing original/build references. It must not install packages or
   bless the RECORD discrepancies. Stop at explicit missing evidence where
   authentication cannot be established offline; do not repeat static MEMFS
   enumeration merely to produce another observation PASS. A4 is a subsequent
   separately reviewed runtime candidate after the selected build is pinned.
3. A5/A6 can inventory and review already-retained provider bytes offline.
   The addendum mentions a separately approved bounded preflight for missing
   provider evidence, but the owner's current rule is stricter: **no real
   network/provider request before G3-L PASS**. This analysis supplies neither
   preflight approval nor an exception. An offline proposal may explain the
   circular dependency; it cannot mint current-run receipts or dispatch. If
   the required current-run pins cannot be established under the existing
   authorization, keep the gate blocked and present that precise dependency
   for separate protocol/owner resolution, without weakening a gate.
4. After A2–A8 and remaining group evidence, assemble PRE_REVIEW, obtain
   independent exact canonical-package G3-L review and its completed terminal,
   reconcile actual executable identities and complete FINAL. Neither prep
   stage, the validator digest nor this analysis grants launch. G3-E, Brain
   admission and root-custodied genuine forward SHADOW remain separate.

The old load-sensitive release failure is already closed at accepted `6ec371e`;
its full log hash was reverified (5,460 passed / 13 skipped), not rerun or
reopened. SHADOW/Brain worktrees remain clean; inventory candidate `9600510`
remains unreviewed/unmerged and ECMWF `backfill_data/` is preserved. No candidate
merge, provider request, package installation, remote publication, service,
authority, financial, V10 or AxiomTrade action occurred. The protected master
hash is unchanged. **No C/J/E/A crossing: 91/200, formal 1/50;
NOT_READY_TO_FUND.**
