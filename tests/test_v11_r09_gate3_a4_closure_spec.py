"""Tests for the bounded offline A4 closure-specification structural checker.

These tests establish only that the checker enforces its own declared bounds
and self-declared-graph consistency rules on synthetic fixtures. A positive
(``accepted: True``) result in any test here is a structurally valid synthetic
proposal, never evidence of a real, complete or reachable native/bootstrap
closure. Fixtures with dangling endpoints, unreachable declarations or known
synthetic edges probe the checker's refusals; they do not imply, and must not
be read as implying, real transitive closure completeness.
"""

import copy
import json

import pytest

from tools.v11_r09_gate3_a4_closure_spec import (
    MAX_ARTIFACTS,
    MAX_JSON_DEPTH,
    MAX_RAW_BYTES,
    SCHEMA_ID,
    ClosureSpecParseError,
    evaluate_proposal_bytes,
    parse_strict,
    validate_structure,
)


def _artifact(art_id, path, role, kind, fill, size):
    return {"id": art_id, "path": path, "role": role, "kind": kind,
            "sha256": fill * 64, "size_bytes": size}


def _valid_doc():
    return {
        "schema": SCHEMA_ID,
        "entrypoint_id": "boot",
        "artifacts": [
            _artifact("boot", "bootstrap/launcher", "bootstrap", "native", "a", 100),
            _artifact("interp", "runtime/python", "runtime", "python", "b", 200),
            _artifact("libnative", "runtime/libnative.so", "runtime", "native", "c", 300),
            _artifact("data1", "data/defs.bin", "runtime", "data", "d", 400),
            _artifact("test1", "tests/test_harness.py", "test", "python", "e", 50),
        ],
        "dependencies": [
            {"from": "boot", "to": "interp"},
            {"from": "interp", "to": "libnative"},
            {"from": "libnative", "to": "data1"},
        ],
        "unresolved_obligations": [
            "A2 lineage not authenticated",
            "A3 reconstruction incomplete",
        ],
        "evidence_refs": [
            {"category": "A2", "locator": "docs/V11_R09_GATE3_A2A3_RETAINED_AUDIT_20261001.md", "sha256": None},
            {"category": "A3", "locator": "docs/V11_R09_GATE3_A2A3_RETAINED_AUDIT_20261001.md", "sha256": None},
            {"category": "MEMFS", "locator": "docs/V11_R09_GATE3_MEMFS_STATIC_OBSERVATION_20261001.json", "sha256": None},
        ],
        "bootstrap_declaration": {
            "entry_artifact_id": "boot",
            "trust_anchor": "external T0 custody pending independent review",
            "stages": ["S0", "S1"],
        },
        "interpreter_loader_declaration": {
            "interpreter_artifact_id": "interp",
            "loader_rules": ["lock interpreter search path"],
        },
        "data_selection_declaration": {
            "data_artifact_ids": ["data1"],
            "selection_rule": "bind exact MEMFS range table",
        },
    }


def _encode(doc):
    return json.dumps(doc).encode("utf-8")


def _fixed_fields_present(result):
    assert result["qualification"] == "UNQUALIFIED"
    assert result["launchable"] is False
    assert result["a4_pass"] is False


# ---------------------------------------------------------------------------
# Fixed-output invariants
# ---------------------------------------------------------------------------

def test_valid_proposal_is_structurally_accepted_but_never_qualified():
    result = evaluate_proposal_bytes(_encode(_valid_doc()))
    assert result["accepted"] is True
    assert result["errors"] == []
    assert result["artifact_count"] == 5
    assert result["cycles_detected"] is False
    assert result["unreachable_declared"] == []
    _fixed_fields_present(result)


def test_parse_rejection_still_carries_fixed_fields():
    result = evaluate_proposal_bytes(b"{")
    assert result["accepted"] is False
    _fixed_fields_present(result)


def test_structural_rejection_still_carries_fixed_fields():
    doc = _valid_doc()
    del doc["artifacts"][0]
    result = evaluate_proposal_bytes(_encode(doc))
    assert result["accepted"] is False
    _fixed_fields_present(result)


# ---------------------------------------------------------------------------
# Raw-byte parse stage: duplicate keys, nonfinite, bounds
# ---------------------------------------------------------------------------

def test_duplicate_key_rejected_before_structural_validation():
    raw = b'{"schema": "X", "schema": "Y"}'
    with pytest.raises(ClosureSpecParseError, match="DUPLICATE_KEY_REJECTED"):
        parse_strict(raw)


def test_nested_duplicate_key_rejected():
    raw = b'{"a": {"x": 1, "x": 2}}'
    with pytest.raises(ClosureSpecParseError, match="DUPLICATE_KEY_REJECTED"):
        parse_strict(raw)


@pytest.mark.parametrize("token", [b"NaN", b"Infinity", b"-Infinity"])
def test_nonfinite_values_rejected(token):
    raw = b'{"size_bytes": ' + token + b'}'
    with pytest.raises(ClosureSpecParseError, match="NONFINITE_VALUE_REJECTED"):
        parse_strict(raw)


def test_oversized_raw_bytes_rejected():
    raw = b'{"pad": "' + b'x' * MAX_RAW_BYTES + b'"}'
    with pytest.raises(ClosureSpecParseError, match="RAW_INPUT_TOO_LARGE"):
        parse_strict(raw)


def test_empty_raw_bytes_rejected():
    with pytest.raises(ClosureSpecParseError, match="RAW_INPUT_EMPTY"):
        parse_strict(b"")


def test_invalid_utf8_rejected():
    with pytest.raises(ClosureSpecParseError, match="RAW_INPUT_NOT_UTF8"):
        parse_strict(b"\xff\xfe\x00")


def test_excess_nesting_depth_rejected():
    raw = b"[" * (MAX_JSON_DEPTH + 1) + b"]" * (MAX_JSON_DEPTH + 1)
    with pytest.raises(ClosureSpecParseError, match="JSON_NESTING_DEPTH_EXCEEDED"):
        parse_strict(raw)


def test_nesting_depth_at_bound_is_allowed():
    raw = b"[" * MAX_JSON_DEPTH + b"]" * MAX_JSON_DEPTH
    assert parse_strict(raw) is not None


def test_malformed_json_syntax_rejected():
    with pytest.raises(ClosureSpecParseError, match="JSON_SYNTAX_ERROR"):
        parse_strict(b"{not json}")


def test_non_bytes_input_rejected():
    with pytest.raises(ClosureSpecParseError, match="RAW_INPUT_NOT_BYTES"):
        parse_strict("not bytes")


# ---------------------------------------------------------------------------
# Top-level schema shape
# ---------------------------------------------------------------------------

def test_unknown_top_level_key_rejected():
    doc = _valid_doc()
    doc["extra_field"] = "nope"
    result = validate_structure(doc)
    assert any("UNKNOWN_KEY:proposal.extra_field" in e for e in result["errors"])
    assert result["accepted"] is False


def test_missing_top_level_key_rejected():
    doc = _valid_doc()
    del doc["unresolved_obligations"]
    result = validate_structure(doc)
    assert any("MISSING_KEY:proposal.unresolved_obligations" in e for e in result["errors"])


def test_schema_mismatch_rejected():
    doc = _valid_doc()
    doc["schema"] = "WRONG_SCHEMA_V1"
    result = validate_structure(doc)
    assert "SCHEMA_MISMATCH" in result["errors"]


def test_top_level_not_object_rejected():
    result = validate_structure(["not", "an", "object"])
    assert result["errors"] == ["TOP_LEVEL_NOT_AN_OBJECT"]
    assert result["accepted"] is False


# ---------------------------------------------------------------------------
# Artifacts: ids, paths, roles, kinds, hashes, sizes, uniqueness
# ---------------------------------------------------------------------------

def test_bool_as_integer_size_rejected():
    doc = _valid_doc()
    doc["artifacts"][0]["size_bytes"] = True
    result = validate_structure(doc)
    assert any("INVALID_ARTIFACT_SIZE" in e for e in result["errors"])


def test_negative_size_rejected():
    doc = _valid_doc()
    doc["artifacts"][0]["size_bytes"] = -1
    result = validate_structure(doc)
    assert any("INVALID_ARTIFACT_SIZE" in e for e in result["errors"])


def test_path_traversal_rejected():
    doc = _valid_doc()
    doc["artifacts"][0]["path"] = "../etc/passwd"
    result = validate_structure(doc)
    assert any("INVALID_ARTIFACT_PATH" in e for e in result["errors"])


def test_absolute_path_rejected():
    doc = _valid_doc()
    doc["artifacts"][0]["path"] = "/etc/passwd"
    result = validate_structure(doc)
    assert any("INVALID_ARTIFACT_PATH" in e for e in result["errors"])


def test_duplicate_artifact_id_rejected():
    doc = _valid_doc()
    clone = copy.deepcopy(doc["artifacts"][1])
    clone["path"] = "runtime/other-path"
    clone["sha256"] = "f" * 64
    doc["artifacts"].append(clone)  # id "interp" repeated
    result = validate_structure(doc)
    assert any("DUPLICATE_ARTIFACT_ID:interp" in e for e in result["errors"])


def test_duplicate_artifact_path_rejected():
    doc = _valid_doc()
    clone = copy.deepcopy(doc["artifacts"][1])
    clone["id"] = "interp2"
    clone["sha256"] = "f" * 64
    doc["artifacts"].append(clone)  # same path as "interp"
    result = validate_structure(doc)
    assert any("DUPLICATE_ARTIFACT_PATH" in e for e in result["errors"])


def test_hash_alias_collision_rejected():
    doc = _valid_doc()
    clone = copy.deepcopy(doc["artifacts"][1])
    clone["id"] = "interp2"
    clone["path"] = "runtime/python-alias"
    # Same sha256 and size as "interp": ambiguous alias target.
    doc["artifacts"].append(clone)
    result = validate_structure(doc)
    assert any("HASH_ALIAS_COLLISION" in e for e in result["errors"])


def test_invalid_hash_format_rejected():
    doc = _valid_doc()
    doc["artifacts"][0]["sha256"] = "not-a-hash"
    result = validate_structure(doc)
    assert any("INVALID_ARTIFACT_HASH" in e for e in result["errors"])


def test_invalid_role_rejected():
    doc = _valid_doc()
    doc["artifacts"][0]["role"] = "launcher"
    result = validate_structure(doc)
    assert any("INVALID_ARTIFACT_ROLE" in e for e in result["errors"])


def test_invalid_kind_rejected():
    doc = _valid_doc()
    doc["artifacts"][0]["kind"] = "binary"
    result = validate_structure(doc)
    assert any("INVALID_ARTIFACT_KIND" in e for e in result["errors"])


def test_artifact_unknown_key_rejected():
    doc = _valid_doc()
    doc["artifacts"][0]["extra"] = "nope"
    result = validate_structure(doc)
    assert any("UNKNOWN_KEY:artifacts[0].extra" in e for e in result["errors"])


def test_empty_artifacts_rejected():
    doc = _valid_doc()
    doc["artifacts"] = []
    result = validate_structure(doc)
    assert "ARTIFACTS_BOUNDS_VIOLATION" in result["errors"]


def test_too_many_artifacts_rejected():
    doc = _valid_doc()
    doc["artifacts"] = [
        _artifact(f"a{i}", f"p/{i}", "test", "python", "a", 1)
        for i in range(MAX_ARTIFACTS + 1)
    ]
    result = validate_structure(doc)
    assert "ARTIFACTS_BOUNDS_VIOLATION" in result["errors"]


# ---------------------------------------------------------------------------
# Dependencies: dangling endpoints, self-loops, duplicates
# ---------------------------------------------------------------------------

def test_dangling_dependency_endpoint_rejected():
    doc = _valid_doc()
    doc["dependencies"].append({"from": "boot", "to": "ghost"})
    result = validate_structure(doc)
    assert any("DANGLING_DEPENDENCY_ENDPOINT:boot->ghost" in e for e in result["errors"])


def test_self_dependency_rejected():
    doc = _valid_doc()
    doc["dependencies"].append({"from": "boot", "to": "boot"})
    result = validate_structure(doc)
    assert any("SELF_DEPENDENCY_INVALID:boot" in e for e in result["errors"])


def test_duplicate_dependency_edge_rejected():
    doc = _valid_doc()
    doc["dependencies"].append({"from": "boot", "to": "interp"})
    result = validate_structure(doc)
    assert any("DUPLICATE_DEPENDENCY_EDGE:boot->interp" in e for e in result["errors"])


def test_dependency_unknown_key_rejected():
    doc = _valid_doc()
    doc["dependencies"][0]["weight"] = 1
    result = validate_structure(doc)
    assert any("UNKNOWN_KEY:dependencies[0].weight" in e for e in result["errors"])


# ---------------------------------------------------------------------------
# Declared-graph reachability: unreachable declarations, legitimate cycles
# ---------------------------------------------------------------------------

def test_unreachable_declared_runtime_artifact_rejected():
    doc = _valid_doc()
    doc["artifacts"].append(_artifact("orphan", "runtime/orphan.so", "runtime", "native", "9", 10))
    # No dependency edge reaches "orphan" from the entrypoint.
    result = validate_structure(doc)
    assert any("UNREACHABLE_DECLARED_ARTIFACT:orphan" in e for e in result["errors"])
    assert "orphan" in result["unreachable_declared"]


def test_unreachable_test_role_artifact_is_exempt():
    # "test1" in the base fixture is declared but intentionally never
    # reachable from the entrypoint; test-role artifacts are harnesses,
    # not part of the enforced native/bootstrap closure.
    result = validate_structure(_valid_doc())
    assert result["accepted"] is True
    assert "test1" not in result["unreachable_declared"]


def test_legitimate_native_cycle_is_accepted_not_flagged():
    doc = _valid_doc()
    doc["artifacts"].append(_artifact("libA", "runtime/libA.so", "runtime", "native", "1", 10))
    doc["artifacts"].append(_artifact("libB", "runtime/libB.so", "runtime", "native", "2", 10))
    doc["dependencies"].append({"from": "libnative", "to": "libA"})
    doc["dependencies"].append({"from": "libA", "to": "libB"})
    doc["dependencies"].append({"from": "libB", "to": "libA"})
    result = validate_structure(doc)
    # Cycle is structurally legitimate: no error, but it is surfaced for review.
    assert result["accepted"] is True
    assert result["cycles_detected"] is True
    assert result["unreachable_declared"] == []


def test_dangling_entrypoint_rejected():
    doc = _valid_doc()
    doc["entrypoint_id"] = "ghost"
    result = validate_structure(doc)
    assert any("DANGLING_ENTRYPOINT:ghost" in e for e in result["errors"])


def test_entrypoint_must_be_bootstrap_role():
    doc = _valid_doc()
    doc["entrypoint_id"] = "interp"
    result = validate_structure(doc)
    assert "ENTRYPOINT_NOT_BOOTSTRAP_ROLE" in result["errors"]


# ---------------------------------------------------------------------------
# Bootstrap / interpreter-loader / data-selection declarations
# ---------------------------------------------------------------------------

def test_bootstrap_declaration_required():
    doc = _valid_doc()
    del doc["bootstrap_declaration"]
    result = validate_structure(doc)
    assert any("MISSING_KEY:proposal.bootstrap_declaration" in e for e in result["errors"])


def test_bootstrap_entry_mismatch_rejected():
    doc = _valid_doc()
    doc["bootstrap_declaration"]["entry_artifact_id"] = "interp"
    result = validate_structure(doc)
    assert "BOOTSTRAP_ENTRY_MISMATCH" in result["errors"]


def test_invalid_bootstrap_stage_rejected():
    doc = _valid_doc()
    doc["bootstrap_declaration"]["stages"] = ["S0", "S9"]
    result = validate_structure(doc)
    assert any("INVALID_BOOTSTRAP_STAGE" in e for e in result["errors"])


def test_duplicate_bootstrap_stage_rejected():
    doc = _valid_doc()
    doc["bootstrap_declaration"]["stages"] = ["S0", "S0"]
    result = validate_structure(doc)
    assert any("DUPLICATE_BOOTSTRAP_STAGE:S0" in e for e in result["errors"])


def test_interpreter_loader_declaration_required():
    doc = _valid_doc()
    del doc["interpreter_loader_declaration"]
    result = validate_structure(doc)
    assert any("MISSING_KEY:proposal.interpreter_loader_declaration" in e for e in result["errors"])


def test_interpreter_artifact_wrong_role_rejected():
    doc = _valid_doc()
    doc["interpreter_loader_declaration"]["interpreter_artifact_id"] = "data1"
    result = validate_structure(doc)
    assert "INTERPRETER_ARTIFACT_INVALID_ROLE_OR_KIND" in result["errors"]


def test_data_selection_wrong_kind_rejected():
    doc = _valid_doc()
    doc["data_selection_declaration"]["data_artifact_ids"] = ["interp"]
    result = validate_structure(doc)
    assert any("DATA_SELECTION_ARTIFACT_INVALID_KIND:interp" in e for e in result["errors"])


def test_data_selection_dangling_ref_rejected():
    doc = _valid_doc()
    doc["data_selection_declaration"]["data_artifact_ids"] = ["ghost"]
    result = validate_structure(doc)
    assert any("DANGLING_DATA_SELECTION_REF:ghost" in e for e in result["errors"])


# ---------------------------------------------------------------------------
# Unresolved obligations and A2/A3/MEMFS evidence references (shape only)
# ---------------------------------------------------------------------------

def test_empty_unresolved_obligations_rejected():
    doc = _valid_doc()
    doc["unresolved_obligations"] = []
    result = validate_structure(doc)
    assert "UNRESOLVED_OBLIGATIONS_BOUNDS_VIOLATION" in result["errors"]


def test_duplicate_unresolved_obligation_rejected():
    doc = _valid_doc()
    doc["unresolved_obligations"] = ["same", "same"]
    result = validate_structure(doc)
    assert "DUPLICATE_UNRESOLVED_OBLIGATION" in result["errors"]


def test_missing_evidence_category_rejected():
    doc = _valid_doc()
    doc["evidence_refs"] = [e for e in doc["evidence_refs"] if e["category"] != "MEMFS"]
    result = validate_structure(doc)
    assert "MISSING_EVIDENCE_REF_CATEGORY:MEMFS" in result["errors"]


def test_evidence_ref_shape_only_no_fetch():
    # An evidence reference with a plausible-looking hash is accepted purely
    # by shape; the checker never opens or verifies the referenced document.
    doc = _valid_doc()
    doc["evidence_refs"][0]["sha256"] = "0" * 64
    result = validate_structure(doc)
    assert result["accepted"] is True


def test_invalid_evidence_hash_rejected():
    doc = _valid_doc()
    doc["evidence_refs"][0]["sha256"] = "short"
    result = validate_structure(doc)
    assert any("INVALID_EVIDENCE_HASH" in e for e in result["errors"])


def test_invalid_evidence_category_rejected():
    doc = _valid_doc()
    doc["evidence_refs"][0]["category"] = "A9"
    result = validate_structure(doc)
    assert any("INVALID_EVIDENCE_CATEGORY" in e for e in result["errors"])


# ---------------------------------------------------------------------------
# Arithmetic bound
# ---------------------------------------------------------------------------

def test_total_size_arithmetic_bound_exceeded_rejected():
    from tools.v11_r09_gate3_a4_closure_spec import MAX_ARTIFACT_SIZE, MAX_TOTAL_SIZE

    doc = _valid_doc()
    needed = MAX_TOTAL_SIZE // MAX_ARTIFACT_SIZE + 2
    assert needed < MAX_ARTIFACTS - len(doc["artifacts"])
    for i in range(needed):
        # Each bulk artifact needs a distinct hash; a repeated hash+size pair
        # would be rejected as a hash-alias collision before the running
        # total ever grows large enough to exercise the arithmetic bound.
        doc["artifacts"].append({
            "id": f"bulk{i}", "path": f"bulk/{i}", "role": "test", "kind": "python",
            "sha256": f"{i:064x}", "size_bytes": MAX_ARTIFACT_SIZE,
        })
    result = validate_structure(doc)
    assert any("ARITHMETIC_BOUND_EXCEEDED" in e for e in result["errors"])


# ---------------------------------------------------------------------------
# CLI smoke test
# ---------------------------------------------------------------------------

def test_cli_main_reports_accepted_proposal(capsys):
    import io

    from tools.v11_r09_gate3_a4_closure_spec import main

    raw = _encode(_valid_doc())
    import sys as _sys
    old_stdin = _sys.stdin
    _sys.stdin = io.TextIOWrapper(io.BytesIO(raw))
    try:
        rc = main([])
    finally:
        _sys.stdin = old_stdin
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert rc == 0
    assert payload["accepted"] is True
    _fixed_fields_present(payload)
