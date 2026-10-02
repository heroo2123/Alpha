# Independent A4 bootstrap architecture and B1 plan review

**PASS_IN_SCOPE — ARCHITECTURE_AND_B1_PLAN_ONLY.** Reviewed detached commit `ae0c7726261f00bfa448c64a24f8b16f8f4d26d3`, tree `e10556db4ec35c0d431614248c7a7a5fbad91ffa`, against parent `6235b6d4b2c126e2d1f4f049fe89ad93e2743b9c`. The change adds exactly the bootstrap design Markdown and JSON. This is acceptance of a conditional architecture and a bounded offline structural checker plan. A4 remains OPEN; A2/A3 and A8 remain UNQUALIFIED; G3-L remains NO-GO. No launcher, confinement backend, provenance acceptance, decoder execution, or launch authority is established.

## Assessment

The design closes the architectural choice without circular self-verification. T0 is placed outside the runtime being checked, with independent custody and a trusted external run/lock/review pin. It expressly treats a statically linked launcher, its build and invocation, and any dynamic bootstrap dependency as future acceptance work. The current verifier is correctly described: `VerifiedInputs` runs inside an already imported Python process, checks `/proc/self/maps` and bootstrap files, uses an ordinary Git executable during verification, and `run()` forks that process before a focused file-open-denial filter. Those controls cannot authenticate code that executed before them or constrain every native mapping.

The proposed S1–S4 order captures and seals actual regular-file bytes, constructs a closed image, then makes a fresh exec whose ELF interpreter, loader resolution, Python imports and constructors are inside the accepted image. It distinguishes prevention before first instruction from later map evidence. Native closure includes `PT_INTERP`, transitive `DT_NEEDED`, explicit and lazy loads, SONAME and search rules, and CPU alternatives. It requires full-lifetime mapping accounting, including transient mappings, while identifying anonymous executable memory and same-user injection as backend proof obligations. A private mount namespace is a candidate, not a proven host capability; the observed bwrap loopback failure supports neither backend availability nor relaxed confinement.

The MEMFS binding is appropriately exact: containing SHA-256 `f984057845fd0a569dd775907d4629b83b09434959436863790492aae32e5fb9`, 39,767,864 bytes, the accepted 7,073-entry inventory, storage-length semantics, and the 112,589-byte residual. The design requires context/search precedence and actual selected ranges through use. Static enumeration and historical traces are supporting observations only. The retained A2/A3 audit still lacks authenticated originals, reconstruction, and adjudication of 25 RECORD discrepancies; its 78 mappings/305 edges are candidates, not a complete lock. The design preserves that dependency. A7's current `run()` also starts a Python worker subprocess from `sys.executable`; B4 must bring that child exec and its temporary data paths within the same reviewed closure or replace the adapter under exact review. The proposal's process-tree, fresh-exec and B4 integration requirements cover this as future work.

Restart custody is not self-minted: S0 requires external original-lock history and refuses a replacement or missing first-lock record. B3 requires separate feasibility and exact integration reviews before actual decoder use. B4 follows accepted B2/B3, binds real runtime maps and data selection, and receives another exact review. A7's pre-native checks and packing restrictions survive integration; A8 and G3-L have separate real-evidence requirements. No current-run acquisition circularity is resolved here.

B1 may proceed offline. It is a versioned, bounded engineering-proposal checker only, with all outputs `qualification=UNQUALIFIED`, `launchable=false`, `a4_pass=false`. It cannot be a production lock, launch token, provenance acceptance, or A2/A3/A4 PASS. There is no blocking ambiguity within that limited scope. The following clarifications should be made in B1's schema/tests so implementation remains faithful to the design:

| Severity | Location | Reason | Remedy |
| --- | --- | --- | --- |
| LOW | `docs/V11_R09_GATE3_A4_BOOTSTRAP_DESIGN_20261002.md:149` | “In-memory JSON validation” does not name the API boundary. Duplicate keys cannot be detected after a normal JSON object is constructed. | Take bounded raw JSON bytes as input and reject duplicate keys and nonfinite values during strict parsing before structural validation. |
| LOW | `docs/V11_R09_GATE3_A4_BOOTSTRAP_DESIGN_20261002.md:170` | A self-declared graph cannot reveal a truly omitted native transitive edge. “Missing transitive edges” could be misread as completeness evidence despite line 161. | Test dangling declared endpoints, unreachable declarations, and synthetic known-edge fixtures; label undisclosed real edges an unresolved A2/A3/B3/B4 obligation. Never derive qualification from B1 graph success. |

Neither clarification blocks B1 because the adjacent byte-bound/duplicate-key rules and explicit graph-completeness limit already constrain the implementation. Any B1 candidate still needs its own exact implementation review. B2/B3/B4 need later exact build, provenance, capability and integration reviews; this design review accepts none of their evidence.

## Checks performed

- Verified HEAD, tree, parent, two-path diff, clean checkout and `git diff --check`.
- Verified SHA-256 of changed Markdown `e891558a5184ea36790e904a41b27eaac328fbe12b5ca3d44c212c261d411223` and JSON `09cabdc820cff0b57f67c3abda8890efac92b179fb5b5d6f97b3a1bacbfa3652`.
- Independently checked every path, byte size and SHA-256 in the design JSON's nine-file manifest; checked all local Markdown links in the design and controlling identity criteria.
- Read the required checkpoint, requirements and engineering-progress heads, the controlling identity criteria, retained A2/A3 audit, accepted A4 R3 and MEMFS static reviews, and targeted current A4/A7/A8 source. Did not repeat suites or enumerate MEMFS entries.
- Used approved read-only shell escalation after the wrapper failed configuring loopback. No decoder import, native load, build/install, namespace experiment, provider/network request, service/authority/financial action or repository edit occurred. Only the two requested review artifacts were written outside the checkout.

Final checkout status: clean. No A4/G3-L qualification or C/J/E/A gate crossing is claimed.

A4_BOOTSTRAP_DESIGN_REVIEW_COMPLETE
