# Gate 3 decoder build: offline byte inventory

**Status: UNREVIEWED_BUILD_OBSERVATION / NOT_QUALIFIED. G3-L remains NO-GO.**
This is a new build-byte inventory of the already-installed Alpha decoder
build and its native dependencies, produced entirely offline from the local
repository worktree and a pre-existing development venv. It is **not** a
repeat of the [decoder/source offline
assessment](V11_R09_GATE3_DECODER_SOURCE_OFFLINE_ASSESSMENT_20261001.md) and
**not** a qualification, acceptance or launch verdict. It advances only item
3 ("exact decoder/dependency build") of that assessment's evidence list. No
provider request, concrete transport wiring or CCSDS enablement follows from
this document. 77 G3-L pre-review identities remain missing; disk on this
host is presently about 1.4 GiB free. **91/200 (45.5%); formal 1/50 (2%);
NOT_READY_TO_FUND.**

Companion machine-readable artifact:
[V11_R09_GATE3_DECODER_BUILD_OBSERVATION_20261001.json](V11_R09_GATE3_DECODER_BUILD_OBSERVATION_20261001.json).
Reproduction script (local observation only, no network, no disk writes
beyond its own stdout):
[V11_R09_GATE3_DECODER_BUILD_OBSERVATION_20261001_probe.py](V11_R09_GATE3_DECODER_BUILD_OBSERVATION_20261001_probe.py).

## Exact identities observed

- Repository: commit `06bab60a5925df86d8ff82be8a9ed57fbf4a7ed7`, tree
  `ba30b72a3a8f4763896e93fc6dd805098f009922` (this worktree's `HEAD` at
  capture time). The two decoder source files were hashed and independently
  confirmed to match that exact committed blob (`git hash-object` on the
  worktree copy equals `git rev-parse HEAD:<path>`):
  - `polymarket_scanner/v11/ecmwf_grib.py` —
    `224ae354c7c80ef2b8ff8206f5ebdaa428b86802eee316e1ae2f4d449c464a3c`,
    12342 bytes.
  - `tools/v11_r09_gate3_offline_io.py` —
    `bca02a320402ca6c0f908cd035fecd08854012c25b744d8cc7436ca370c50235`,
    22644 bytes.
  - At capture time the only untracked paths in this worktree were this
    slice's own three new deliverables (this report, its JSON companion and
    the probe script); no other file in the worktree differed from `HEAD`.
- Interpreter: `/home/alphaadmin/AlphaV11_Dev/venv/bin/python`, CPython
  3.12.3, Ubuntu 24.04.4 LTS, `x86_64`, platform string
  `Linux-6.8.0-137-generic-x86_64-with-glibc2.39`, ABI tag `cpython-312`.
  Confirmed this is the accepted Alpha development venv (path match), not
  the review venv (which lacks ecCodes and would yield three
  `pytest.importorskip` skips instead of a real decode).
- Python wrapper packages, each verified by recomputing SHA-256/size for
  **every** file listed in its own `dist-info/RECORD` (not a broad
  filesystem scan):
  - `eccodes` 2.48.0 — 52 RECORD entries, 0 mismatches, 0 missing. Includes
    the `gribapi` binding, which ships inside this same wheel.
  - `eccodeslib` 2.49.0.30 — 69 RECORD entries, 0 mismatches, 0 missing.
  - `eckitlib` 2.3.0.30 — 776 RECORD entries; **24 mismatches**, 0 missing
    (see "Unresolved identities" below).
  - `findlibs` 0.1.3 — 8 RECORD entries, 0 mismatches, 0 missing.
  - None of the four `dist-info` directories contain a `direct_url.json`, so
    the exact upstream wheel source URL/hash is not locally recoverable.
- Native libraries actually loaded into the process during the bounded
  synthetic decode below (confirmed via `/proc/self/maps` after decode, not
  assumed from package metadata), with SHA-256/size recomputed directly from
  the mapped file path:

  | Library | Bytes | SHA-256 (first 16 hex) | GNU Build ID |
  | --- | --- | --- | --- |
  | `eccodeslib/lib64/libeccodes.so` | 3,330,792 | `1d6b8a3a8206cc43...` | `a2d3a7d075e089d4...` |
  | `eccodeslib/lib64/libeccodes_memfs.so` | 39,767,864 | `f984057845fd0a56...` | `7343a92b9537da49...` |
  | `eccodeslib/lib64/libaec.so.0` | 131,032 | `df7cb2e645c02558...` | `f6624fc53fbabb78...` |
  | `eccodeslib/lib64/libopenjp2.so.7` | 2,399,408 | `a8eecba4c05ed538...` | — |
  | `eccodeslib/lib64/libpng16.so.16` | 221,200 | `ebd1fa42daa29afd...` | — |
  | `eckitlib/lib64/libeckit.so` (+12 `libeckit_*.so`) | 3,342,929 (core) | `b6bdb025bde045d0...` | `26376b695159292f...` |
  | `eckitlib.libs/*` (24 vendored third-party libs: curl, ssl, krb5, ldap, ssh, sqlite3, proj, etc.) | see JSON | see JSON | — |

  Full 42-entry table with every path, hash and size is in the JSON
  companion's `loaded_native_libraries` array.

## Bounded offline capture method

Run with `PYTHONDONTWRITEBYTECODE=1` and the Alpha development venv's
interpreter, from the repository root:

```bash
PYTHONDONTWRITEBYTECODE=1 \
  /home/alphaadmin/AlphaV11_Dev/venv/bin/python \
  docs/V11_R09_GATE3_DECODER_BUILD_OBSERVATION_20261001_probe.py
```

Before importing `eccodes`, the probe script sets `RLIMIT_CPU=60s` and
`RLIMIT_AS=512 MiB`, and replaces `socket.socket` with a subclass whose
`__init__` always raises, then self-tests that the override is in effect.
Observed peak RSS was 88,484 KB and observed CPU time 0.88 s, both far
inside the configured bounds; no temporary files were written and no
provider byte was requested. It then imports `test_v11_grib_fields.grib`
and `test_v11_model_panel.ecmwf_bytes`/`request` — the repository's existing
simple-packing and CCSDS-re-encoded synthetic fixture builders used by
`tests/test_v11_r09_gate3_offline_io.py` — and reuses them unchanged rather
than constructing a new fixture:

- A template-0 (simple packing) synthetic message is built and opened/
  released through `eccodes.codes_new_from_message` to confirm the simple
  path loads cleanly.
- For both `ECMWF_IFS_ENS` and `ECMWF_AIFS_ENS`, the existing AIFS/IFS
  synthetic fixture is re-encoded to `packingType='grid_ccsds'` via ecCodes
  itself (exactly as `test_valid_synthetic_ccsds_stays_unqualified_for_gate3`
  does) and decoded through `polymarket_scanner.v11.ecmwf_grib.decode_station`.
  Both produced template 5.42 and the expected synthetic `290.0 K`.
- `eccodes.codes_get_api_version()` reports `2.49.0`; `eccodes.__version__`
  reports `2.49.0`; `eccodes.codes_definition_path()` and
  `codes_samples_path()` both resolve to `/MEMFS/definitions` and
  `/MEMFS/samples` — an in-memory filesystem compiled directly into
  `libeccodes_memfs.so`, not discrete files on disk (see below).

This reproduces the prior source assessment's observation that "genuine
template 5.42 bytes from ecCodes decode through the ECMWF path for IFS and
AIFS" using only existing, unmodified fixture builders, while adding exact
build-byte identities for the library that performed the decode.

## Honest attestation boundary: `/MEMFS` definitions and samples

`eccodes.codes_definition_path()`/`codes_samples_path()` both point inside
`libeccodes_memfs.so`, a 39,767,864-byte shared object whose SHA-256 is
hashed and RECORD-verified above as a single containing binary. **This is a
containing-library digest, not an enumerated manifest of the individual
GRIB definition/sample files baked into it.** No discrete definitions/samples
files exist on this host's filesystem to hash one by one, and this capture
does not claim otherwise. Resolving individual embedded definition/sample
identities — e.g. to independently confirm which exact ECMWF template 5.42
definition revision is compiled in — would require either a separately
reviewed source build of ecCodes with MEMFS disabled (yielding discrete
filesystem artifacts to hash) or a reviewed, offline extraction/enumeration
tool run against this exact library build. Neither exists yet; this remains
an explicit open requirement, not a filled identity.

## Unresolved identities and remaining gaps

1. **`eckitlib` vendored-library RECORD mismatch (24/776 entries).** Every
   file under `eckitlib.libs/` (the auditwheel-bundled third-party runtime
   libraries — libcurl, libssl, libgssapi_krb5, libldap, libssh, libsqlite3,
   libproj and others) is larger on disk than its own `dist-info/RECORD`
   declares, by 4,096–4,208 bytes in every case, with a correspondingly
   different SHA-256. This is consistent with a post-packaging ELF patch
   (e.g. an RPATH/section rewrite applied after RECORD was generated) but
   this capture does not independently attribute the cause or recover the
   original reference bytes. The core `eckit`/`eckit_*` libraries themselves
   (under `eckitlib/lib64/`) and all of `eccodes`/`eccodeslib`/`findlibs`
   matched their RECORD exactly. **Status: MISSING_EVIDENCE.**
2. **No wheel-origin provenance.** None of the four installed packages have
   a `direct_url.json`; the exact upstream wheel URL/hash that produced this
   venv cannot be confirmed from local metadata alone. **MISSING_EVIDENCE.**
3. **`/MEMFS` definitions/samples not individually attestable**, as above.
   **MISSING_EVIDENCE.**
4. **No reproducible lock file.** The repository does not pin ecCodes (per
   the prior assessment) and this venv has no committed
   requirements/poetry/pip-compile lock recording these exact four package
   versions; rebuilding an equivalent venv from the repository alone is not
   currently reproducible. **MISSING_EVIDENCE.**
5. **Native dependency graph wider than the decode needs.** Merely importing
   `eccodes` eagerly loads `eckitlib`, which pulls in `libcurl`, `libssl`,
   `libgssapi_krb5`, `libldap`, `libssh`, `libsqlite3` and `libproj` even for
   a purely local, in-memory decode with no network or database use. The
   Python-level socket block in this capture only intercepts `socket.socket`;
   it does not prove native code inside those libraries could not open a
   raw file descriptor by other means. No network API of ecCodes/eckit was
   invoked here, and host `/proc/self/maps` after the decode shows only
   library mappings, not open socket file descriptors, but this is an
   **observation for independent review**, not a sandboxing guarantee.
6. Platform/ABI (`cpython-312`, Ubuntu 24.04.4, `x86_64`) and the bounded
   peak RSS (88,484 KB) are recorded as diagnostics only; they are not
   compared against any Gate 3 memory-floor acceptance threshold here —
   that comparison, and a decoder peak-memory measurement under realistic
   (non-synthetic) field sizes, remains a separate, later review task.

None of the above six items are filled with fabricated values; each is
explicit `null`/`MISSING_EVIDENCE` in the companion JSON's
`unresolved_identities` array.

## What this does and does not establish

This inventory establishes, with reproducible local commands:

- The exact repository commit/tree and decoder source-file hashes in use.
- The exact installed Python wrapper and native-library files, their sizes
  and hashes, verified against their own package manifests where available.
- Which of those native libraries are actually mapped into the process by a
  real (synthetic) decode, not merely installed on disk.
- An honest boundary between individually-hashed bytes and a
  containing-binary hash for the `/MEMFS` definitions/samples case.
- A concrete, itemized list of what remains unresolved before this build
  could be called a frozen, reproducible Gate 3 decoder/dependency identity.

It does **not** establish, and does not claim to establish:

- That this build is qualified, accepted, or launch-eligible for Gate 3.
- Any provider/source release identity, licence or access determination —
  that is item 1 of the prior assessment, untouched here.
- Any section-pin or current-run evidence — item 2, untouched here.
- Resource qualification against the G3-L 2 GiB/512 MiB floor, which is a
  window-specific, separately-reviewed measurement.
- That the `eckitlib` RECORD mismatch is benign; it is reported exactly as
  observed and left open for independent review.

## Smallest next offline step

Independently review (a) the 24-file `eckitlib.libs` RECORD mismatch against
the original published wheel artifact (e.g. by downloading the exact
`eckitlib==2.3.0.30` wheel in a separately authorized, reviewed environment
and diffing bytes — not performed here, since this capture permits no
provider/package-index network request) and (b) whether a MEMFS-disabled
ecCodes build is available/required to make the embedded definitions/samples
individually attestable. Both are prerequisites to item 3 of the prior
assessment being closed; items 1, 2, 4 and 5 of that assessment remain
separately open and unaffected by this inventory.

## Acceptance tests still required before any integration credit

- Independent re-run of this exact probe script against this exact venv by
  a reviewer in an isolated environment, confirming identical hashes.
- Resolution of the `eckitlib` RECORD mismatch (item 1 above) with an
  explicit verdict (benign post-packaging patch vs. untrusted artifact).
- A reviewed MEMFS/definitions resolution path (item 3 above).
- A checked-in, reviewed lock file pinning these exact four packages (item
  4 above), without changing the repository's current non-pinned stance
  until that review happens.
- Everything already listed in items 1, 2, 4 and 5 of the [decoder/source
  offline
  assessment](V11_R09_GATE3_DECODER_SOURCE_OFFLINE_ASSESSMENT_20261001.md),
  which this document does not shorten.

The [decoder/source offline
assessment](V11_R09_GATE3_DECODER_SOURCE_OFFLINE_ASSESSMENT_20261001.md),
[G3-L offline prep](V11_R09_GATE3_G3L_OFFLINE_PREP.md), [collection
protocol](V11_R09_GATE3_COLLECTION_PROTOCOL.md) and [launch-readiness
audit](V11_GATE3_LAUNCH_READINESS_AUDIT_20261001.md) remain the governing
requirements. This document creates no G3-L inventory IDs, fabricates no
independent-review reference, and grants no provider, capture, learner,
SHADOW, financial, promotion or host authority.
