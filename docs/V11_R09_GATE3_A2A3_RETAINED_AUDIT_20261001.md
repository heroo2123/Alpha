# Gate 3 A2/A3 retained-artifact corroboration

**Observation only. A2 and A3 remain unqualified; G3-L remains NO-GO.**
This candidate extends the [A2/A3 dossier](V11_R09_GATE3_A2A3_OFFLINE_DOSSIER_20261001.md)
with a fresh, read-only [audit tool](../tools/v11_r09_gate3_a2a3_retained_audit.py)
and [machine evidence](V11_R09_GATE3_A2A3_RETAINED_AUDIT_20261001.json).
It uses only retained local installed files, the historical reviewed map and
repository bytes. The three environments may share an installation history;
their agreement is not independent upstream authentication.

## What the retained bytes establish

The previously recorded pip-cache wheel at
`/home/alphaadmin/.cache/pip/http-v2/d/b/5/c/1/db5c167b99b3920157e6ef8cd4747df3c0035c5ce559ce4fe109e10c.body`
is **absent now**. Its earlier 35,957,853-byte SHA-256
`71b4059a56d8b35d682b05b0929e848e34750bb4c9e8d7198aa6d0049171c984`
and payload comparison remain historical reviewed evidence; the wheel cannot
be freshly inspected or used for an offline reconstruction. No other original
`eckitlib` wheel or source archive was found in the searched local package
caches and development directories.

The Alpha development venv, ECMWF backfill venv and Brain history venv each
retain `eckitlib-2.3.0.30.dist-info/RECORD` with the same SHA-256,
`fbff0d5c15a6a2ec52984aafd254e15549b5e79b30207999640d4b87c02c2bcf`.
For **each of the 25** `eckitlib.libs` rows named in the earlier dossier,
the audit checks all three RECORD hash/size fields against that dossier and
streams all three actual payload hashes/sizes. All 75 payload reads match
the previously observed installed bytes. For every row, the recorded
hash/size still disagrees with those bytes. The machine evidence lists the
recorded SHA-256/size, all three observed SHA-256/sizes and an explicit
`UNRESOLVED_DO_NOT_QUALIFY` verdict per file. No original, causal patch record
or independent per-file review exists for any of them. The larger size deltas
are observations, not a benign alteration explanation. The installed RECORD
was neither modified nor repaired.

The previously observed 78 native mapped files still match their historical
hashes/sizes. A static `readelf -d` scan of those exact files yields 305
`DT_NEEDED` references. Matching by filename **or ELF SONAME** gives exactly
one candidate in the historical mapped set for every reference, with no
missing or ambiguous name *within that set*. The JSON preserves every edge,
the candidate path and `RPATH`/`RUNPATH` entries. This is a concrete static
cross-check of a historical observation, not a proof of actual loader
resolution, production-only membership, future lazy loads, `dlopen` targets,
search-path safety or an exhaustive interpreter/native closure. The set
includes review-only `libseccomp` and other review-unclassified mappings.

Packaged metadata gives narrower build clues. The retained `eckit.pc` says
`CXX=/opt/rh/gcc-toolset-14/root/usr/bin/c++`; CMake export files name a
`MINSIZEREL` imported configuration; `eccodes_config.h` advertises features
including JPEG, OpenJPEG, PNG, AEC and MEMFS. The JSON hashes those files and
records the selected lines. These installed, self-reported values do not
identify the original compiler binary, complete flags, source revision,
patches, auditwheel stage or build invocation, and do not authenticate the
wheel producer. The repository's hashed requirements files cover a different
application/runtime profile and omit this ecCodes stack; they cannot serve
as its A3 lock.

## Exact remaining evidence

- **A2:** Independently authenticated original package/source artifacts and
  trusted release digests or signatures for all eight Python packages, the
  interpreter and native artifacts; an authenticated `eckitlib` original and
  build/postprocessing records that explain and independently adjudicate
  each of the 25 RECORD discrepancies; exact toolchain binaries, compiler and
  linker options, patches, platform and ABI. The absent cache wheel does not
  satisfy any original-artifact requirement.
- **A3:** An entrypoint-specific Python/interpreter, CFFI/NumPy, native loader,
  transitive and lazy-load closure with actual loader selection, environment,
  symlink and search rules; original definition/sample revision and selected
  data path; authenticated pinned installable artifacts and a separate
  unprivileged offline reconstruction with exact output hashes and terminal.
  No reconstruction was attempted. Unhashed RECORD rows remain unattested.

No package installation, network or provider request, decoder import, RECORD
rewrite, service change, authority action or financial action occurred. A4
runtime binding and all other Gate 3 requirements remain separate. Fresh
different-model, exact-commit review is required before this candidate can
be credited even at its observation scope.

Reproduce from the checkout with installed bytes still present:

```bash
python3 tools/v11_r09_gate3_a2a3_retained_audit.py --output /tmp/a2a3-retained-rerun.json
```

Compare the rerun JSON excluding `observed_utc`. The tool fails if any of
the 25 RECORD rows, 75 payloads or 78 historical mapped file hashes drift.
