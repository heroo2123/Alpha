# A4 pre-import bootstrap and native decoder closure design

Status: **proposed architecture; independent review pending**. Authored in the
Astra/high handoff on main `6235b6d`, 2026-10-02. This resolves the next design
choice; it does not accept A2/A3, supply a trusted launcher, or grant A4 PASS.
G3-L remains NO-GO. No runtime code or existing gate is changed.

## Evidence and decision

The [identity acceptance criteria](V11_R09_GATE3_IDENTITY_ACCEPTANCE_20261001.md)
remain controlling. [R3 review](V11_R09_GATE3_A4_R3_REVIEW_95541da.md) accepts
the narrow alternate-FIFO repair, not a positive native execution boundary.
The [current verifier](../tools/v11_r09_gate3_a4_verify.py) begins after Python
and its imports have executed; `run()` forks that process and prohibits new
opens. Snapshotting native files does not cause the loader to use them.
Its bootstrap file/map rechecks cannot establish pre-load authenticity.
The existing A7 subprocess and A8 method checks do not repair that gap.

Select a **fresh-process, closed immutable runtime image** as the target
architecture. Do not extend the inherited Python child with ad hoc `dlopen`
permissions or claim that preloading, source rehashing, `LD_PRELOAD` observation,
`RTLD_NOW`, file-descriptor names, or a maps sample alone satisfies A4.
The selected architecture is conditional on a separately reviewed bootstrap
and confinement backend. No such backend is accepted or installed here.

A2/A3 is a hard dependency of qualified execution, but not of writing an
offline closure specification or testing its structural refusals with explicit
synthetic fixtures. The [retained audit](V11_R09_GATE3_A2A3_RETAINED_AUDIT_20261001.md)
still lacks authenticated originals, reconstruction, and explanations for 25
RECORD discrepancies. Its 78 observed libraries/305 edges are candidates to
classify, not an exhaustive runtime lock. The missing cached wheel stays
missing. Static MEMFS acceptance applies only to its exact containing binary.

## Trust starts outside the process being verified

T0 is the already-authorized host kernel and an independently authenticated,
reviewed bootstrap launcher with protected identity/custody and a trusted
external run/lock/review pin. Root or kernel compromise and a malicious custody
operator are outside this proposed A4 substitution boundary. Mutable artifacts,
search paths, environment, unexpected dependencies and accidental or concurrent
replacement are inside it. The threat model and custody assumptions require
independent acceptance; they are not newly installed authority.

Prefer a small, statically linked T0 launcher with no runtime plugins or
helper subprocesses. Static linkage is a design constraint, not evidence of
trust: its source, compiler/linker closure, build outputs, embedded code/data,
launch mechanism, exact executable bytes and custody still need acceptance.
If any dynamically loaded bootstrap dependency is necessary, it too needs an
external pre-execution guarantee; a post-start self-hash is insufficient.
The launcher's own authenticated invocation is the explicit trust anchor,
not a recursive claim that Python verifies Python before Python starts.

The current coordinator, shell, Git command and Python observation tools are
engineering tools, not this accepted T0. Prepare Git commit/tree/blob proofs
offline; pin independently reviewed proof bytes into the eventual lock. Do not
invoke an unqualified Git executable and loader from the qualified launch path.
A new proof verifier would itself belong to the reviewed T0 closure.
No creation under `/etc/alpha-v11` or `/var/lib/alpha-v11`, setuid helper,
service change, host-policy relaxation or privileged installation is authorized.
If a safe backend cannot operate under existing policy, refuse and retain the
external prerequisite. The observed bwrap loopback failure is not permission
to bypass confinement or proof about every namespace capability on this host.

## Required launch sequence (future implementation)

| Stage | Required invariant before advancing |
| --- | --- |
| S0: external acceptance | Trusted custody supplies run identity, first-lock pin, exact A2/A3 review pins, T0 identity and the accepted backend policy. No candidate-supplied `ACCEPTED` string authenticates these. Same-run restart must preserve the original lock; missing/replaced history refuses. |
| S1: capture | T0 validates a bounded canonical lock and reviewed Git proof bundle, opens only declared regular inputs without following undeclared links, copies and hashes the actual captured bytes, and seals them before executable use. Bound bytes/count/depth/time and descriptor use; FIFO/device/oversize/truncation/duplicate inputs refuse. No import, native inspection by execution, constructor or decoder runs here. |
| S2: isolate | Construct a private closed root from those sealed objects, including the exact ELF interpreter path, Python, stdlib/extensions, complete native/data closure and explicit symlink aliases. No mutable backing alias, host root fallback, inherited directory/file/socket descriptor, host `/proc` escape, network, or executable writable scratch. Scrub environment before exec. Establish resource bounds, parent-death handling and complete process-tree termination. Failure refuses before S3. |
| S3: fresh exec | Start the pinned interpreter from the immutable image. The kernel's ELF interpreter and every loader search candidate must resolve inside that image to accepted bytes. This is a fresh exec, not fork-only inheritance of host Python modules. Loader initializers/IFUNCs/constructors already fall under the closed image guarantee. |
| S4: Python/decoder init | Only locked startup modules, extensions, imports and explicit lazy dependencies are reachable. Disable implicit user/site/cwd/bytecode/plugin discovery unless individually frozen and reviewed. Initialize the reviewed decoder adapter and fixed definitions/sample policy. No late fallback to host-installed packages or data. |
| S5: bounded decode | Receive only bounded data over the reviewed input channel; retain A5/A6 pins and A7 pre-native checks, source/packing restrictions and resource accounting. All code and data remain bound through use. The decoder has no transport, credential, order or storage-authority capability. |
| S6: finalize | Validate bounded typed output, child exit and complete parent-owned evidence; timeout/crash/incomplete accounting/map or selection mismatch refuses. Reap the complete child tree and durably seal the report before use. A result is not G3-L or SHADOW permission. |

Candidate S2 backend: a private mount namespace containing only read-only views
of fully sealed file objects and a minimal reviewed scratch/input/output layout.
This is a backend specification, not a claim that the host currently supports
it. Directory topology, mounts, executable memory policy, process inspection,
remaining descriptors, auxiliary kernel mappings and permitted syscalls must be
reviewed and tested. A read-only bind of a mutable host file or chmod alone is
not adequate; every backing file needs immutable captured bytes. Host symlink
resolution is not the runtime alias policy. If namespace creation or required
protections are unavailable, terminate; no automatic weaker fallback.

The accepted backend must prevent execution/mapping outside the locked closure,
including undeclared lazy loads and writable-file executable mappings, before
their first instruction. Account explicitly for vDSO/kernel-provided mappings,
relocations, Python bytecode compilation, generated CFFI trampolines and other
anonymous executable memory. Any needed exception must have a bounded reviewed
mechanism and provenance; do not silently permit arbitrary W+X or JIT/plugin
code. Protect the child against unintended same-user process injection under
the accepted host threat model. These are open proof obligations, not controls
provided by the current focused seccomp test filter.

## Lock and loader/data binding

The future lock binds the exact entrypoint Git commit/tree/source proof,
A2/A3 evidence and detached review hashes, T0/backend identity, interpreter/ABI,
full Python/native/data artifact set, path aliases, environment allowlist and
values, loader resolution rules, resource limits and the MEMFS policy. Include
artifact kind, byte length, SHA-256, provenance reference and dependency edges.
Separate reviewer/test overhead with reasons; do not omit an artifact simply
because one successful trace did not load it.

Native closure covers `PT_INTERP`, startup and transitive `DT_NEEDED`, symbol
versions/ABI, explicit `dlopen`/extension/plugin targets and reachable lazy
loads. Model exact SONAME/path resolution, RPATH/RUNPATH, loader cache/default
paths and CPU/hwcaps alternatives; omit/disable or individually pin each search
source. An edge's basename matching one historical mapping is insufficient.
The selected image admits only accepted alternatives. Extra/ambiguous/missing
resolutions refuse. Trace observations corroborate reachability but do not
prove it exhaustively. Changes require a new lock and independent review.

Enforcement precedes constructors because all reachable executable backing
bytes are confined to the captured image before exec, not because a later
`/proc/maps` comparison can undo an initializer. Parent-owned map/loader evidence
must bind device/inode or sealed-object identity, file offsets, executable
ranges and hashes to the image. Account for load/unload events over the full
lifetime; an unexplained transient mapping is a failure. Never compare relocated
memory blindly with the unrelocated file. Backend integration review must show
both prevention and accurate observation, using safe constructor sentinels.

For the currently observed MEMFS binary, bind containing SHA-256
`f984057845fd0a569dd775907d4629b83b09434959436863790492aae32e5fb9`, size
39,767,864, the exact accepted 7,073-entry inventory and its separate review.
Bind table path, symbol, offset, storage length and range hash for definitions,
samples and IFS samples. Verify whole containing bytes plus inventory identity
before loading; validate range arithmetic against that exact object. Preserve
storage-length semantics and the 112,589-byte unattributed `.rodata` residual;
whole-binary identity binds residual bytes but does not authenticate their origin.
A different build must regenerate and review the inventory.

Freeze ecCodes context/API configuration, definition/sample search order and
all `ECCODES_*`/legacy overrides that could affect selection. A locked library
with mutable external definitions is not a bound decoder. Either expose only
the accepted MEMFS namespace or individually lock every permitted external
file and its precedence. Record actual opens/existence lookups and selected
ranges with parent-verified completion; undeclared fallback refuses before
consuming its bytes. Existing static enumeration and A1 interposition are
supporting evidence, not the sole enforcement. Original upstream definitions,
provider semantics and source authenticity remain A2/A5 obligations.

## Bounded offline implementation and review path

**B1, next implementable slice: closure-specification checker, no launcher.**
After independent design review, add only
`tools/v11_r09_gate3_a4_closure_spec.py`,
`tests/test_v11_r09_gate3_a4_closure_spec.py`, and its scope document. Pure bounded
in-memory JSON validation; no filesystem discovery, Git process, native load,
imports from the proposed image, provider access or change to existing A4/A7/A8.
Input is explicitly an engineering proposal, never a production lock.

Define one strict versioned canonical schema with unique artifact IDs/paths,
exact dependency endpoints, distinct classified bootstrap/runtime/test roles,
explicit unresolved obligations and references to A2/A3/MEMFS evidence. Bound
input bytes, collection lengths, nesting, strings and integer ranges; reject
duplicate keys, nonfinite/bool-as-integer values, path traversal, invalid hashes,
ambiguous aliases/targets and arithmetic overflow. Resolve declared graph
reachability from the entrypoint; allow legitimate native dependency cycles
with visited-node tracking. Graph consistency cannot establish completeness.
Require bootstrap, interpreter/loader and data-selection declarations, but
label them assertions pending evidence. Review pins have shape only at B1.

Output deterministically lists structural errors and unresolved obligations.
Every output, including a structurally complete synthetic proposal, must state
`qualification=UNQUALIFIED`, `launchable=false`, `a4_pass=false`. No conversion
to A8/G3-L identities, no acceptance-token producer, and no auto-filled genuine
references. Positive tests mean structurally valid proposal only. Test missing
transitive edges, duplicate/alias collisions, cycles, absent bootstrap/data
rules, wrong inventory/library binding, fake acceptance labels and bounded
malformed input. This avoids another synthetic runtime presented as progress
on native execution. B1 reduces interface ambiguity; it crosses no C/J/E/A gate.

**B2, blocked qualification input:** accepted authentic A2 lineage, complete A3
reconstruction and explicit trusted bootstrap custody/backend capability.
Independent review must bind exact artifacts. Retained installed equality is
not a substitute. No package download/install or root authority work is assigned.

**B3, separately assigned backend feasibility and enforcement:** only after
accepted design and a fixed candidate build, implement a standalone T0/S1–S3
prototype and synthetic benign constructor controls in an isolated worktree.
First review is on exact source/build/TCB and capability feasibility; a second
exact integration review must prove the table above before actual decoder use.
Unavailable backend features stop the slice. Do not reuse current fork-only
results as pre-import proof. Do not start B3 in this handoff.

**B4, native/decoder integration:** after B2/B3 acceptance, bind actual runtime
mappings and MEMFS selection, integrate A7 checks without widening permitted
packings, and test retained real fields with adequate host resources. Run an
independent exact-commit review, then reconcile with newer main and repeat
relevant integration tests. Reconciliation invalidating reviewed bytes needs
fresh review. A8 and G3-L still require their own real evidence and acceptance.

## Acceptance counterexamples and stop conditions

| Counterexample | Required observation in B3/B4 |
| --- | --- |
| Changed Python wrapper/stdlib/extension or preloaded module | No unverified import/initializer; fresh exec cannot inherit the host module |
| Changed native dependency, interpreter, loader, MEMFS or data file | Refusal before any changed constructor/import/data consumption |
| Same path replaced by symlink, rename, hardlink or in-place write between capture/use | Substituted bytes never execute; record refusal when required identity changes; immutable captured bytes remain the only possible source |
| Loader environment/cache/hwcaps/SONAME collision or missing transitive edge | No host fallback or unexpected resolution; sentinel never executes |
| Undeclared late `dlopen`, extension, external definition or sample override | Refusal before code/data use, not merely post-decode mismatch |
| Transient mapping or omitted/failed observation suffix | Fail complete evidence; prevention must independently have held |
| Changed first-lock record/restart lock, invented replacement run | Existing trusted run custody refuses substitution; candidate cannot reset history |
| Insufficient space/memory, blocked namespace, child hang/crash, large output | Refuse/reap under bounded resources; no filter relaxation or incomplete success |
| Valid synthetic closure plus fabricated `ACCEPTED` records | Never A2/A3/A4 PASS or launchable; exact independent provenance review still required |

A2/A3 remain UNQUALIFIED, A4 OPEN, A8 UNQUALIFIED. Disk is below the 2 GiB
G3-L floor; the owner rule forbids real network/provider requests before G3-L
PASS. This design cannot resolve current-run acquisition circularity or grant
an exception. Root-custodied forward SHADOW and multi-model Brain admission
remain separate. No financial/V10/AxiomTrade action, new release test result,
forward evidence, score increase or remote publication is claimed.
