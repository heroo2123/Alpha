#!/usr/bin/env python3
"""Static offline MEMFS path-to-payload inventory for libeccodes_memfs.so.

UNREVIEWED/NOT_QUALIFIED. SYMBOL_RANGE_OBSERVATION only insofar as it is a
static byte-layout observation of an already-installed library; it is not a
qualification, acceptance, launch or provenance verdict. G3-L remains NO-GO.

This probe never loads the native library and never calls any of its
exported functions. It only reads the target .so as plain bytes and
statically parses its ELF headers, section headers, symbol tables and
`.rela.dyn` relocations with the Python standard library (`struct`), the
same way `readelf`/`objdump` do, to recover the exact path -> payload
mapping the library's own `find()`/`codes_memfs_open()` functions use at
runtime (reverse-engineered by disassembling those two functions with
`objdump -d`, not guessed from symbol-name underscore substitution).

Mechanism (established by static disassembly, see the companion .md):
`codes_memfs_open(path)` calls an internal `find(path, &size_out)` that
linearly scans a fixed-size array of 7,073 24-byte `{name_ptr, data_ptr,
size}` C structs at the local `entries` symbol, using `strcmp` against
`name_ptr`. On a match it returns `data_ptr` (an exact exported
`_definitions_*`/`_samples_*`/`_ifs_samples_*` OBJECT symbol address) and
writes `size` to the caller. Both pointer fields are themselves resolved
through this file's own `.rela.dyn` relocations (R_X86_64_RELATIVE for the
name string, R_X86_64_64 referencing the exact backing symbol for the data
pointer), not inferred.

Bounds: sets RLIMIT_CPU=60s and RLIMIT_AS=256 MiB before any parsing.
Reads the target file fully into memory once (~40 MB, well under the AS
limit) and performs only read-only byte-range slicing and SHA-256 hashing
over it; writes no file to disk other than this script's own stdout. No
native library is loaded (no `ctypes`, no `dlopen`, no `import eccodes`),
no network, no subprocess that mutates anything. The three negative probes
near the end operate only on in-memory byte slices/dict copies and never
touch the installed file.

Run with any available Python 3 interpreter (standard library only), e.g.:

    /home/alphaadmin/AlphaV11_Dev/venv/bin/python \
      docs/V11_R09_GATE3_MEMFS_STATIC_OBSERVATION_20261001_probe.py
"""
from __future__ import annotations

import hashlib
import json
import platform
import resource
import struct
import sys
import time

TARGET = (
    "/home/alphaadmin/AlphaV11_Dev/venv/lib/python3.12/site-packages/"
    "eccodeslib/lib64/libeccodes_memfs.so"
)
EXPECTED_SIZE = 39767864
EXPECTED_SHA256 = (
    "f984057845fd0a569dd775907d4629b83b09434959436863790492aae32e5fb9"
)

R_X86_64_64 = 1
R_X86_64_RELATIVE = 8
ENTRY_STRIDE = 24
EXPECTED_ENTRY_COUNT = 7073


def _read_target():
    with open(TARGET, "rb") as fh:
        data = fh.read()
    if len(data) != EXPECTED_SIZE:
        raise AssertionError(
            f"size mismatch: expected {EXPECTED_SIZE}, got {len(data)}"
        )
    sha256 = hashlib.sha256(data).hexdigest()
    if sha256 != EXPECTED_SHA256:
        raise AssertionError(
            f"sha256 mismatch: expected {EXPECTED_SHA256}, got {sha256}"
        )
    return data, sha256


def _parse_elf_structure(data):
    if data[:4] != b"\x7fELF" or data[4] != 2 or data[5] != 1:
        raise AssertionError("not a 64-bit little-endian ELF file")
    e_phoff = struct.unpack_from("<Q", data, 0x20)[0]
    e_shoff = struct.unpack_from("<Q", data, 0x28)[0]
    e_phentsize, e_phnum = struct.unpack_from("<HH", data, 0x36)
    e_shentsize, e_shnum, e_shstrndx = struct.unpack_from("<HHH", data, 0x3A)

    segments = []
    for i in range(e_phnum):
        off = e_phoff + i * e_phentsize
        (p_type, _flags, p_offset, p_vaddr, _paddr, p_filesz, _memsz,
         _align) = struct.unpack_from("<IIQQQQQQ", data, off)
        if p_type == 1:  # PT_LOAD
            if p_vaddr != p_offset:
                raise AssertionError(
                    "non-identity PT_LOAD vaddr/offset mapping; this "
                    "parser's file-offset arithmetic assumes vaddr==offset"
                )
            segments.append((p_vaddr, p_offset, p_filesz))

    shstr_hdr_off = e_shoff + e_shstrndx * e_shentsize
    shstr_offset = struct.unpack_from("<Q", data, shstr_hdr_off + 24)[0]
    sections = {}
    for i in range(e_shnum):
        off = e_shoff + i * e_shentsize
        (sh_name, sh_type, _flags, sh_addr, sh_offset, sh_size, sh_link,
         sh_info, _align, sh_entsize) = struct.unpack_from(
            "<IIQQQQIIQQ", data, off
        )
        name_off = shstr_offset + sh_name
        name_end = data.index(b"\x00", name_off)
        name = data[name_off:name_end].decode()
        sections[name] = dict(
            type=sh_type, addr=sh_addr, offset=sh_offset, size=sh_size,
            link=sh_link, info=sh_info, entsize=sh_entsize,
        )
    return segments, sections


def _vaddr_to_off(segments, vaddr):
    for v, o, sz in segments:
        if v <= vaddr < v + sz:
            return o + (vaddr - v)
    raise ValueError(f"vaddr 0x{vaddr:x} is not covered by any PT_LOAD segment")


def _parse_symtab(data, sections, sec_name, str_sec_name):
    sec = sections[sec_name]
    strsec = sections[str_sec_name]
    n = sec["size"] // 24
    syms = []
    for i in range(n):
        off = sec["offset"] + i * 24
        st_name, st_info, _other, st_shndx, st_value, st_size = (
            struct.unpack_from("<IBBHQQ", data, off)
        )
        name_off = strsec["offset"] + st_name
        name_end = data.index(b"\x00", name_off)
        name = data[name_off:name_end].decode()
        syms.append(dict(
            name=name, info=st_info, shndx=st_shndx, value=st_value,
            size=st_size,
        ))
    return syms


def _parse_rela_dyn(data, sections):
    rela = sections[".rela.dyn"]
    n = rela["size"] // 24
    relocs = {}
    for i in range(n):
        off = rela["offset"] + i * 24
        r_offset, r_info, r_addend = struct.unpack_from("<QQq", data, off)
        relocs[r_offset] = (r_info & 0xFFFFFFFF, r_info >> 32, r_addend)
    return relocs, n


def _read_cstr(data, segments, vaddr, max_len=1024):
    off = _vaddr_to_off(segments, vaddr)
    end = data.index(b"\x00", off, off + max_len)
    return data[off:end].decode("utf-8")


def _resolve_entries(data, segments, sections, dynsym, relocs, entries_base, n_entries):
    rodata = sections[".rodata"]
    rodata_lo, rodata_hi = rodata["addr"], rodata["addr"] + rodata["size"]

    results = []
    unresolved = []
    for i in range(n_entries):
        ebase = entries_base + i * ENTRY_STRIDE
        name_reloc = relocs.get(ebase)
        data_reloc = relocs.get(ebase + 8)
        size_off = _vaddr_to_off(segments, ebase + 16)
        literal_size = struct.unpack_from("<Q", data, size_off)[0]

        if name_reloc is None or name_reloc[0] != R_X86_64_RELATIVE:
            unresolved.append({"index": i, "issue": "NAME_RELOC_UNEXPECTED"})
            continue
        if data_reloc is None or data_reloc[0] != R_X86_64_64:
            unresolved.append({"index": i, "issue": "DATA_RELOC_UNEXPECTED"})
            continue

        name_vaddr = name_reloc[2]
        if not (rodata_lo <= name_vaddr < rodata_hi):
            unresolved.append({
                "index": i, "issue": "NAME_PTR_OUTSIDE_RODATA",
                "vaddr": hex(name_vaddr),
            })
            continue
        path = _read_cstr(data, segments, name_vaddr)

        sym_index = data_reloc[1]
        addend = data_reloc[2]
        if not (0 <= sym_index < len(dynsym)):
            unresolved.append({"index": i, "issue": "DATA_SYM_INDEX_OUT_OF_RANGE"})
            continue
        sym = dynsym[sym_index]
        if (sym["info"] & 0xF) != 1 or sym["shndx"] != 13:
            unresolved.append({
                "index": i, "issue": "DATA_SYM_NOT_RODATA_OBJECT",
                "symbol": sym["name"],
            })
            continue
        if addend != 0:
            unresolved.append({
                "index": i, "issue": "NONZERO_DATA_ADDEND", "addend": addend,
            })
            continue

        sym_vaddr, sym_size = sym["value"], sym["size"]
        payload_off = _vaddr_to_off(segments, sym_vaddr)
        if payload_off + sym_size > len(data):
            unresolved.append({
                "index": i, "issue": "PAYLOAD_OUT_OF_FILE_BOUNDS", "path": path,
            })
            continue

        payload = data[payload_off:payload_off + sym_size]
        trailing_byte = data[payload_off + sym_size:payload_off + sym_size + 1]
        ends_with_embedded_nul = sym_size > 0 and payload.endswith(b"\x00")
        trailing_zero_byte_count = 0
        if ends_with_embedded_nul:
            stripped = payload.rstrip(b"\x00")
            trailing_zero_byte_count = len(payload) - len(stripped)
        results.append({
            "index": i,
            "path": path,
            "symbol": sym["name"],
            "symbol_vaddr": sym_vaddr,
            "file_offset": payload_off,
            "symbol_size": sym_size,
            "table_literal_size": literal_size,
            "size_field_matches_symbol_size": literal_size == sym_size,
            "sha256": hashlib.sha256(payload).hexdigest(),
            "trailing_byte_after_payload_is_nul": trailing_byte == b"\x00",
            "payload_itself_ends_with_embedded_nul": ends_with_embedded_nul,
            "trailing_zero_byte_count_within_payload": trailing_zero_byte_count,
        })
    return results, unresolved


def _negative_probes(data, segments, sections, dynsym, relocs, entries_base):
    probes = {}

    # 1. Out-of-range vaddr must be rejected, not silently mapped.
    try:
        _vaddr_to_off(segments, 0xFFFFFFFFFFFF)
        probes["out_of_range_vaddr_rejected"] = False
    except ValueError:
        probes["out_of_range_vaddr_rejected"] = True

    # 2. Truncated input (first 2000 bytes only, in-memory slice) must fail
    #    to parse section headers rather than silently returning wrong data.
    truncated = data[:2000]
    try:
        _parse_elf_structure(truncated)
        probes["truncated_input_rejected"] = False
    except (struct.error, IndexError, ValueError):
        probes["truncated_input_rejected"] = True

    # 3. An entry whose name-slot relocation type is corrupted to an
    #    unexpected value (in a local dict copy only) must be rejected by
    #    the actual resolver used for the real 7,073-entry resolution above
    #    (_resolve_entries), not by a predicate that merely repeats that
    #    function's own rejection condition. The real `relocs` dict is never
    #    mutated; only this local copy is.
    corrupted = dict(relocs)
    real = corrupted[entries_base]
    corrupted[entries_base] = (99, real[1], real[2])
    probe_results, probe_unresolved = _resolve_entries(
        data, segments, sections, dynsym, corrupted, entries_base, 1,
    )
    rejected = (
        len(probe_results) == 0
        and len(probe_unresolved) == 1
        and probe_unresolved[0]["issue"] == "NAME_RELOC_UNEXPECTED"
    )
    probes["ambiguous_relocation_type_rejected"] = rejected
    probes["ambiguous_relocation_type_resolver_result"] = probe_unresolved

    probes["note"] = (
        "All three probes operate on in-memory byte slices or dict copies "
        "only; none opened the target file for writing and none mutated "
        "installed bytes. Probe 3 invokes the same _resolve_entries() "
        "function used for the real 7,073-entry resolution, not a "
        "duplicated predicate, and any unexpected exception propagates "
        "rather than being treated as a successful rejection."
    )
    return probes


def main():
    t0 = time.time()
    resource.setrlimit(resource.RLIMIT_CPU, (60, 60))
    resource.setrlimit(resource.RLIMIT_AS, (256 * 1024 * 1024, 256 * 1024 * 1024))

    report = {"schema": "R09_GATE3_MEMFS_STATIC_OBSERVATION_PROBE_V1"}
    report["observed_utc"] = int(time.time())
    report["status"] = "UNREVIEWED/NOT_QUALIFIED"
    report["g3l_status"] = "NO-GO"
    report["target_path"] = TARGET

    data, sha256 = _read_target()
    report["input_verification"] = {
        "expected_byte_length": EXPECTED_SIZE,
        "observed_byte_length": len(data),
        "expected_sha256": EXPECTED_SHA256,
        "observed_sha256": sha256,
        "matches": sha256 == EXPECTED_SHA256 and len(data) == EXPECTED_SIZE,
    }

    segments, sections = _parse_elf_structure(data)
    report["pt_load_segments"] = [
        {"vaddr": v, "file_offset": o, "file_size": sz} for v, o, sz in segments
    ]
    report["sections_of_interest"] = {
        name: {"offset": sections[name]["offset"], "size": sections[name]["size"]}
        for name in (
            ".symtab", ".strtab", ".dynsym", ".dynstr", ".rela.dyn",
            ".rodata", ".data.rel.ro",
        )
    }

    symtab = _parse_symtab(data, sections, ".symtab", ".strtab")
    entries_syms = [s for s in symtab if s["name"] == "entries"]
    if len(entries_syms) != 1:
        raise AssertionError(
            f"expected exactly one local 'entries' symbol, found {len(entries_syms)}"
        )
    entries_sym = entries_syms[0]
    n_entries = entries_sym["size"] // ENTRY_STRIDE
    report["entries_table"] = {
        "symbol_vaddr": entries_sym["value"],
        "symbol_size": entries_sym["size"],
        "stride_bytes": ENTRY_STRIDE,
        "entry_count": n_entries,
        "entry_count_matches_expected": n_entries == EXPECTED_ENTRY_COUNT,
    }

    dynsym = _parse_symtab(data, sections, ".dynsym", ".dynstr")
    object_syms = [s for s in dynsym if (s["info"] & 0xF) == 1]
    report["dynsym_summary"] = {
        "total_dynsym_entries": len(dynsym),
        "object_type_entries": len(object_syms),
        "non_object_entries": len(dynsym) - len(object_syms),
        "non_object_entry_names": [
            s["name"] for s in dynsym if (s["info"] & 0xF) != 1 and s["name"]
        ],
        "unique_object_names": len({s["name"] for s in object_syms}),
    }

    relocs, n_rela = _parse_rela_dyn(data, sections)
    report["rela_dyn_total_entries"] = n_rela

    results, unresolved = _resolve_entries(
        data, segments, sections, dynsym, relocs,
        entries_sym["value"], n_entries,
    )

    addrs = [r["symbol_vaddr"] for r in results]
    by_off = sorted(results, key=lambda r: r["file_offset"])
    overlaps = []
    for a, b in zip(by_off, by_off[1:]):
        a_end = a["file_offset"] + a["symbol_size"]
        if a_end > b["file_offset"]:
            overlaps.append({
                "a_path": a["path"], "b_path": b["path"],
                "overlap_bytes": a_end - b["file_offset"],
            })

    paths = {}
    for r in results:
        paths.setdefault(r["path"], []).append(r["index"])
    duplicate_paths = {p: idx for p, idx in paths.items() if len(idx) > 1}

    size_mismatches = [
        r for r in results if not r["size_field_matches_symbol_size"]
    ]

    prefix_counts = {"/MEMFS/definitions/": 0, "/MEMFS/samples/": 0, "/MEMFS/ifs_samples/": 0}
    other_prefix = []
    for r in results:
        matched = False
        for p in prefix_counts:
            if r["path"].startswith(p):
                prefix_counts[p] += 1
                matched = True
                break
        if not matched:
            other_prefix.append(r["path"])

    report["coverage"] = {
        "entries_table_denominator": n_entries,
        "resolved_count": len(results),
        "unresolved_count": len(unresolved),
        "unique_symbol_addresses": len(set(addrs)),
        "aliased_addresses": len(addrs) - len(set(addrs)),
        "overlap_count": len(overlaps),
        "overlaps": overlaps,
        "duplicate_path_count": len(duplicate_paths),
        "duplicate_paths": duplicate_paths,
        "size_field_mismatch_count": len(size_mismatches),
        "path_prefix_counts": prefix_counts,
        "paths_outside_known_prefixes": other_prefix,
        "rodata_section_size": sections[".rodata"]["size"],
        "sum_of_resolved_symbol_sizes": sum(r["symbol_size"] for r in results),
        "unattributed_rodata_residual_bytes": (
            sections[".rodata"]["size"]
            - sum(r["symbol_size"] for r in results)
            - sum(len(r["path"]) + 1 for r in results)
        ),
    }
    report["unresolved_entries"] = unresolved

    cross_check_targets = [
        "_definitions_grib2_templates_template_5_42_def",
        "_definitions_boot_def",
        "_definitions_boot_extra_def",
    ]
    report["representative_entries"] = [
        r for r in results if r["symbol"] in cross_check_targets
    ] + [r for r in results if r["path"].startswith("/MEMFS/samples/")][:3] + [
        r for r in results if r["path"].startswith("/MEMFS/ifs_samples/")
    ][:3]

    report["negative_probes"] = _negative_probes(
        data, segments, sections, dynsym, relocs, entries_sym["value"]
    )

    report["full_entry_inventory"] = sorted(results, key=lambda r: r["path"])

    report["interpreter"] = {
        "executable": sys.executable,
        "version": sys.version,
        "platform": platform.platform(),
    }
    usage = resource.getrusage(resource.RUSAGE_SELF)
    report["resource_usage"] = {
        "ru_maxrss_kb": usage.ru_maxrss,
        "ru_utime_s": usage.ru_utime,
        "ru_stime_s": usage.ru_stime,
        "wall_clock_s": time.time() - t0,
    }
    report["disclaimer"] = (
        "UNREVIEWED/NOT_QUALIFIED. Static offline byte-layout observation "
        "only; establishes exact path->symbol->payload bytes as laid out in "
        "this installed binary, not upstream provenance, authenticity, "
        "GRIB-content correctness or any Gate 3/G3-L qualification. No "
        "native library was loaded, no function was called, no network "
        "request was made, and no installed byte was modified. G3-L "
        "remains NO-GO; no provider, financial, promotion or host authority."
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
