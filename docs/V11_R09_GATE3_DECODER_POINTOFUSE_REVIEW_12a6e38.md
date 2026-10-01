# Independent exact-commit point-of-use repair review

**Verdict: PASS for the bounded offline observation scope only.** R1, R2 and C1 are corrected. No additional material failure was found in the completed controls. This is the sole independent GPT-6 Astra/high review; no sub-agents were used.

Reviewed commit: `12a6e3879e5618617e4abd1f39165add9253151a`.
Reviewed tree: `01b05f82f7578f1a02b304f370d424c8f41f9758`.
Checkout: `/tmp/alpha-v11-pointofuse-review-12a6e38`, detached and clean before and after execution, including untracked-file inspection. `git diff --check 93cd43c HEAD` passes. Exactly the four observation artifacts differ from `93cd43c411de696dd575efc7038eca723ed1e41a`; those same four files are the entire change since source pin `95f03393467fd65e851721dedc9fffe991889552`.

The reviewer read the prior independent CHANGES_REQUIRED report at `/home/alphaadmin/AlphaV11_Dev/Alpha/docs/V11_R09_GATE3_DECODER_POINTOFUSE_REVIEW_93cd43c.md`, the complete four-file repair diff, candidate evidence, both repair terminal forms and postcommit report, and the checkpoint/matrix/progress in both this candidate and current main. Current main's 23:04 UTC entries still treat A1 as awaiting independent acceptance and retain G3-L NO-GO, 91/200, formal 1/50 and NOT_READY_TO_FUND. Main, services and protected/private material were not modified or inspected beyond the requested public repository records.

The four candidate SHA-256 values agree with the author's terminal:

| Observation artifact suffix | SHA-256 |
|---|---|
| `.json` | `2953e7ffaa676f6178099637037a3b0c7c10a816b5144b21f5e76f49edeb11d1` |
| `.md` | `33c830ce3dac4422d9cc8eeead49d81d8587db65a9c31e017b5c96f51cda29c1` |
| `_probe.py` | `3404cbb0c0233eb2c487e36d5b193cdd7285fcbd3cf43760ceb627cda5d3ae72` |
| `_shim.c` | `a56327183a4313b092f0787d57d88804a608a595f5887354377c66f66744560a` |

All four share prefix `docs/V11_R09_GATE3_DECODER_POINTOFUSE_OBSERVATION_20261001`. The author's postcommit report hashes to `ca092595cea20e681ee7818e4321f4ae16e86bddc81365d7d066cb9dd0481451`. The committed JSON honestly records precommit dirty state; its source/shim hashes and observation objects agree with this exact clean reproduction. It is not misattributed as a clean postcommit capture.

**Independent reproduction.** The exact committed probe exited 0, with empty stderr and `POINTOFUSE_OBSERVED_CONSISTENT`. The development interpreter ran with `PYTHONDONTWRITEBYTECODE=1`, core soft/hard limits zero, and reviewer-installed inherited seccomp denial of native `socket` and `connect`. IPv4/IPv6 socket denial was checked before execution; native connect denial was separately checked in both output comparison children. No provider/network request, credential access, real order or financial execution was made. Compilation and all artifacts were confined to unique temporary directories; no candidate or installed package bytes were changed.

There are 176 typed, uniquely indexed calls in order, followed by one completion record. The completion count agrees with the separately emitted child result; neither parser nor reviewer treats 176 as a universal constant. Independent raw reads, without the candidate cross-check helper, compared all 86 stream dumps to the static inventory's library offsets, sizes and SHA-256 values: all agree, totaling 180,691 bytes, maximum 15,150 bytes. All 90 presence checks agree (71 true, 19 false). There are 71 distinct open paths and 90 distinct presence paths. Template 5.42 is opened at call 169; its hash is `54013f4db7aebac06cdd37b38243ae8e06871d9e8725dcedf2cbbc7362ad9827`.

The installed MEMFS library remains 39,767,864 bytes, SHA-256 `f984057845fd0a569dd775907d4629b83b09434959436863790492aae32e5fb9`. Independent `dladdr` checks resolve both real symbols to this library. Simple packing succeeds; IFS/AIFS synthetic CCSDS outputs are template 42, 290.0 K, and 205/208 bytes. A separate reviewer wrapper observed identical message hashes with and without the shim:

| Synthetic output | SHA-256 |
|---|---|
| IFS ENS, 205 bytes | `3668fb63e92e9ea1dddca18407228d81a96befee9c997ec082454a0b66e7ecef` |
| AIFS ENS, 208 bytes | `9d923a1f4ea1489782a4d9b7cd13d54c6125a305bbebc75e4477de13793533e6` |

Actual resource queries after the candidate child function show CPU 60/60 seconds and address space 536,870,912/536,870,912 bytes. Code inspection confirms 90-second main-capture timeout and 60-second compiler/negative timeouts. CPU/address-space limits belong to the capture child; the compiler and candidate negatives do not receive them. Core suppression and native network denial are reviewer wrapper properties. These checks establish these fixture outputs, not general instrumentation equivalence.

**R1 — corrected.** All trace writes/flushes and completion close are checked. The genuine `/dev/full` decode control aborts with FATAL/-6. Independent direct-symbol controls likewise abort on failed exists-record writes, failed open-record writes, a sink redirected to `/dev/full` only after a successful record and before completion, and a dump sink symlinked to `/dev/full`. Duplicate completion and a MEMFS call after completion abort. Wrong nonexistent library reproduces FATAL/-6; an already loaded wrong library (`libc`) fails symbol resolution. No preload permits the small decode with no trace; the full capture child without preload exits nonzero for missing completion symbol.

Fourteen independently constructed trace variants are rejected by both the parser and unchanged main reporting logic: empty trace; suffix plus completion omitted; suffix omitted with original completion retained; suffix omitted with completion rewritten to the shorter count; omitted middle record; misordered records; duplicate call; missing, duplicate or early completion; missing final newline; truncated JSON; unknown function; and boolean call index. Every main replay exits 1 with `TRACE_INTEGRITY_FAILED`. These are explicit reviewer-injected replays of genuine child output, not claims of naturally occurring disk faults. Changed dump bytes and flipped presence values separately exit 1 with `POINTOFUSE_OBSERVATION_INCONSISTENT`. Count binding detects loss against this child's result; it is not cryptographic authentication against coordinated fabrication of both outputs or a claim of crash-durable storage.

**R2 — corrected.** The sibling's reviewed commit/blob binding is checked before source comparison and execution: commit `95f03393467fd65e851721dedc9fffe991889552`, blob `d814a6f5a9139647bc68d8a782d97b2046b62051`, SHA-256 `87721400087dc81ae8878672cd7ee31db28185056217a6a8338b26a2496f9f56`. A harmless changed temporary sibling is refused with zero audited execution events and no marker. A separate control changes a temporary sibling after successful verification; execution still uses the verified snapshot. Candidate files remain untouched. This validates a local exact-source check; it authenticates no upstream build lineage.

**C1 — corrected.** The comments/documentation now disclose temporary compilation and writes, use an existing companion section, limit the transparency claim, distinguish Python socket blocking from reviewer native denial, and accurately delimit subprocess resource limits. No G3-L, provider, financial or forward SHADOW acceptance is promoted.

The normal sandbox could not start (`bwrap` loopback namespace error); authorized local execution outside it used the native denial wrapper above. One automatic approval attempt timed out and the permitted retry succeeded. All required tests completed safely. An initial reviewer-only output-hash assertion expected two getter invocations; ecCodes produced four observations, two identical observations per message. Both children had already exited successfully. The corrected validator checked the retained complete outputs and their two distinct hashes; the initial diagnostic is preserved. This was a reviewer harness assumption, not a candidate failure. No full product suite or broader resource/build qualification was attempted or claimed.

Evidence is retained in `/tmp/alpha-pou-independent-12a6e38-ptUp36g8`; the machine terminal includes scripts, raw evidence, input/output SHA-256 values, test results and final checkout identity. Principal evidence hashes:

| Evidence | SHA-256 |
|---|---|
| `reproduction.json` | `b97ed9c3ac61f769b5882e7c55ec3d58ac721c6298207e6c6ecce3d0ab13fca2` |
| `controls.json` | `fd3adaaaad1fb0c62f0c0e48a706936ee0a0c81570573f0fd002afd42456eb6e` |
| `output_limits.json` | `f6546b02ec12bc0676c4b63b245cab755bb9d50a61563160b0fdc79ba72ae651` |
| `final_checks.json` | `2a3a593b418d8bc90ae6dbaa0c1feac4ff37378ce934775c2b6cca1023be4f6b` |

**NO_AUTHORITY for G3-L, financial and provider acceptance.** Missing authenticated upstream source/build provenance, inconsistent wheel RECORD, CCSDS/provider permission and wider G3-L identities remain unresolved. No forward SHADOW credit or C/J/E/A change follows. G3-L remains NO-GO; 91/200, formal 1/50; NOT_READY_TO_FUND. V10, AxiomTrade, services, protected authority and the private master were untouched. No merge or publication was performed. This PASS accepts only the exact repaired bounded observation.
