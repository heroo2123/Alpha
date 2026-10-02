"""Offline A4 Gate 3 bootstrap closure-specification structural checker (B1 slice).

Pure bounded, in-process JSON structural validation of a proposed native/bootstrap
closure specification. This is an engineering-proposal checker only: it never
reads or discovers filesystem state, spawns a process, loads native code, imports
anything from a proposed runtime image, or contacts a provider/network. A result
that is structurally valid shows only that the submitted proposal is internally
self-consistent by its own declarations -- never that the described artifacts
exist, match real bytes, or form a complete transitive native closure. Declared
graph reachability is self-declared-graph consistency only; a dangling or
unreachable entry in a hostile fixture, or a correct reachable synthetic one,
says nothing about real native completeness. Every result returned by this
module, including parse rejections and a structurally complete synthetic
proposal, unconditionally carries qualification="UNQUALIFIED", launchable=False
and a4_pass=False. See
docs/V11_R09_GATE3_A4_BOOTSTRAP_DESIGN_20261002.md and
docs/V11_R09_GATE3_A4_CLOSURE_SPEC_SCOPE_20261002.md for the governing design,
independent review scope and B1 boundaries. This module performs no production
lock acceptance, no acceptance-token issuance, no auto-filled genuine evidence,
no A8/G3-L conversion and no native execution.
"""

from __future__ import annotations

import json
import math
import re
import sys
from typing import Any

SCHEMA_ID = "ALPHA_V11_A4_CLOSURE_SPEC_V1"

MAX_RAW_BYTES = 262_144          # 256 KiB bound on the raw proposal byte string.
MAX_JSON_DEPTH = 16              # Bound on container nesting, checked pre-parse.
MAX_STRING_LEN = 512
MAX_ARTIFACTS = 512
MAX_DEPENDENCIES = 4096
MAX_LIST_LEN = 512
MAX_ARTIFACT_SIZE = 2 ** 40      # 1 TiB per-artifact bound; keeps sums in-range.
# Strictly below MAX_ARTIFACTS * MAX_ARTIFACT_SIZE (2**49) so the bound below
# is actually reachable and enforced, not dead arithmetic.
MAX_TOTAL_SIZE = 2 ** 45

_ID_RE = re.compile(r"\A[A-Za-z0-9_.-]{1,128}\Z")
_HASH_RE = re.compile(r"\A[0-9a-f]{64}\Z")
_STAGE_RE = re.compile(r"\AS[0-6]\Z")
_ROLES = ("bootstrap", "runtime", "test")
_KINDS = ("python", "native", "data")
_EVIDENCE_CATEGORIES = ("A2", "A3", "MEMFS")

_TOP_KEYS = frozenset({
    "schema", "entrypoint_id", "artifacts", "dependencies",
    "unresolved_obligations", "evidence_refs", "bootstrap_declaration",
    "interpreter_loader_declaration", "data_selection_declaration",
})
_ARTIFACT_KEYS = frozenset({"id", "path", "role", "kind", "sha256", "size_bytes"})
_DEPENDENCY_KEYS = frozenset({"from", "to"})
_EVIDENCE_KEYS = frozenset({"category", "locator", "sha256"})
_BOOTSTRAP_KEYS = frozenset({"entry_artifact_id", "trust_anchor", "stages"})
_LOADER_KEYS = frozenset({"interpreter_artifact_id", "loader_rules"})
_DATA_KEYS = frozenset({"data_artifact_ids", "selection_rule"})


class ClosureSpecParseError(ValueError):
    """Raised only for raw-byte rejection: oversize, invalid UTF-8, nesting
    depth, duplicate object keys or nonfinite numeric literals. Structural
    content problems never raise; they are collected into the result instead.
    """


def _fixed_fields() -> dict:
    return {"qualification": "UNQUALIFIED", "launchable": False, "a4_pass": False}


def _reject_constant(token: str) -> float:
    raise ClosureSpecParseError(f"NONFINITE_VALUE_REJECTED:{token}")


def _parse_finite_float(token: str) -> float:
    value = float(token)
    if not math.isfinite(value):
        raise ClosureSpecParseError(f"NONFINITE_VALUE_REJECTED:{token}")
    return value


def _check_nesting_depth(text: str) -> None:
    depth = 0
    in_string = False
    escape = False
    for ch in text:
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in "{[":
            depth += 1
            if depth > MAX_JSON_DEPTH:
                raise ClosureSpecParseError("JSON_NESTING_DEPTH_EXCEEDED")
        elif ch in "}]":
            depth -= 1
    if in_string:
        raise ClosureSpecParseError("JSON_UNTERMINATED_STRING")


def _dedupe_object_pairs(pairs: list) -> dict:
    seen: dict = {}
    for key, value in pairs:
        if key in seen:
            raise ClosureSpecParseError(f"DUPLICATE_KEY_REJECTED:{key}")
        seen[key] = value
    return seen


def parse_strict(raw: bytes) -> Any:
    """Parse bounded raw JSON bytes with strict duplicate-key and nonfinite
    rejection, before any structural validation runs. Raises
    ClosureSpecParseError on any violation; never returns a partially accepted
    document.
    """
    if not isinstance(raw, (bytes, bytearray)):
        raise ClosureSpecParseError("RAW_INPUT_NOT_BYTES")
    if len(raw) == 0:
        raise ClosureSpecParseError("RAW_INPUT_EMPTY")
    if len(raw) > MAX_RAW_BYTES:
        raise ClosureSpecParseError("RAW_INPUT_TOO_LARGE")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ClosureSpecParseError("RAW_INPUT_NOT_UTF8") from exc
    _check_nesting_depth(text)
    try:
        return json.loads(
            text,
            object_pairs_hook=_dedupe_object_pairs,
            parse_constant=_reject_constant,
            parse_float=_parse_finite_float,
        )
    except ClosureSpecParseError:
        raise
    except json.JSONDecodeError as exc:
        raise ClosureSpecParseError(f"JSON_SYNTAX_ERROR:{exc.msg}") from exc
    except ValueError as exc:
        # CPython's integer digit guard raises ValueError inside json.loads.
        raise ClosureSpecParseError("JSON_NUMBER_REJECTED") from exc


def _is_strict_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_bounded_string(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    if not (1 <= len(value) <= MAX_STRING_LEN):
        return False
    if "\x00" in value:
        return False
    return True


def _check_exact_keys(obj: Any, expected: frozenset, where: str, errors: list) -> bool:
    if not isinstance(obj, dict):
        errors.append(f"NOT_AN_OBJECT:{where}")
        return False
    actual = frozenset(obj.keys())
    missing = expected - actual
    extra = actual - expected
    ok = True
    for key in sorted(missing):
        errors.append(f"MISSING_KEY:{where}.{key}")
        ok = False
    for key in sorted(extra):
        errors.append(f"UNKNOWN_KEY:{where}.{key}")
        ok = False
    return ok


def _check_path(path: str) -> bool:
    if not _is_bounded_string(path):
        return False
    if path.startswith("/") or path.startswith("\\"):
        return False
    if "\x00" in path:
        return False
    segments = path.split("/")
    if any(seg in ("", ".", "..") for seg in segments):
        return False
    return True


def _validate_artifacts(doc: dict, errors: list):
    artifacts = doc.get("artifacts")
    if not isinstance(artifacts, list) or not (1 <= len(artifacts) <= MAX_ARTIFACTS):
        errors.append("ARTIFACTS_BOUNDS_VIOLATION")
        return {}, 0
    by_id: dict = {}
    seen_paths: dict = {}
    seen_hash_size: dict = {}
    total_size = 0
    for index, artifact in enumerate(artifacts):
        where = f"artifacts[{index}]"
        if not _check_exact_keys(artifact, _ARTIFACT_KEYS, where, errors):
            continue
        art_id = artifact["id"]
        path = artifact["path"]
        role = artifact["role"]
        kind = artifact["kind"]
        sha256 = artifact["sha256"]
        size_bytes = artifact["size_bytes"]
        valid = True
        if not (isinstance(art_id, str) and _ID_RE.match(art_id)):
            errors.append(f"INVALID_ARTIFACT_ID:{where}")
            valid = False
        if not _check_path(path):
            errors.append(f"INVALID_ARTIFACT_PATH:{where}")
            valid = False
        if role not in _ROLES:
            errors.append(f"INVALID_ARTIFACT_ROLE:{where}")
            valid = False
        if kind not in _KINDS:
            errors.append(f"INVALID_ARTIFACT_KIND:{where}")
            valid = False
        if not (isinstance(sha256, str) and _HASH_RE.match(sha256)):
            errors.append(f"INVALID_ARTIFACT_HASH:{where}")
            valid = False
        if not _is_strict_int(size_bytes) or not (0 <= size_bytes <= MAX_ARTIFACT_SIZE):
            errors.append(f"INVALID_ARTIFACT_SIZE:{where}")
            valid = False
        if not valid:
            continue
        if art_id in by_id:
            errors.append(f"DUPLICATE_ARTIFACT_ID:{art_id}")
            continue
        if path in seen_paths:
            errors.append(f"DUPLICATE_ARTIFACT_PATH:{path}")
            continue
        hash_size_key = (sha256, size_bytes)
        if hash_size_key in seen_hash_size:
            errors.append(
                f"HASH_ALIAS_COLLISION:{art_id}~{seen_hash_size[hash_size_key]}"
            )
            continue
        total_size += size_bytes
        if total_size > MAX_TOTAL_SIZE:
            errors.append("ARITHMETIC_BOUND_EXCEEDED:total_artifact_size")
            return by_id, total_size
        seen_paths[path] = art_id
        seen_hash_size[hash_size_key] = art_id
        by_id[art_id] = {"role": role, "kind": kind}
    return by_id, total_size


def _validate_dependencies(doc: dict, by_id: dict, errors: list):
    dependencies = doc.get("dependencies")
    if not isinstance(dependencies, list) or len(dependencies) > MAX_DEPENDENCIES:
        errors.append("DEPENDENCIES_BOUNDS_VIOLATION")
        return []
    edges = []
    seen_edges = set()
    for index, dep in enumerate(dependencies):
        where = f"dependencies[{index}]"
        if not _check_exact_keys(dep, _DEPENDENCY_KEYS, where, errors):
            continue
        src, dst = dep["from"], dep["to"]
        if not (isinstance(src, str) and isinstance(dst, str)):
            errors.append(f"INVALID_DEPENDENCY_ENDPOINT_TYPE:{where}")
            continue
        if src not in by_id or dst not in by_id:
            errors.append(f"DANGLING_DEPENDENCY_ENDPOINT:{src}->{dst}")
            continue
        if src == dst:
            errors.append(f"SELF_DEPENDENCY_INVALID:{src}")
            continue
        edge = (src, dst)
        if edge in seen_edges:
            errors.append(f"DUPLICATE_DEPENDENCY_EDGE:{src}->{dst}")
            continue
        seen_edges.add(edge)
        edges.append(edge)
    return edges


def _reachable_from(entrypoint_id: str, edges: list) -> set:
    adjacency: dict = {}
    for src, dst in edges:
        adjacency.setdefault(src, []).append(dst)
    visited = set()
    stack = [entrypoint_id]
    while stack:
        node = stack.pop()
        if node in visited:
            continue
        visited.add(node)
        for neighbor in adjacency.get(node, ()):
            if neighbor not in visited:
                stack.append(neighbor)
    return visited


def _has_cycle(edges: list) -> bool:
    adjacency: dict = {}
    for src, dst in edges:
        adjacency.setdefault(src, []).append(dst)
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict = {}

    def visit(node: str) -> bool:
        color[node] = GRAY
        for neighbor in adjacency.get(node, ()):
            state = color.get(neighbor, WHITE)
            if state == GRAY:
                return True
            if state == WHITE and visit(neighbor):
                return True
        color[node] = BLACK
        return False

    for node in list(adjacency.keys()):
        if color.get(node, WHITE) == WHITE:
            if visit(node):
                return True
    return False


def _validate_entrypoint(doc: dict, by_id: dict, errors: list):
    entrypoint_id = doc.get("entrypoint_id")
    if not isinstance(entrypoint_id, str) or not _ID_RE.match(entrypoint_id):
        errors.append("INVALID_ENTRYPOINT_ID")
        return None
    if entrypoint_id not in by_id:
        errors.append(f"DANGLING_ENTRYPOINT:{entrypoint_id}")
        return None
    if by_id[entrypoint_id]["role"] != "bootstrap":
        errors.append("ENTRYPOINT_NOT_BOOTSTRAP_ROLE")
        return None
    return entrypoint_id


def _validate_bootstrap_declaration(doc: dict, by_id: dict, entrypoint_id, errors: list):
    decl = doc.get("bootstrap_declaration")
    if not _check_exact_keys(decl, _BOOTSTRAP_KEYS, "bootstrap_declaration", errors):
        return
    entry_ref = decl["entry_artifact_id"]
    if entrypoint_id is not None and entry_ref != entrypoint_id:
        errors.append("BOOTSTRAP_ENTRY_MISMATCH")
    elif not (isinstance(entry_ref, str) and entry_ref in by_id):
        errors.append("DANGLING_BOOTSTRAP_ENTRY_REF")
    if not _is_bounded_string(decl["trust_anchor"]):
        errors.append("INVALID_BOOTSTRAP_TRUST_ANCHOR")
    stages = decl["stages"]
    if not isinstance(stages, list) or not (1 <= len(stages) <= 7):
        errors.append("BOOTSTRAP_STAGES_BOUNDS_VIOLATION")
        return
    seen = set()
    for stage in stages:
        if not (isinstance(stage, str) and _STAGE_RE.match(stage)):
            errors.append(f"INVALID_BOOTSTRAP_STAGE:{stage!r}")
            continue
        if stage in seen:
            errors.append(f"DUPLICATE_BOOTSTRAP_STAGE:{stage}")
            continue
        seen.add(stage)


def _validate_loader_declaration(doc: dict, by_id: dict, errors: list):
    decl = doc.get("interpreter_loader_declaration")
    if not _check_exact_keys(decl, _LOADER_KEYS, "interpreter_loader_declaration", errors):
        return
    interp_ref = decl["interpreter_artifact_id"]
    if not (isinstance(interp_ref, str) and interp_ref in by_id):
        errors.append("DANGLING_INTERPRETER_REF")
    else:
        entry = by_id[interp_ref]
        if entry["role"] != "runtime" or entry["kind"] not in ("python", "native"):
            errors.append("INTERPRETER_ARTIFACT_INVALID_ROLE_OR_KIND")
    rules = decl["loader_rules"]
    if not isinstance(rules, list) or not (1 <= len(rules) <= 64):
        errors.append("LOADER_RULES_BOUNDS_VIOLATION")
        return
    seen = set()
    for rule in rules:
        if not _is_bounded_string(rule):
            errors.append("INVALID_LOADER_RULE")
            continue
        if rule in seen:
            errors.append(f"DUPLICATE_LOADER_RULE:{rule}")
            continue
        seen.add(rule)


def _validate_data_selection(doc: dict, by_id: dict, errors: list):
    decl = doc.get("data_selection_declaration")
    if not _check_exact_keys(decl, _DATA_KEYS, "data_selection_declaration", errors):
        return
    ids = decl["data_artifact_ids"]
    if not isinstance(ids, list) or not (1 <= len(ids) <= MAX_LIST_LEN):
        errors.append("DATA_SELECTION_BOUNDS_VIOLATION")
    else:
        seen = set()
        for art_id in ids:
            if not isinstance(art_id, str):
                errors.append("INVALID_DATA_SELECTION_REF_TYPE")
                continue
            if art_id in seen:
                errors.append(f"DUPLICATE_DATA_SELECTION_REF:{art_id}")
                continue
            seen.add(art_id)
            if art_id not in by_id:
                errors.append(f"DANGLING_DATA_SELECTION_REF:{art_id}")
                continue
            if by_id[art_id]["kind"] != "data":
                errors.append(f"DATA_SELECTION_ARTIFACT_INVALID_KIND:{art_id}")
    if not _is_bounded_string(decl["selection_rule"]):
        errors.append("INVALID_DATA_SELECTION_RULE")


def _validate_unresolved_obligations(doc: dict, errors: list):
    obligations = doc.get("unresolved_obligations")
    if not isinstance(obligations, list) or not (1 <= len(obligations) <= MAX_LIST_LEN):
        errors.append("UNRESOLVED_OBLIGATIONS_BOUNDS_VIOLATION")
        return
    seen = set()
    for item in obligations:
        if not _is_bounded_string(item):
            errors.append("INVALID_UNRESOLVED_OBLIGATION")
            continue
        if item in seen:
            errors.append("DUPLICATE_UNRESOLVED_OBLIGATION")
            continue
        seen.add(item)


def _validate_evidence_refs(doc: dict, errors: list):
    refs = doc.get("evidence_refs")
    if not isinstance(refs, list) or not (1 <= len(refs) <= MAX_LIST_LEN):
        errors.append("EVIDENCE_REFS_BOUNDS_VIOLATION")
        return
    categories_present = set()
    for index, ref in enumerate(refs):
        where = f"evidence_refs[{index}]"
        if not _check_exact_keys(ref, _EVIDENCE_KEYS, where, errors):
            continue
        category = ref["category"]
        locator = ref["locator"]
        sha256 = ref["sha256"]
        if category not in _EVIDENCE_CATEGORIES:
            errors.append(f"INVALID_EVIDENCE_CATEGORY:{where}")
            continue
        if not _is_bounded_string(locator):
            errors.append(f"INVALID_EVIDENCE_LOCATOR:{where}")
            continue
        if sha256 is not None and not (isinstance(sha256, str) and _HASH_RE.match(sha256)):
            errors.append(f"INVALID_EVIDENCE_HASH:{where}")
            continue
        categories_present.add(category)
    for category in _EVIDENCE_CATEGORIES:
        if category not in categories_present:
            errors.append(f"MISSING_EVIDENCE_REF_CATEGORY:{category}")


def validate_structure(doc: Any) -> dict:
    """Run the bounded structural checks on an already strictly-parsed document.

    Never raises; all problems are collected into the returned result's
    ``errors`` list. ``accepted`` is True only when the proposal is self-
    consistent by its own declarations -- this is never evidence that the
    described native/bootstrap closure is real, complete or reachable.
    """
    errors: list = []
    if not isinstance(doc, dict):
        errors.append("TOP_LEVEL_NOT_AN_OBJECT")
        return {"accepted": False, "errors": errors, **_fixed_fields()}
    _check_exact_keys(doc, _TOP_KEYS, "proposal", errors)
    if doc.get("schema") != SCHEMA_ID:
        errors.append("SCHEMA_MISMATCH")

    by_id, _total_size = _validate_artifacts(doc, errors)
    edges = _validate_dependencies(doc, by_id, errors)
    entrypoint_id = _validate_entrypoint(doc, by_id, errors)
    _validate_bootstrap_declaration(doc, by_id, entrypoint_id, errors)
    _validate_loader_declaration(doc, by_id, errors)
    _validate_data_selection(doc, by_id, errors)
    _validate_unresolved_obligations(doc, errors)
    _validate_evidence_refs(doc, errors)

    cycles_detected = False
    unreachable: list = []
    if entrypoint_id is not None:
        reachable = _reachable_from(entrypoint_id, edges)
        cycles_detected = _has_cycle(edges)
        for art_id, info in by_id.items():
            if info["role"] in ("bootstrap", "runtime") and art_id not in reachable:
                unreachable.append(art_id)
        for art_id in sorted(unreachable):
            errors.append(f"UNREACHABLE_DECLARED_ARTIFACT:{art_id}")

    result = {
        "accepted": len(errors) == 0,
        "errors": sorted(errors),
        "artifact_count": len(by_id),
        "dependency_count": len(edges),
        "cycles_detected": cycles_detected,
        "unreachable_declared": sorted(unreachable),
    }
    result.update(_fixed_fields())
    return result


def evaluate_proposal_bytes(raw: bytes) -> dict:
    """Entry point: strictly parse bounded raw JSON bytes, then run bounded
    structural validation. Always returns a result dict carrying
    qualification="UNQUALIFIED", launchable=False and a4_pass=False, whether
    the raw bytes were rejected at parse time or the parsed document was
    structurally validated.
    """
    try:
        doc = parse_strict(raw)
    except ClosureSpecParseError as exc:
        return {"accepted": False, "errors": [str(exc)], **_fixed_fields()}
    return validate_structure(doc)


def main(argv: list) -> int:
    raw = sys.stdin.buffer.read(MAX_RAW_BYTES + 1)
    result = evaluate_proposal_bytes(raw)
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result["accepted"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
