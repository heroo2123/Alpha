# Exact decoder-build observation review: 20a42f7

**CHANGES_REQUIRED — do not integrate this candidate. G3-L remains NO-GO.**
Independent reviewer: GPT-6 Astra/high; candidate author: Sonnet/high.
Candidate `20a42f713571824c36236a63eb8870f6478268f8`, tree
`39a67d3e77fb9e9ee78a1717713789d64b1871fa`, parent `06bab60`.
Main was clean at `6225b53`; candidate changes only three new docs artifacts.
Both decoder sources remain identical on newer main. No product change or
launch acceptance follows from this review.

## Verified observations

Two independent runs in clean detached `/tmp/alpha-v11-decoder-review-20a42f7`
reproduced both decoder hashes, every reported RECORD observation, all 42
listed native-library hashes/sizes and both synthetic IFS/AIFS CCSDS results
(template 42, 290 K, 205/208-byte messages). First peak RSS was 88,452 KiB;
this is a tiny synthetic diagnostic, not realistic resource qualification.
The reviewer added a libseccomp network-syscall deny filter before decoder
imports, checked no inherited socket descriptors, and verified native IPv4/
IPv6 socket creation returned EPERM. Existing 60-second CPU/512-MiB address
space limits were retained, with a 90-second outer timeout. No network or
provider request, installation, service change or native-library mutation.
The candidate's Python socket replacement alone is not equivalent containment.

A separate CSV/hash implementation reproduced all four package results:
52/69/776/8 RECORD rows, with 21/2/2/2 unhashed rows respectively. Hashed rows
outside the 25 eckitlib mismatches match; unhashed rows are not attested.
The negative contract probe uses in-memory stubs, changing no installed files.

## Findings requiring correction

1. **R1 — inaccurate mismatch evidence.** There are **25**, not 24, mismatched
   `eckitlib.libs` files. Actual size deltas span **3,368 to 213,008 bytes**,
   not 4,096–4,208. The candidate JSON already lists 25 paths; both narrative
   counts and its unresolved-item description contradict the captured bytes.
   Installed-but-unmapped files must not be confused with mapped libraries.
   Preserve per-file expected/observed hashes, sizes and deltas, and derive
   summaries mechanically. The uniform 4-KiB patch explanation is unsupported.
2. **R2 — false probe exit contract.** Probe lines 16–18 promise nonzero exit
   on venv/RECORD/source mismatch, but line 302 always returns zero. The probe
   never reads its companion manifest. In-memory wrong-venv, dirty-repository,
   source-drift, missing-file and RECORD-mismatch observations all return zero.
   Make the observation-only exit contract explicit, or implement and test a
   separately scoped baseline validator. Exit zero must never imply byte
   acceptance. Preserve truthful NOT_QUALIFIED and known mismatch reporting.
3. **R3 — incomplete dependency inventory scope.** The 42-entry table is
   selected by path substrings, not a complete native dependency graph. The
   review process maps another 36 shared objects, including NumPy/OpenBLAS,
   CFFI and system dependencies (and reviewer-only libseccomp). Do not label
   those 42 as complete. Explicitly retain missing production dependency and
   point-of-use identity coverage; a before/after map would be needed to
   distinguish decoder dependencies from interpreter/test/reviewer overhead.
4. **R4 — inconsistent unresolved list.** Markdown says all six gaps are in
   the JSON array as null/MISSING_EVIDENCE; JSON has five, including an
   OBSERVATION_FOR_REVIEW, and omits resource qualification. Reconcile both
   formats and narrow claims that every RECORD file was hashed: 27 rows lack
   hashes. No missing identity is to be converted into acceptance.

## RECORD discrepancy assessment and local wheel evidence

An offline header/ZIP directory scan of 60 existing pip-cache bodies found
an exact-version eckitlib archive; nothing was fetched, extracted or installed.
Its 35,957,853-byte SHA-256 is
`71b4059a56d8b35d682b05b0929e848e34750bb4c9e8d7198aa6d0049171c984`.
**All 25 installed vendored files match the cached wheel payload exactly;
all 25 cached payload files disagree with the wheel's own RECORD.** Those 25
RECORD rows match installed RECORD rows. Whole RECORD files differ, so the
comparison correctly uses per-file rows (the first whole-RECORD equality
attempt failed before comparison; it is not counted as successful evidence).

Verdict: **local cached artifact internally inconsistent; cause and trusted
upstream identity unresolved**. This supports neither a claim of installed
bytes uniquely changing after this cached artifact nor a benign-patch verdict.
The cached archive is not independently authenticated upstream evidence. No
`direct_url.json` exists in the four packages, but its absence does not prove
there are no other local artifacts: this cached archive is useful partial
provenance. Preserve it; do not repair RECORD or change any library in place.

## MEMFS and qualification decision

The containing `libeccodes_memfs.so` hash reproduces and its package RECORD
matches. Individual embedded definition/sample identities were not enumerated.
Keep that gap open. Nothing reviewed here establishes that a MEMFS-disabled
build is mandatory or that no offline extraction mechanism could exist. A
reviewed enumeration/extraction proposal or separately reviewed build remains
future work; do not install/build/download anything under this review.
A four-package version list is also not a complete reproducible lock.

The original source assessment's provider/run/section identities, full build
freeze, realistic capacity and independent integration review remain required.
G3-L remains NO-GO (last screen: 77 missing and one expired item); physical
free disk is below 2 GiB. No G3-L IDs, CCSDS permission, SHADOW samples or
C/J/E/A credit result. **91/200; formal 1/50; NOT_READY_TO_FUND** unchanged.

## Reproduction and next action

[Machine-readable review](V11_R09_GATE3_DECODER_BUILD_REVIEW_20a42f7.json)
contains exact comparisons, per-file mismatch data and hashes/locators of raw
stdout and review probes. Preserved companion `runner.py`, `contract.py` and
`wheel.py` scripts reproduce this exact local review; paths intentionally name
the immutable detached worktree. Set `PYTHONDONTWRITEBYTECODE=1` and use the
existing `/home/alphaadmin/AlphaV11_Dev/venv/bin/python` for the runner.
The wheel probe reads the cache discovery JSON at its hash-bound local locator.
The seccomp runner is additional reviewer containment, not production policy.

Repair R1–R4 only in the original isolated author worktree, preserving the
candidate commit and all unresolved qualification gaps. Retest the corrected
manifest against captured observations and leave a clean commit/terminal for
fresh different-model exact review before any merge reconciliation. Do not
merge the rejected candidate, download packages/provider bytes, fill G3-L
identities, or alter installed native artifacts.
