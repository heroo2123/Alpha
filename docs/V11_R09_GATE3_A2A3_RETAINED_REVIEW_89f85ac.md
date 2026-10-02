# Gate 3 A2/A3 retained provenance audit — independent exact-commit review

**ACCEPT_IN_SCOPE. A2 and A3 remain UNQUALIFIED. G3-L NO-GO.** Acceptance covers the four-file retained-byte observation candidate only. It grants no authenticated provenance, complete dependency lock, runtime, source, launch, SHADOW, production or financial qualification.

- Candidate: `89f85acdb973fb2c7cceafbcf3bb0cb1848c7ead`
- Tree: `9ff38fb832c2b68836e81ccdc03eed409f36283b`
- Base: `6ce37ab6ba8b93bafacdcb1597f53916d994eea7`
- Checkout: `/tmp/alpha-v11-gate3-a2a3-review-89f85ac`
- Review completed: 2026-10-02 UTC (started 2026-10-01); independent Codex review session, separate from the identified gpt-6-sol author.

## Scope and validation

Inspected the complete four-file diff, the A2/A3 rows and complete-lock note in `docs/V11_R09_GATE3_IDENTITY_ACCEPTANCE_20261001.md`, the retained dossier, and its underlying observation/review evidence. Followed the report/terminal structure of `V11_R09_GATE3_V4_SLICE3_REVIEW_ef45d35`.

The candidate terminal intentionally binds content commit `8198d7351a051ce2c792e6bb9ed5ab1ddddf8d4c`, tree `6363b29d20ab17b9330b4e2322ee378efe8c909c`. Those objects exist and all three bound artifact byte lengths and SHA-256s match both their content-commit blobs and the final candidate. The final commit adds the terminal; it does not alter those three artifacts. The supplied `/tmp` dossier is byte-identical to the repository dossier: 48,895 bytes, SHA-256 `55cedb8d459884e909076db6c97bcddaa375b5fe69276aaf4583beb6f19b7883`.

| Check | Result |
| --- | --- |
| Independent focused probes | 13 passed; final run 4.056 seconds |
| Candidate audit regenerated to captured stdout | Identical JSON after removing only `observed_utc` |
| 25 discrepancy rows in three installations | All 75 payload sizes/hashes match; every RECORD size and hash disagrees with its payload |
| Historical native map | All 78 actual file sizes/hashes match |
| Independent ELF inspection using `objdump -p` | Every NEEDED, SONAME, RPATH and RUNPATH matches; all 305 edges have exactly one filename/SONAME candidate in that set |
| Packaged build metadata | All five file sizes/hashes and selected marker lists match |
| Related dossier packages | All eight METADATA/RECORD hashes verified; 2,370 RECORD rows, 484 unhashed; all 1,886 hashed rows checked, exactly 25 mismatches and no missing hashed rows |
| Interpreter | Resolved path, 8,020,928-byte length and SHA-256 match retained dossier |
| Artifact, historical-source and readelf bindings | Match actual retained bytes; readelf version also matches |
| Python compilation and `git diff --check 6ce37ab..89f85ac` | Pass; compilation performed in memory |

The probes use standard-library `unittest`; they do not run the repository suite or import a decoder. The ELF cross-check uses a separate executable/parser from the candidate's `readelf` parser. Its binary is `/usr/bin/objdump`, SHA-256 `325c4205a4c658a9d1e1ebc469ae55975a2b897a3d3c1e79d9b158612d37f745`.

Run the retained probes:

```bash
python3 -B /tmp/alpha-v11-gate3-a2a3-review-89f85ac-probes.py
```

Six adversarial cases passed by rejecting before JSON output: changed historical RECORD digest, changed expected payload digest, changed mapped dependency digest, missing discrepancy row, duplicated discrepancy row, and disagreement among local RECORD copies. All changes were in-memory mocks; no installed or candidate bytes were changed. These test the observation tool's stated drift checks, not A3/A4 runtime enforcement or resistance to a coherently forged provenance dossier.

## Findings

**No P1, P2 or P3 correction findings within the declared retained-observation scope.** The candidate consistently preserves UNKNOWN/UNRESOLVED status for unsupported provenance and false qualification flags. The outstanding requirements below are acceptance gates, not defects concealed by this observation candidate.

The old cache path is absent. Its claimed historical 35,957,853-byte archive hash, wheel RECORD hash and all 25 payload comparisons agree with the retained prior review. The archive itself cannot be freshly hashed or inspected, and this review grants no fresh wheel verification. The author's broader search statement is not proof that no original exists anywhere; this review verifies the specific absent locator and grants no authentication or reconstruction credit from a negative search.

The three matching installations may share their origin. Filename/SONAME uniqueness proves only candidate matching inside a selected historical set, not actual loader selection or completeness. Compiler-path strings, MINSIZEREL exports and enabled feature macros are accurately quoted installed metadata, not authenticated compiler/options/build evidence. The two repository hashed requirements files omit the ecCodes stack and cannot supply its lock. The terminal's `independent_review: PENDING` accurately describes the author-produced artifact; this detached review does not retroactively change it.

## A2/A3 disposition and precise open sub-criteria

| Criterion | Accepted contribution | Still open / gated |
| --- | --- | --- |
| A2 original artifacts and provenance | Exact local installed identities and preserved historical observations | Reviewed original package/source artifacts with independently authenticated origin and hashes for all eight Python packages, interpreter and native artifacts; no original is authenticated here |
| A2 build lineage | Five installed metadata files with reproducible byte identities | Exact build recipe/invocation, compiler/linker/toolchain identities, options, patches/postprocessing, source revisions, platform and ABI tied to the selected artifacts |
| A2 all 25 RECORD discrepancies | Each recorded digest/size and actual local digest/size independently corroborated; each remains `UNRESOLVED_DO_NOT_QUALIFY` | Evidence-backed causal explanation and independent adjudication for every file, tied to authenticated originals and the actual loaded payloads; no benign-patch verdict or RECORD repair is justified |
| A3 complete executable closure | Eight-package RECORD observations and 78 historical mapped-file identities, with 305 static references | Entrypoint-specific Python/interpreter, wrappers/transitive modules, CFFI/NumPy, native loader and full transitive/lazy/dlopen closure; actual loader selection and complete separation of runtime dependencies from reviewer/test overhead |
| A3 definitions/samples and selection | Existing containing-byte observations remain historical inputs | Authenticated definition/sample revision and locked exact data selection, environment/search rules, symlink/path resolution and override policy |
| A3 reproducible reconstruction | None; explicitly not attempted | Pinned reviewed authenticated installable artifacts, separate unprivileged reconstruction, exact source/input and output hashes, and retained reconstruction result/terminal with independent review |
| A3 refusal behavior and unattested rows | Audit drift refusals for named retained inputs | Complete-lock refusal of absent/changed/transitively substituted artifacts and uncontrolled data paths; all 484 unhashed RECORD rows remain unattested; point-of-use enforcement is separately A4 |
| A3 complete-lock note | Correctly preserved by this review | Authenticated binary artifacts may support reproducible exact installation without rebuilding every dependency. A replacement build requires regenerated affected observations, MEMFS mappings, ABI/resource measurements and reviews; old evidence cannot transfer by version string |

No compound A2 or A3 criterion is closed. This review accepts the exact observation artifact and its explicit limitations; it does not authorize filling `code.dependency_build_lock`, source decoder-build identities or runtime-entrypoint acceptance with this evidence alone.

## Disposition and boundaries

Candidate was clean before and after validation. Candidate and main were not edited; no commit, merge or push occurred. Only the requested external Markdown, terminal JSON and probe file were written. No network/provider request, service change, installation, RECORD rewrite, V10/AxiomTrade access, credential, financial or live action occurred. No full test suite ran. The sandbox failed to initialize its loopback interface; approved external execution was used for local commands, with the same offline/read-only candidate scope.

**ACCEPT_IN_SCOPE for retained observations only; A2/A3 UNQUALIFIED, G3-L NO-GO, NOT_READY_TO_FUND.**
