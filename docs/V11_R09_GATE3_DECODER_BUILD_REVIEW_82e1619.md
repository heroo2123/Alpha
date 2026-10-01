# Decoder correction exact review: 82e1619

**PASS for integration as an offline observation only. Build NOT_QUALIFIED;
G3-L NO-GO.** Reviewer: GPT-6 Astra/high, independent of Sonnet/high author.
Exact candidate `82e16191fb3b0dc4ca6f4cb068e1cba748957c48`, tree
`3d8a7d3a1bb5815d2320110d115a0694960d713b`; main at review `493b63f`.
The three observation artifacts must be read with the errata below. This
accepts their corrected evidence scope, not native package trust, source
qualification, CCSDS permission or an executable release.

## Independent reproduction and prior findings

A clean detached checkout reproduced both decoder source hashes, all 42
listed mapped-library hashes/sizes and both template-42 synthetic IFS/AIFS
results (290 K, 205/208 bytes). Reviewer containment denied native socket
creation before decoder import, verified IPv4/IPv6 EPERM and no inherited
sockets; CPU 60 seconds, address space 512 MiB, outer timeout 90 seconds.
Observed peak RSS 88,592 KiB and CPU 0.83 seconds are diagnostic only.
No provider request, installation, native mutation or authority change.

- **R1 closed:** independent CSV parsing and streaming hashes reproduce all
  four package audits: 52/69/776/8 rows, 21/2/2/2 unhashed rows, no missing
  hashed files, and exactly 25 eckitlib mismatches. Every expected/observed
  hash, size and delta agrees with the corrected JSON. Deltas are
  3,368–213,008 bytes. Separately reread the existing cached archive and all
  25 vendored payloads: installed bytes match cached bytes exactly; the
  cached wheel itself disagrees with those 25 RECORD rows. The whole RECORD
  differs from the installed RECORD. Origin and cause remain unresolved.
- **R2 closed:** probe documentation now states the actual observation-only
  exit contract. Repeated the in-memory negative contract probe: wrong venv,
  source drift, dirty repo and injected mismatch/missing observations still
  yield zero, as now documented. This is not a drift validator. Actual
  read/import exceptions remain uncaught. No installed byte was modified.
- **R3 closed:** JSON lists precisely the 42 substring-selected objects.
  Independently captured maps contain 36 additional objects, including
  reviewer-only libseccomp. Both formats explicitly preserve the incomplete
  production dependency and point-of-use identity gap.
- **R4 closed:** both formats list six unresolved items, five
  MISSING_EVIDENCE and one OBSERVATION_FOR_REVIEW. Resource qualification is
  explicitly absent, and all 27 unhashed RECORD rows are unattested.

## Nonblocking narrative errata

The eckit table's `libeckit.so (+12 libeckit_*.so)` is off by one: maps and
JSON contain **12 eckit/lib64 objects total**, the core plus 11 others.
Together with five eccodeslib objects and 25 vendored objects this is 42.
The 36 excluded objects **include** reviewer libseccomp; the Markdown's
"plus" wording must not be read as a thirty-seventh object. The current
88,592-KiB RSS exceeds the historical examples slightly; those examples are
not maximum-memory claims. These are scoped narrative clarifications; the
corrected machine evidence is accurate and no trust boundary changes.

## Qualification and reconciliation boundary

Retain every open gap: upstream provenance and mismatch cause, embedded
MEMFS file identities, a complete reproducible dependency lock, loaded
artifact verification at use, realistic field resource qualification, exact
source/current-run pins and all remaining G3-L evidence. A four-package list
is not a complete build lock. Python socket replacement alone is not native
network containment. The reviewer deny filter is additional local review
scaffolding, not production qualification.

Only the three new observation files differ from common ancestor `06bab60`;
newer main changes documentation only. Reconciliation must preserve those
exact candidate blobs and every existing newer-main blob. No product code
or test changes, so no release-suite claim or repeat is needed. The accepted
`58a465f` denial fix and `6ec371e` release remain on main.

A fresh offline screen for the **proposed** October 2 acquisition / October 3
target reports 77 missing identities, launchable=false, disk 1,413,423,104
bytes and memory 802,996,224 bytes. The expired October 1 window is not
revived, and this proposed date is not a reviewed freeze or approval. Disk
remains below 2 GiB. Earlier screen's expiry must not be carried forward as
an expiry of this later proposed window. G3-L remains NO-GO.

See [machine evidence](V11_R09_GATE3_DECODER_BUILD_REVIEW_82e1619.json) for
raw evidence hashes/locators, full independent RECORD audit, cached-wheel
comparison and reproducible reviewer scripts. One summary helper initially
used `status` instead of `state` for missing-evidence entries; the actual
screen had already exited 2 and been saved intact. Corrected summary reads
that saved output; no successful launch or extra gate pass is inferred.

Next safe prerequisite: a bounded, static offline MEMFS enumeration candidate.
Local `readelf` shows thousands of exported definition/sample OBJECT symbols
and `codes_memfs_open`/`codes_memfs_exists`. This is a reason to inspect the
exact ELF bytes, not proof that symbol names equal file paths. A candidate
must establish bounds and exact path-to-byte mapping or report the remaining
gap, and receive independent exact review before any identity credit.
No V10, AxiomTrade, financial, service, authority or remote-publication action.
**91/200; formal 1/50; NOT_READY_TO_FUND** unchanged.
