# Gate 3 MEMFS static inventory: path-to-payload enumeration

**Status: UNREVIEWED/NOT_QUALIFIED. SYMBOL_RANGE_OBSERVATION with exact
path-to-payload resolution for all 7,073 table entries. Build remains
NOT_QUALIFIED; G3-L NO-GO.** This is the "bounded, static offline MEMFS
enumeration candidate" flagged as the next safe prerequisite by the
independent review of
[`82e1619`](V11_R09_GATE3_DECODER_BUILD_REVIEW_82e1619.md). It targets the
exact installed `libeccodes_memfs.so` whose containing-binary SHA-256 was
already recorded in the [decoder build
observation](V11_R09_GATE3_DECODER_BUILD_OBSERVATION_20261001.md), and
closes that document's "Honest attestation boundary" gap for individual
`/MEMFS` definition/sample identities **to the extent statically provable
from this one installed build**. It does not touch, repeat or supersede
R1–R4 of the eckitlib correction, does not install, build, download or
mutate anything, and grants no provider, launch, SHADOW, financial,
promotion or host authority.

Companion machine-readable artifact:
[V11_R09_GATE3_MEMFS_STATIC_OBSERVATION_20261001.json](V11_R09_GATE3_MEMFS_STATIC_OBSERVATION_20261001.json)
(4,020,820 bytes; full 7,073-entry inventory). Reproduction script (static,
offline, read-only; never loads the native library, never calls any of its
exported functions, writes no file besides its own stdout):
[V11_R09_GATE3_MEMFS_STATIC_OBSERVATION_20261001_probe.py](V11_R09_GATE3_MEMFS_STATIC_OBSERVATION_20261001_probe.py).

## Input identity (verified before and after this capture)

- Target: `eccodeslib/lib64/libeccodes_memfs.so` inside
  `/home/alphaadmin/AlphaV11_Dev/venv`.
- Byte length: `39,767,864` (matches the prior decoder-build observation).
- SHA-256: `f984057845fd0a569dd775907d4629b83b09434959436863790492aae32e5fb9`.
- Confirmed identical via independent `sha256sum` immediately before this
  capture, by the probe's own internal check (`input_verification` in the
  JSON), after a first probe run, and again after a second independent
  probe run — the file was never opened for writing at any point. ELF
  `GNU_BUILD_ID` note: `7343a92b9537da49d08fc4e2440ac097ac324539` (matches
  the prior observation's table).

## Method: how the real path -> payload mapping was established

No path was ever guessed by replacing underscores in symbol names. The
mapping was recovered by statically disassembling the library's own lookup
code (`objdump -d`, read-only) and then parsing the same ELF structures a
disassembler/`readelf` would, using only `struct` from the Python standard
library:

1. `codes_memfs_open(path)` and `codes_memfs_exists(path)` both call an
   internal `find(path, &size_out)`. `find` linearly scans a fixed array of
   **7,073** 24-byte C structs at the local symbol `entries`
   (`.data.rel.ro`, vaddr `0x25506c0`, size `169,752` = `7073 * 24`),
   comparing each struct's first field against `path` with `strcmp`. The
   loop bound is a literal `cmp rbx, 0x1ba1` (`0x1ba1` = `7073`) baked into
   the disassembly — not an assumption.
2. Each struct is `{ char *name; const unsigned char *data; size_t size; }`.
   On a match, `find` returns `data` and writes `size` through the
   out-pointer; `codes_memfs_open` then calls `fmemopen(data, size, "r")`
   directly on those bytes.
3. Both pointer fields are filled in by this file's own `.rela.dyn`
   relocations, read exactly as the linker/loader would: the `name` field
   is `R_X86_64_RELATIVE` (its addend is the exact string address), and the
   `data` field is `R_X86_64_64` **referencing the exact backing dynsym
   symbol by index** (e.g. `_definitions_boot_def`) with addend `0`. This
   means the symbol that backs each table row is read directly from the
   relocation record, not inferred from the string.
4. All 7,073 `.dynsym` `OBJECT`-type symbols live in section 13
   (`.rodata`), have unique names, and — after resolution — unique
   addresses; these are exactly the `_definitions_*`/`_samples_*`/
   `_ifs_samples_*` symbols the prior observation flagged as a lead.

The probe reimplements this with plain `struct.unpack_from` over the file
bytes (ELF header, program headers for vaddr→file-offset translation,
section headers, `.symtab`/`.dynsym`/`.strtab`/`.dynstr`, `.rela.dyn`), with
no dependency on `readelf`/`objdump`/pyelftools. All `PT_LOAD` segments in
this binary have `p_offset == p_vaddr` (confirmed, not assumed — the probe
raises if this is ever false), so file offset and virtual address coincide
for every byte range resolved here.

### Independent cross-check against a different tool chain

For `_definitions_grib2_templates_template_5_42_def`, `readelf --dyn-syms`
reported vaddr `0x22f34c0`, size `974`; extracting those exact bytes with
`dd if=libeccodes_memfs.so bs=1 skip=36648128 count=974` and `sha256sum`
produced `54013f4db7aebac06cdd37b38243ae8e06871d9e8725dcedf2cbbc7362ad9827`,
which equals the probe's independently-computed value, including
confirming the `.def` text begins `# TEMPLATE 5.42, Grid point data -
CCSDS recommended lossless compression`. This is an independent
`readelf`+`dd`+`sha256sum` reproduction of one parser-reported entry, not a
claim that every one of the 7,073 entries was separately cross-checked this
way (that would exceed this task's bounds); the structural parse itself
(section/program-header offsets, relocation types, loop bound) was
separately confirmed against `readelf -lSW`, `readelf -r --wide` and
`objdump -d` output for the whole file, not only this one symbol.

## Coverage result

**All 7,073 of 7,073 table entries (100%) resolved to an exact path, an
exact exported backing symbol, an exact file-offset byte range, and a
SHA-256 over those exact bytes.** This is a full resolution of the lookup
table the library itself uses, not a partial or best-effort sample:

- `unresolved_count`: 0 (no entry had an unexpected relocation type,
  an out-of-rodata name pointer, a data pointer aliasing a non-`.rodata`
  non-`OBJECT` symbol, a nonzero data-pointer addend, or a payload range
  exceeding the file).
- `aliased_addresses`: 0 — every entry resolves to a distinct symbol
  address; no two paths share backing bytes.
- `overlap_count`: 0 — sorting all 7,073 resolved byte ranges by file
  offset, no range overlaps the next.
- `duplicate_path_count`: 0 — no path string repeats across entries.
- `size_field_mismatch_count`: 0 — the struct's literal `size` field
  equals the backing symbol's `st_size` for every entry (distinguishing
  "symbol size" from "table-declared size": here they always agree, which
  is itself a reportable fact, not assumed).
- Path prefixes: `/MEMFS/definitions/` 6,927; `/MEMFS/samples/` 127;
  `/MEMFS/ifs_samples/` 19 (sum 7,073). The `ifs_samples` prefix was not
  mentioned in the prior observation and is reported here for the first
  time.
- `.dynsym` total 7,082 matches the prior observation's "7,082 total
  dynsym entries" lead exactly: 7,073 `OBJECT` payload symbols plus 9
  non-`OBJECT` entries (`fmemopen`, `__gmon_start__`,
  `_ITM_deregisterTMCloneTable`, `_ITM_registerTMCloneTable`,
  `__cxa_finalize`, `strcmp` — all imported — plus the two exported
  functions `codes_memfs_open`/`codes_memfs_exists` and one empty `UND`
  slot). The prior observation's "thousands of exported OBJECT symbols"
  lead is therefore fully accounted for, not merely consistent with it.

### Storage length and trailing-zero-byte observations

- `trailing_byte_after_payload_is_nul` (the single file byte immediately
  **after** the declared symbol range, i.e. the start of whatever follows
  in `.rodata`): true for 6,850 of 7,073 entries. Checking, for each of
  those 6,850, whether that next byte is the start of the next entry's own
  payload (by file offset) or lies in an unattributed gap before it: **all
  6,850 lie in a gap before the next payload symbol begins — none is the
  first byte of the next entry's own payload.** This is reported only as
  an observation about adjacent bytes; it is not part of any entry's own
  payload and does not change any payload's `symbol_size` or `sha256`.
- `payload_itself_ends_with_embedded_nul` (the **last** byte of the
  payload, i.e. within the declared `size`, is itself `0x00`): true for
  only 3 of 7,073 entries — `/MEMFS/samples/diag.tmpl` (120 bytes, storage
  length unchanged), `/MEMFS/samples/sh_ml_grib1.tmpl` (10,200 bytes,
  storage length unchanged) and `/MEMFS/samples/sh_pl_grib1.tmpl` (9,360
  bytes, storage length unchanged). A single trailing `0x00` byte does not
  by itself establish a string terminator or any "logical content length"
  shorter than the declared/table storage size — binary payloads can
  legitimately end in `0x00` as real content. This document therefore does
  **not** report a derived `logical_content_length` field. Instead, the
  exact trailing run of `0x00` bytes within each payload was counted
  directly: `/MEMFS/samples/diag.tmpl` has **44** trailing zero bytes,
  `/MEMFS/samples/sh_ml_grib1.tmpl` has **106**, and
  `/MEMFS/samples/sh_pl_grib1.tmpl` has **2** (JSON field
  `trailing_zero_byte_count_within_payload`) — not one byte in any of the
  three. For the two GRIB samples, the GRIB header's own encoded message
  length fields are 10,094 and 9,358 bytes respectively; those are
  **GRIB-header-declared lengths**, a different quantity from this
  document's `symbol_size`/table storage byte length (10,200 and 9,360)
  and must not be conflated with it — this document reports the latter
  (the exact bytes compiled into the binary and hashed) as the authoritative
  storage length, and reports the GRIB header fields only as a separate,
  explicitly labeled observation. For the other 7,070 entries no payload
  ends in `0x00` and no such distinction arises. Payload hashes
  (`sha256`) are computed over the full declared `symbol_size` in every
  case and are unaffected by any of this.
- Payload sizes observed range from 12 bytes to 818,602 bytes across the
  7,073 entries (see the JSON `full_entry_inventory` for every value).

### Unattributed `.rodata` residual (explicitly not claimed as enumerated)

`sum(symbol_size)` over all 7,073 entries is `37,791,608` bytes; the
7,073 NUL-terminated path strings add an estimated `339,412` bytes.
`.rodata` itself is `38,243,609` bytes, leaving **112,589 bytes (0.29% of
`.rodata`)** unattributed by this enumeration — alignment padding between
packed objects/strings (symbols are not declared with explicit alignment
in `.symtab`) and a small number of other local constants used by
`codes_memfs_open`/`codes_memfs_exists` themselves (for example, the
one-byte `fmemopen` mode string `"r"` was located immediately adjacent to
the path-string region during manual inspection). This residual is
reported honestly as **unattributed**, not silently folded into the
7,073-entry total and not claimed to be itself individually enumerable
from this candidate.

## Representative spot checks (full set is in the JSON)

| Path | Symbol | Bytes | SHA-256 |
| --- | --- | --- | --- |
| `/MEMFS/definitions/grib2/templates/template.5.42.def` | `_definitions_grib2_templates_template_5_42_def` | 974 | `54013f4db7aebac06cdd37b38243ae8e06871d9e8725dcedf2cbbc7362ad9827` |
| `/MEMFS/definitions/boot.def` | `_definitions_boot_def` | 3,784 | `aefc19ee5ead67d76e2191b2ab761c18b5cb70191c81f8259118c034cde5a067` |
| `/MEMFS/definitions/boot_extra.def` | `_definitions_boot_extra_def` | 998 | `4e8a811a8ad2cfae00ff0024371d84ae4e49df8ec9b9ad3faed4930835ace380` |
| `/MEMFS/samples/BUFR3.tmpl` | `_samples_BUFR3_tmpl` | 231 | `91c7398811ca7ef819f7d08665085a1a8d163780f3cbf82cabaab8297321bd2a` |
| `/MEMFS/ifs_samples/grib1/gg_ml.tmpl` | `_ifs_samples_grib1_gg_ml_tmpl` | 27,596 | `dd7ccb5145e3be0cc96f8b5851e9789e8e101e70c088fde541d330091054347a` |

No literal "signature" filename was found among any of the 7,073 paths
(searched case-insensitively); the task's "sample signatures" is therefore
represented here by the `/MEMFS/samples/*` and `/MEMFS/ifs_samples/*`
entries above rather than by a file named `signature`. This is reported as
a negative search result, not silently dropped.

## Negative/bounds probes (in-memory only, no installed byte touched)

All three ran inside the same bounded process (`RLIMIT_CPU=60s`,
`RLIMIT_AS=256 MiB`; observed peak RSS 69,736 KiB, observed CPU 0.21 s
(0.14 s user + 0.06 s system), wall clock 0.25 s — all far inside bounds):

1. **Out-of-range virtual address.** Calling the vaddr→file-offset
   translator with `0xFFFFFFFFFFFF` (not covered by any `PT_LOAD` segment)
   raises `ValueError` rather than returning a wrong offset. **Rejected:
   true.**
2. **Truncated input.** Parsing ELF/section-header structure from only the
   first 2,000 bytes of the file (an in-memory slice, nothing written to
   disk, nothing read beyond those 2,000 bytes for this sub-probe) raises
   (`struct.error`/`IndexError`/`ValueError`) rather than silently
   returning a partial or wrong structure. **Rejected: true.**
3. **Ambiguous relocation metadata.** Corrupting the first table entry's
   name-slot relocation type to an unrecognized value `99` in a local
   dictionary copy (the real `relocs` dict used for the actual 7,073-entry
   resolution above is never mutated), then calling the **same
   `_resolve_entries()` function used for the real 7,073-entry resolution**
   (not a separate predicate that merely repeats its rejection condition)
   on that one corrupted entry, produces zero resolved results and exactly
   one unresolved entry flagged `NAME_RELOC_UNEXPECTED` — the actual
   resolver rejects it rather than silently accepting it as a valid path
   pointer, and any unrelated exception would propagate rather than being
   counted as a successful rejection. **Rejected: true** (JSON field
   `negative_probes.ambiguous_relocation_type_resolver_result`).

## What this does and does not establish

Establishes, with reproducible local, static, read-only commands:

- The exact mechanism `libeccodes_memfs.so` itself uses to map a requested
  path to backing bytes (disassembly-derived, not guessed).
- An exact path → exported-symbol → file-byte-range → SHA-256 mapping for
  all 7,073 entries in that mechanism's own lookup table — full coverage
  of the table, not `SYMBOL_RANGE_OBSERVATION_ONLY` with a residual path
  gap.
- An explicit, checked distinction between a table-declared size and a
  backing symbol's size (they agree for all 7,073 entries), and, for the 3
  entries whose payload ends in `0x00`, an exact count of trailing zero
  bytes within the declared payload (44, 106 and 2 respectively) reported
  without inferring any unproven "logical content length" or terminator
  semantics from a binary trailing zero.
- An explicit, bounded, honestly-reported residual (112,589 of 38,243,609
  `.rodata` bytes) that this enumeration does not attribute to any path.
- That out-of-range, truncated and relocation-ambiguous inputs are
  rejected rather than silently mishandled, without mutating the installed
  library.

Does **not** establish, and does not claim to establish:

- Upstream provenance or authenticity of any individual definition/sample
  file's *content* — this enumerates and hashes exactly what is compiled
  into this one installed binary; it says nothing about whether that
  content matches any particular upstream ecCodes release, revision or an
  authenticated original-reference byte set. That remains open, same as
  item 2 of the decoder build observation's unresolved list.
- Any GRIB template 5.42 *semantic correctness* — only that the exact bytes
  of that one `.def` file were located, sized and hashed.
- Build qualification, resource qualification, a reproducible lock, loaded-
  artifact verification at point of use, or any other item already left
  open by the [decoder build
  observation](V11_R09_GATE3_DECODER_BUILD_OBSERVATION_20261001.md)'s
  six-item unresolved list or the [decoder/source offline
  assessment](V11_R09_GATE3_DECODER_SOURCE_OFFLINE_ASSESSMENT_20261001.md)'s
  five-item evidence list. None of those are shortened or closed by this
  document.
- Any G3-L identity, CCSDS permission, provider/launch/SHADOW/financial/
  promotion/host authority, or integration credit. This candidate remains
  unreviewed.
- **General malformed-ELF robustness.** The three negative probes above
  are bounded checks against this one pinned binary, not a general
  malformed-ELF validator. This parser's helpers map a starting address
  without validating that an entire range stays in-bounds, hard-code
  `.rodata` as section index 13, permit a C-string read to run past its
  containing segment's declared bounds, and silently overwrite a duplicate
  relocation offset in `_parse_rela_dyn`'s dict rather than flagging the
  collision. None of those conditions occur anywhere in this exact pinned
  library (confirmed by the full-coverage resolution above, which found
  zero unresolved/overlap/duplicate entries), so they do not affect this
  observation's results, but a differently malformed ELF input could
  reach them. This document makes no claim that the parser safely rejects
  arbitrary malformed ELF files in general — only that it correctly and
  verifiably resolves this one pinned, hash-checked binary, and correctly
  rejects the three specific bounded perturbations tested above.

## Host resource observation (passive only)

At capture time: root filesystem free space approximately 1,382,014,976
bytes (about 1.29 GiB), below the 2 GiB G3-L floor; available memory
approximately 728,317,952 bytes. These are passive `df`/`free` observations
made without any provider/service/authority action, consistent with every
prior checkpoint in this slice. G3-L remains **NO-GO**; the prior screen's
77 missing identities are unchanged by this document. **91/200 (45.5%);
formal 1/50 (2%); NOT_READY_TO_FUND**, unchanged by this document — this
candidate records no C/J/E/A credit and creates no G3-L inventory ID.

## Smallest next offline step

Independent exact-commit review of this candidate: re-run the committed
probe against this exact installed file in an isolated environment,
confirm identical input hash, identical 7,073/7,073 resolution with zero
unresolved/overlap/duplicate/mismatch counts, and independently
spot-check the disassembly-derived mechanism (`find`/`codes_memfs_open`)
against this document's claims. After that, the smallest remaining
prerequisite for closing decoder-observation item 3 is still **upstream
authentication** of the enumerated content (this document supplies exact
local bytes and hashes, not an authenticated original-reference set to
compare them against) — unchanged from the decoder build observation's
unresolved item 2/3, and not addressed by any static enumeration of an
already-installed binary alone.

The [decoder build
observation](V11_R09_GATE3_DECODER_BUILD_OBSERVATION_20261001.md), its
[independent review](V11_R09_GATE3_DECODER_BUILD_REVIEW_82e1619.md), the
[decoder/source offline
assessment](V11_R09_GATE3_DECODER_SOURCE_OFFLINE_ASSESSMENT_20261001.md),
[G3-L offline prep](V11_R09_GATE3_G3L_OFFLINE_PREP.md) and [collection
protocol](V11_R09_GATE3_COLLECTION_PROTOCOL.md) remain the governing
requirements. This document creates no G3-L inventory IDs, fabricates no
independent-review reference, and grants no provider, capture, learner,
SHADOW, financial, promotion or host authority.
