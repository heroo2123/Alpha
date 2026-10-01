# Gate 3 A2/A3 offline dependency and provenance dossier

**Decision: A2 and A3 remain unqualified; G3-L NO-GO.** This is an offline
installed-byte observation at 2026-10-01 22:29 UTC. It extends the accepted
observation-only [build review](V11_R09_GATE3_DECODER_BUILD_REVIEW_82e1619.md)
using the read-only [generator](../tools/v11_r09_gate3_a2a3_dossier.py) and
[machine evidence](V11_R09_GATE3_A2A3_OFFLINE_DOSSIER_20261001.json).
The generator independently rehashed every hashed RECORD row of eight locally
installed Python packages, the cached eckitlib wheel, each discrepant vendored
payload, the resolved Python interpreter and all 78 previously observed native
mapped files. It did not import the decoder, install packages, access providers,
reconstruct an environment, or establish build provenance.

## Local observations versus authenticated lineage

The interpreter symlink resolves to `/usr/bin/python3.12`, SHA-256
`e50d468e8b0adfb05733f5b87b3cff34829c4a8c1aea50c865aa8bdfe4bb150f`,
8,020,928 bytes, ABI `cpython-312-x86_64-linux-gnu`. This is the currently
installed executable, not an authenticated interpreter build or an immutable
launch pin. The cached `eckitlib` wheel body is 35,957,853 bytes, SHA-256
`71b4059a56d8b35d682b05b0929e848e34750bb4c9e8d7198aa6d0049171c984`.
Its origin is not authenticated. No `direct_url.json` exists for any of the
eight packages below. Installed metadata supplies names, versions and declared
dependencies, but no trusted original archive digest or build recipe.

| Package | Installed version | RECORD rows | Unhashed rows | Hash mismatches | Declared production dependencies |
| --- | --- | ---: | ---: | ---: | --- |
| eccodes | 2.48.0 | 52 | 21 | 0 | NumPy, attrs, CFFI, findlibs, eccodeslib |
| eccodeslib | 2.49.0.30 | 69 | 2 | 0 | eckitlib==2.3.0.30 |
| eckitlib | 2.3.0.30 | 776 | 2 | **25** | none declared |
| findlibs | 0.1.3 | 8 | 2 | 0 | none outside test extra |
| numpy | 2.5.3 | 1,336 | 408 | 0 | none declared |
| attrs | 26.1.0 | 55 | 20 | 0 | none declared |
| cffi | 2.1.1 | 53 | 21 | 0 | pycparser |
| pycparser | 3.0 | 21 | 8 | 0 | none declared |

All hashed entries are present. Unhashed rows are **unattested**, regardless
of why RECORD omits a digest. The eight-package metadata closure is wider than
the prior four-package list, but still not a complete reproducible dependency
lock. The generator also rehashed 42 decoder-filtered native map entries and
36 additional mapped files from the independent review. The latter include
NumPy/OpenBLAS, CFFI, interpreter/system libraries and one reviewer-only
`libseccomp`. These are **observed mappings**, not a proof that every item is
a production dependency or that no other module, loader target or lazy load can
occur. Full path/hash/size/classification is in the JSON. `/MEMFS` definition
and sample bytes are covered by the previously reviewed static containing-byte
inventory, but their upstream source revision and semantics remain unproved.

## All 25 `eckitlib.libs` discrepancies

Each row below has the same independently reproduced finding: its installed
bytes equal the cached wheel payload; both disagree with the wheel's own
unchanged RECORD row. The cause and authenticated original are **unknown for
each file**, so each verdict is `UNRESOLVED_DO_NOT_QUALIFY`. The JSON provides
the complete recorded and observed SHA-256, sizes and per-file verdict.

| Vendored file (`eckitlib.libs/`) | Observed size minus RECORD bytes |
| --- | ---: |
| `libbrotlicommon-6ce2a53c.so.1.0.6` | 4,208 |
| `libbrotlidec-811d1be3.so.1.0.6` | 4,112 |
| `libcom_err-730ca923.so.2.1` | 5,904 |
| `libcrypt-52aca757.so.1.1.0` | 3,464 |
| `libcrypto-a324fe28.so.1.1.1k` | 93,360 |
| `libcurl-f8def1d0.so.4.5.0` | 12,304 |
| `libgssapi_krb5-323bbd21.so.2.2` | 12,304 |
| `libidn2-2f4a5893.so.0.3.6` | 4,112 |
| `libk5crypto-9a74ff38.so.3.1` | 4,112 |
| `libkeyutils-2777d33d.so.1.6` | 4,248 |
| `libkrb5-a55300e8.so.3.3` | 20,496 |
| `libkrb5support-e6594cfc.so.0.1` | 4,112 |
| `liblber-2-d20824ef.4.so.2.10.9` | 3,368 |
| `libldap-2-cea2a960.4.so.2.10.9` | 16,400 |
| `liblz4-0414571a.so.1.10.0` | 7,400 |
| `libnghttp2-39367a22.so.14.17.0` | 7,376 |
| `libpcre2-8-516f4c9d.so.0.7.1` | 3,952 |
| `libproj-e5164a33.so.25.9.8.1` | 213,008 |
| `libpsl-99becdd3.so.5.3.1` | 4,112 |
| `libsasl2-7de4d792.so.3.0.0` | 4,112 |
| `libselinux-d0805dcb.so.1` | 8,208 |
| `libsqlite3-1ec6053f.so.0.8.6` | 10,744 |
| `libssh-05a03e26.so.4.8.7` | 16,400 |
| `libssl-8248cfc9.so.1.1.1k` | 20,496 |
| `libunistring-05abdd40.so.2.1.0` | 15,608 |

The varying deltas provide no causal explanation. A matching cached payload
does not make the archive independent, authentic or internally consistent.
Do not repair RECORD, infer a benign patch, reinstall in place, or grant trust
from the version string.

## Exact missing evidence and next bounded action

1. Obtain independently authenticated original wheel/source bytes and trusted
   origin references for all eight packages, plus the exact interpreter and
   native libraries. Compare the original `eckitlib` wheel, its RECORD and each
   of the 25 payloads; document an evidence-backed cause and reviewer verdict
   per row. Until then all 25 remain unresolved.
2. Pin and review build recipe, compiler/linker/toolchain, auditwheel and other
   postprocessing, options, patches, platform and ABI for the chosen native
   artifacts. Bind original ecCodes/eckit definition and sample revisions.
3. Produce a complete Python, wrapper, native-loader, transitive library and
   data/search-path lock for the actual runtime entrypoint. Separate test and
   reviewer overhead by before/after or equivalent controlled observation;
   classify lazy loads and environment overrides. Hashing 78 historical map
   entries does not close this requirement.
4. Reconstruct the selected environment from pinned authenticated artifacts
   in a separate unprivileged location; compare every resulting artifact and
   record the exact reconstruction terminal. This has **not** been attempted.
   Only then can a different-model exact review consider A2/A3 acceptance.

The owner's no-provider-request-before-G3-L-PASS boundary remains in force.
The queued A1 observer repair is separate. A4 point-of-use runtime binding,
A5/A6 source/current-run identities and A8 launch composition remain open.
No code, gate, service, authority or financial state changed. No C/J/E/A
boundary crossed: **91/200 (45.5%), formal 1/50; NOT_READY_TO_FUND**.
