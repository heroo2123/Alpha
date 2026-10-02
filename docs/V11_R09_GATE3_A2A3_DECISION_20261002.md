# A2/A3 retained-artifact acceptance decision

Status: **proposed decision and evidence-intake ledger; independent exact-commit
review required**. Astra/high, 2026-10-02, starting main `3878fa9`.
This resolves the [handoff](V11_R09_GATE3_A2A3_NEXT_REVIEW_HANDOFF_20261002.md).
It does not revise the [controlling acceptance criteria](V11_R09_GATE3_IDENTITY_ACCEPTANCE_20261001.md)
or accept any package, discrepancy, build, lock or runtime.
**A2/A3 UNQUALIFIED; A4 OPEN; A8 UNQUALIFIED; G3-L NO-GO.**

## Decision and limits of the evidence

Retain the installed environments as evidence. Do not repair their RECORDs,
reinstall over them, or use agreement between their copies as authentication.
The accepted [retained audit review](V11_R09_GATE3_A2A3_RETAINED_REVIEW_89f85ac.md)
already establishes the observation scope; another observation PASS cannot
advance A2/A3. A fresh read-only audit at 03:45:53 UTC matched its JSON exactly
after excluding only `observed_utc`: 25 discrepancy rows, 75 payload reads,
78 historical mapped files and 305 static NEEDED edges. The named wheel cache
locator is absent. This does not prove exhaustive absence everywhere.

The contradiction is between retained RECORD assertions and payload bytes.
It does **not** establish malicious tampering, a harmless packaging error,
auditwheel use, or the chronological stage at which bytes changed. Even a
future authenticated archive matching the historical wheel digest would only
authenticate that archive; its internal disagreement still needs adjudication.
The historical digest is a comparison target, never the trust anchor.

Retained bytes can support file equality, metadata statements, static ELF
structure and consistency of a declared graph. They cannot alone establish
producer identity, a lost build history, independently trusted release hashes,
complete loader selection, or reproducible installation. Further disassembly,
matching another related installation, or reconstructing a wheel from installed
files cannot recover those missing provenance facts. A successful decode or
trace would likewise not establish them. No decoder execution is assigned here.

## Two admissible future paths; neither is executable under this handoff

**H: qualify the historical installed bytes.** Require authenticated originals
and the exact build/postprocessing chain below. For each of the 25 files, bind
both the RECORD assertion and actual payload to authenticated stages, identify
the discrepancy's cause, and obtain an independent per-file disposition. If the
RECORD value does not identify a real historical file, the producer must supply
authenticated records explaining how that assertion was generated; do not
invent a preimage. Missing causal evidence leaves H blocked even if an
authenticated final archive contains exactly the observed payload.

**R: qualify a replacement in a separate environment.** If H's provenance is
unavailable, select a separately authenticated, installable binary distribution
or a separately authenticated source/build chain. No requirement to rebuild
every dependency from source is added. A binary installation still needs the
A2-required build recipe/toolchain/options/patches/platform/ABI evidence and a
reviewed supply chain. Reproducible exact installation does not imply a
bit-for-bit source rebuild was performed. State precisely which was tested.
R leaves the 25 historical discrepancies unresolved and excluded from the new
qualified closure; it does not retroactively resolve them. Any old payload
reused by R retains its own provenance obligation.

No replacement is selected now. R changes build identity and requires fresh
affected A1 observations, A3 closure/reconstruction, static MEMFS inventory,
A4 integration, A7 ABI/resource evidence and A8 composition review. Retain A5
provider facts only if their independent provenance, scope and effective period
still apply; rebuild any decoder-dependent semantic comparison. Reconcile exact
code/lock/review pins, run custody and G3-L evidence afresh. Package-version
equality transfers none of the old binary acceptance. Leave old evidence intact.

## Evidence to supply, and responsibility

The companion [ledger](V11_R09_GATE3_A2A3_DECISION_20261002.json) binds existing
source evidence and lists all eight observed packages and all 25 discrepancy
rows. All authentication, causal and acceptance references are **null**.
It is an engineering intake request, not a lock or accepted G3-L identity.
The eight-package list and 78 mappings are lower-bound observations, not the
complete install/runtime set. Extend the inventory for every discovered input.

| Item | Exact required evidence | Supplier and acceptance responsibility |
| --- | --- | --- |
| E1 originals | Exact original wheel/sdist/native archives with filenames, tags, lengths and hashes; original eckitlib RECORD and payloads; publisher release manifest/signature or independently trusted release-digest record binding those exact artifacts | Original publisher/build custodian supplies lineage; an already authorized owner/custodian supplies bytes through an approved offline transfer. Independent provenance reviewer verifies the trust anchor and artifact binding. A candidate-provided hash or signing key alone is insufficient. |
| E2 each discrepancy | Package/archive entry and original RECORD row; recorded and observed hash/size; authenticated source/build stages and transformation inputs/outputs; exact patch/postprocessing command, tool binary and options; authenticated producer explanation; independent per-file causal and acceptance verdict tied to actual payload | Producer/build custodian supplies records; separate reviewer adjudicates all 25 entries. One shared recipe may explain several files only with explicit per-file stage/output bindings. Unknown cause, absent stage evidence or a merely plausible explanation refuses H. |
| E3 interpreter/native/toolchain | Original interpreter/stdlib/extensions and OS/native packages; authenticated repository/release metadata and original archives; source revisions, patches, compiler/linker/build/postprocessor binaries and their dependency identities, commands/options, target platform/ABI and build records | Interpreter/distribution and native-build custodians supply; provenance reviewer accepts exact artifacts and build chain. `/usr/bin/python3.12` hash, ABI label, compiler pathname, CMake markers and build IDs are observations only. Include unpack/install tools in reconstruction provenance. |
| E4 complete closure | Fixed entrypoint and source proof; interpreter startup/stdlib/site policy, wrappers/CFFI/NumPy and transitive extensions; PT_INTERP, startup/transitive NEEDED, symbol versions, explicit/lazy dlopen/plugins; environment, aliases/symlinks, loader cache/default/hwcaps/RPATH/RUNPATH selection rules; justified runtime versus test classification | Offline implementation owner proposes the full closure using authenticated inputs; independent dependency/runtime reviewer accepts its coverage and selection evidence. Basename matching and one maps snapshot are insufficient. Actual prevention and through-use binding remain A4. |
| E5 definitions/samples | Original source revision and authenticated definition/sample bytes, generated/embedded transformation and exact containing binary; selected data namespace and precedence, complete allowed overrides or their refusal; exact MEMFS inventory and separate review | Original source/build custodian supplies lineage; data/runtime reviewer accepts exact inputs and selection policy. Existing 7,073-entry inventory only binds the old containing bytes, not upstream origin or actual runtime selection. |
| E6 reconstruction | Authenticated pinned installable inputs and installer/build closure; isolated unprivileged offline procedure, scrubbed environment, exact commands/logs/input-output manifest and completed exit terminal; independent reproduction and refusal controls | Separate reconstruction operator performs it only after input review, capacity and isolation are accepted. Different-model exact-artifact reviewer verifies reproduction and changed/missing/substituted input and uncontrolled-data-path refusals. Owner/root custody is not self-installed. |

An unhashed RECORD row is not automatically malicious or automatically runtime
irrelevant. Account for all 484 observed unhashed rows: bind source and generation
rules for generated outputs, lock needed bytes, or justify exclusion and prevent
their selection. Disable or pin bytecode/site/plugin discovery as appropriate
to the separately reviewed closure. Do not label them authenticated merely by
adding locally calculated hashes. Review harness dependencies must be classified
without dropping any real production dependency.

The owner/custodian supplies authorization and independently trusted material;
the implementer assembles it; the independent reviewer decides its technical
sufficiency. Owner authorization alone does not authenticate a package or resolve
a discrepancy. No messages to suppliers, downloads, installation or reconstruction
are authorized by this request list. No real network/provider request before
G3-L PASS remains binding; if obtaining evidence requires changing that boundary,
leave acquisition blocked for separate owner/protocol resolution.

## Next safe slice and review gates

The useful offline slice completed here is a **fully enumerated pending intake
ledger**, copied from accepted observations with explicit empty external-evidence
slots. It prevents a generic package PASS from silently erasing 25 unresolved
file obligations. It adds no acceptance-producing checker, duplicates neither
the retained audit nor the live B1 closure checker, and requires no package bytes
to execute. No further provenance implementation is justified until new input
arrives; repeatedly rehashing the same files is not progress on authentication.

1. Commit this decision and ledger as a candidate. A different-model reviewer
   must check their exact commit/tree and both hashes, every copied package/row,
   evidence references, H/R conditions, trust responsibilities and scope limits
   against controlling criteria. PASS means decision/intake-plan acceptance only.
2. Continue the existing B1 retry in its isolated worktree. Its independent
   exact implementation review remains separate; synthetic structure cannot
   supply E1–E6. Do not launch another B1 writer or start B3/backend work here.
3. When authentic evidence arrives, preserve original intake bytes and custody
   records separately. Review E1–E3/E5 and each H discrepancy, or explicitly review
   R's new provenance. Quarantine conflicting or incomplete inputs as evidence;
   never repair them in place or fill acceptance references automatically.
4. After fixed authenticated inputs, complete E4/E6 in a separately scoped
   unprivileged offline task with sufficient resources. Exact independent A2/A3
   review binds all inputs, closure and reconstruction outputs. A4/B3/B4, A5–A8,
   final reconciliation and G3-L each retain their own required evidence/gates.

Main was clean at handoff recovery. B1 PID 2084227 is alive waiting for its
04:20:10 UTC retry, with no candidate or retry terminal. SHADOW and Brain
readiness worktrees remain clean; ECMWF `backfill_data/` remains untracked and
preserved. No new commissioning/BrainWork file was found in the bounded
depth-two check after 03:43 UTC. The private FINAL-REVIEWED master hash matches
the checkpoint. Free disk at 03:44 UTC was 1,075,892,224 bytes, below the 2 GiB
G3-L floor; no cleanup or resource qualification was attempted. The accepted
5,460-pass/13-skip release result remains historical; no new failure or rerun
is claimed. **No C/J/E/A crossing: 91/200, formal 1/50; NOT_READY_TO_FUND.**
