"""Offline tests for ``tools/v11_gate3_evidence_preflight_real_intake.py``.

Every fixture here is synthetic/offline, reusing the already-reviewed
``tests/v11_gate3_preflight_synthetic_cases.py`` fabricated bytes, or
freshly fabricated synthetic bytes written to ``tmp_path``. No private
package, private evidence or real provider file is opened by this suite.

No socket, subprocess, credential lookup or write-to-tracked-evidence
happens anywhere in this module or the one under test.
"""

from __future__ import annotations

import ast
import hashlib
import json

import pytest

from tools.v11_gate3_evidence_preflight_checker import (
    ClockObservation, OUTCOME_REFUSED, OUTCOME_SATISFIED, ResourceObservation,
)
from tools.v11_gate3_evidence_preflight_real_intake import (
    MAX_INTAKE_BYTES, NO_QUALIFIED_CLOCK_SECONDS, EvidenceIntakeError,
    RetainedRef, build_report, current_clock_observation,
    current_resource_observation, evaluate_real_evidence, parse_binding,
    read_private_evidence, read_protocol_bytes, run_real_evidence_intake,
    verify_retained_bytes,
)

from tests.v11_gate3_preflight_synthetic_cases import (
    GOOD_CLOCK, GOOD_RESOURCES, GOOD_REVIEW_TERMINAL, RETAINED_22_REASONS,
    all_blocked_package_and_restrictions, encode_raws, good_binding_dict,
    good_package_dict, good_raws, good_restrictions_dict, protocol_raw_bytes,
)


def _ref_for(raw: bytes, path: str) -> dict:
    return {"path": path, "sha256": hashlib.sha256(raw).hexdigest(), "byte_length": len(raw)}


# =============================================================================
# Honest observation builders
# =============================================================================

class TestObservationBuilders:
    def test_current_clock_observation_is_honest_not_a_fabricated_pass(self):
        clock = current_clock_observation()
        assert isinstance(clock, ClockObservation)
        assert clock.source == "LOCAL_AUTHORIZED_ONLY"
        assert clock.monotonic_consistent is True
        # Explicit sentinel, far past the checker's real 1s/60s floors --
        # this must never look like a qualified calibration.
        assert clock.uncertainty_seconds == NO_QUALIFIED_CLOCK_SECONDS
        assert clock.calibration_age_seconds == NO_QUALIFIED_CLOCK_SECONDS
        assert clock.uncertainty_seconds > 1
        assert clock.calibration_age_seconds > 60
        assert clock.measured_utc.endswith("Z")

    def test_current_resource_observation_reports_real_host_counters_and_zero_reservation(self):
        resources = current_resource_observation()
        assert isinstance(resources, ResourceObservation)
        assert resources.physically_reserved_bytes == 0
        assert resources.free_disk_bytes_after_reservation >= 0
        assert resources.mem_available_bytes_after_reservation >= 0


# =============================================================================
# Byte verification -- synthetic files only, never the real private root.
# =============================================================================

class TestVerifyRetainedBytes:
    def test_matching_bytes_pass_through_unchanged(self):
        raw = b"synthetic retained bytes"
        ref = RetainedRef(path="synthetic://whatever", sha256=hashlib.sha256(raw).hexdigest(),
                           byte_length=len(raw))
        assert verify_retained_bytes(raw, ref, label="x") == raw

    def test_length_mismatch_refuses(self):
        raw = b"abc"
        ref = RetainedRef(path="synthetic://x", sha256=hashlib.sha256(raw).hexdigest(), byte_length=4)
        with pytest.raises(EvidenceIntakeError):
            verify_retained_bytes(raw, ref, label="x")

    def test_hash_mismatch_refuses(self):
        raw = b"abc"
        ref = RetainedRef(path="synthetic://x", sha256="0" * 64, byte_length=len(raw))
        with pytest.raises(EvidenceIntakeError):
            verify_retained_bytes(raw, ref, label="x")

    def test_oversized_bytes_refuse_before_hashing(self):
        raw = b"a" * 10
        ref = RetainedRef(path="synthetic://x", sha256=hashlib.sha256(raw).hexdigest(), byte_length=len(raw))
        with pytest.raises(EvidenceIntakeError):
            verify_retained_bytes(raw, ref, label="x", max_bytes=5)

    def test_non_bytes_input_refuses(self):
        ref = RetainedRef(path="synthetic://x", sha256="0" * 64, byte_length=3)
        with pytest.raises(EvidenceIntakeError):
            verify_retained_bytes("abc", ref, label="x")


class TestReadPrivateEvidenceAndProtocolFromTmpPath:
    def test_read_private_evidence_round_trips_matching_synthetic_files(self, tmp_path):
        package_raw = b'{"synthetic":"package"}'
        restrictions_raw = b'{"synthetic":"restrictions"}'
        (tmp_path / "package.json").write_bytes(package_raw)
        (tmp_path / "restriction-history.json").write_bytes(restrictions_raw)
        binding = {
            "private_package": _ref_for(package_raw, str(tmp_path / "package.json")),
            "private_restrictions": _ref_for(restrictions_raw, str(tmp_path / "restriction-history.json")),
        }
        got_package, got_restrictions = read_private_evidence(binding)
        assert got_package == package_raw
        assert got_restrictions == restrictions_raw

    def test_read_private_evidence_refuses_on_tampered_file(self, tmp_path):
        package_raw = b'{"synthetic":"package"}'
        (tmp_path / "package.json").write_bytes(package_raw)
        (tmp_path / "restriction-history.json").write_bytes(b"{}")
        binding = {
            "private_package": _ref_for(package_raw, str(tmp_path / "package.json")),
            "private_restrictions": _ref_for(b"{}", str(tmp_path / "restriction-history.json")),
        }
        # Mutate the file after the binding reference was computed.
        (tmp_path / "package.json").write_bytes(package_raw + b"tampered")
        with pytest.raises(EvidenceIntakeError):
            read_private_evidence(binding)

    def test_read_protocol_bytes_uses_the_repo_docs_dir_not_the_binding_path(self, tmp_path):
        protocol_raw = b"synthetic offline protocol document bytes"
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        (docs_dir / "PROTOCOL.md").write_bytes(protocol_raw)
        binding = {"protocol": _ref_for(protocol_raw, "/this/absolute/authoring/path/PROTOCOL.md")}
        got = read_protocol_bytes(binding, repo_docs_dir=docs_dir)
        assert got == protocol_raw

    def test_read_protocol_bytes_refuses_on_mismatch(self, tmp_path):
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        (docs_dir / "PROTOCOL.md").write_bytes(b"changed bytes")
        binding = {"protocol": _ref_for(b"original bytes", "/anywhere/PROTOCOL.md")}
        with pytest.raises(EvidenceIntakeError):
            read_protocol_bytes(binding, repo_docs_dir=docs_dir)

    def test_parse_binding_rejects_non_object_json(self):
        with pytest.raises(EvidenceIntakeError):
            parse_binding(b"[]")


# =============================================================================
# evaluate_real_evidence against the established synthetic fixtures --
# proves this module's checker wiring, and that (unlike the attempt model)
# it tolerates real-shaped absolute filesystem paths embedded in the JSON.
# =============================================================================

class TestEvaluateRealEvidenceAgainstSyntheticFixtures:
    def test_good_fixture_with_qualified_clock_and_resources_is_satisfied(self):
        package_raw, restrictions_raw, protocol_raw, binding_raw = good_raws()
        result = evaluate_real_evidence(
            package_raw, restrictions_raw, protocol_raw, binding_raw,
            clock=GOOD_CLOCK, resources=GOOD_RESOURCES,
            review_terminal=dict(GOOD_REVIEW_TERMINAL))
        assert result.outcome == OUTCOME_SATISFIED
        assert result.refusal_reasons == ()

        report = build_report(result, generated_at_utc=GOOD_CLOCK.measured_utc)
        assert report["satisfied"] is True
        assert report["refusal_reasons"] == []
        assert report["execution_authority"] is False
        assert report["provider_authority"] is False
        assert report["capture_authority"] is False

    def test_all_22_retained_blockers_fixture_refuses_with_exactly_those_reasons(self):
        pkg, restrictions = all_blocked_package_and_restrictions()
        package_raw, restrictions_raw, protocol_raw, binding_raw = encode_raws(pkg, restrictions)
        result = evaluate_real_evidence(
            package_raw, restrictions_raw, protocol_raw, binding_raw,
            clock=GOOD_CLOCK, resources=GOOD_RESOURCES)
        assert result.outcome == OUTCOME_REFUSED
        assert set(result.refusal_reasons) == set(RETAINED_22_REASONS)

        report = build_report(result, generated_at_utc=GOOD_CLOCK.measured_utc)
        assert report["satisfied"] is False
        assert report["refusal_reasons"] == sorted(RETAINED_22_REASONS)

    def test_unqualified_clock_sentinel_adds_clock_and_window_reasons_on_top_of_the_22(self):
        """This module's own honest current-host clock (far past the
        checker's uncertainty/calibration floors, and -- for this frozen
        fixture's 2026-10-02 window -- also past the window itself) must
        surface additional real refusal reasons, never silently pass."""
        pkg, restrictions = all_blocked_package_and_restrictions()
        package_raw, restrictions_raw, protocol_raw, binding_raw = encode_raws(pkg, restrictions)
        unqualified_clock = ClockObservation(
            measured_utc="2026-10-02T20:00:00.000000Z",
            uncertainty_seconds=NO_QUALIFIED_CLOCK_SECONDS,
            calibration_age_seconds=NO_QUALIFIED_CLOCK_SECONDS,
            monotonic_consistent=True,
        )
        result = evaluate_real_evidence(
            package_raw, restrictions_raw, protocol_raw, binding_raw,
            clock=unqualified_clock, resources=GOOD_RESOURCES)
        assert result.outcome == OUTCOME_REFUSED
        assert set(RETAINED_22_REASONS) <= set(result.refusal_reasons)
        assert "EXCESSIVE_CLOCK_UNCERTAINTY" in result.refusal_reasons
        assert "EXPIRED_CLOCK_CALIBRATION" in result.refusal_reasons
        assert "EXPIRED_WINDOW" in result.refusal_reasons

    def test_real_shaped_absolute_paths_in_package_do_not_short_circuit_the_checker_layer(self):
        """Confirms the documented contrast with the attempt model: a
        package whose JSON embeds real-looking absolute filesystem paths
        (as the real retained package.json genuinely does, e.g. its
        owner_directive_original_record.path) is still fully evaluated by
        the checker layer -- it is REFUSED on its own real-world merits
        (the 22 retained reasons), never short-circuited to a single
        path-shape complaint the way tools.v11_gate3_preflight_attempt_
        model.admit_synthetic's _walk_synthetic_paths would."""
        pkg, restrictions = all_blocked_package_and_restrictions()
        pkg["prerequisites"]["owner_directive_original_record"] = {
            "byte_length": 1441,
            "path": "/home/alphaadmin/AlphaV11_Gate3EvidencePreflight/20261002-gefs-index-v1/"
                    "inputs/OWNER_PREFLIGHT_DIRECTIVE_20261002.txt",
            "qualification": "OWNER_INSTRUCTION_ONLY_NOT_PROVIDER_RIGHTS",
            "sha256": hashlib.sha256(b"owner-directive").hexdigest(),
        }
        package_raw, restrictions_raw, protocol_raw, binding_raw = encode_raws(pkg, restrictions)
        result = evaluate_real_evidence(
            package_raw, restrictions_raw, protocol_raw, binding_raw,
            clock=GOOD_CLOCK, resources=GOOD_RESOURCES)
        assert result.outcome == OUTCOME_REFUSED
        assert "NON_SYNTHETIC_PATH_OR_INVALID_JSON" not in result.refusal_reasons
        assert set(RETAINED_22_REASONS) <= set(result.refusal_reasons)

        # And confirm the contrasting claim about the attempt model layer is
        # actually true, not merely asserted in the module docstring.
        from tools.v11_gate3_preflight_attempt_model import SyntheticInputs, admit_synthetic
        from tests.v11_gate3_preflight_synthetic_cases import StateLedger, genesis_checkpoint
        inputs = SyntheticInputs(
            package_raw=package_raw, restrictions_raw=restrictions_raw,
            protocol_raw=protocol_raw, binding_raw=binding_raw,
            clock=GOOD_CLOCK, resources=GOOD_RESOURCES, ledger=StateLedger(()),
            review_terminal=None)
        state = admit_synthetic(inputs, genesis_checkpoint())
        assert state.reasons == ("NON_SYNTHETIC_PATH_OR_INVALID_JSON",)


# =============================================================================
# build_report shape
# =============================================================================

class TestBuildReport:
    def test_report_never_carries_raw_bytes_or_private_paths(self):
        pkg, restrictions = all_blocked_package_and_restrictions()
        package_raw, restrictions_raw, protocol_raw, binding_raw = encode_raws(pkg, restrictions)
        result = evaluate_real_evidence(
            package_raw, restrictions_raw, protocol_raw, binding_raw,
            clock=GOOD_CLOCK, resources=GOOD_RESOURCES)
        report = build_report(result, generated_at_utc=GOOD_CLOCK.measured_utc)
        serialized = json.dumps(report)
        assert str(package_raw) not in serialized
        assert set(report) == {
            "schema", "intake_schema", "outcome", "eligibility",
            "refusal_reasons", "generated_at_utc", "satisfied",
            "execution_authority", "provider_authority", "capture_authority",
        }
        assert report["execution_authority"] is False
        assert report["provider_authority"] is False
        assert report["capture_authority"] is False


# =============================================================================
# run_real_evidence_intake orchestration -- synthetic files under tmp_path
# only, never the actual retained private root.
# =============================================================================

class TestRunRealEvidenceIntakeOrchestration:
    def test_end_to_end_against_synthetic_retained_files(self, tmp_path):
        pkg, restrictions = all_blocked_package_and_restrictions()
        protocol_raw = protocol_raw_bytes()
        package_raw, restrictions_raw, _, _ = encode_raws(pkg, restrictions, protocol_raw)

        private_root = tmp_path / "private"
        private_root.mkdir()
        (private_root / "package.json").write_bytes(package_raw)
        (private_root / "restriction-history.json").write_bytes(restrictions_raw)

        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        (docs_dir / "PROTOCOL.md").write_bytes(protocol_raw)

        binding = good_binding_dict(package_raw, restrictions_raw, protocol_raw)
        binding["private_package"] = _ref_for(package_raw, str(private_root / "package.json"))
        binding["private_restrictions"] = _ref_for(restrictions_raw, str(private_root / "restriction-history.json"))
        binding["protocol"] = _ref_for(protocol_raw, "/original/authoring/path/PROTOCOL.md")
        binding_raw = json.dumps(binding).encode()
        binding_path = tmp_path / "BINDING.json"
        binding_path.write_bytes(binding_raw)

        report = run_real_evidence_intake(repo_docs_dir=docs_dir, binding_path=binding_path)
        assert report["intake_schema"] == "ALPHA_V11_EVIDENCE_PREFLIGHT_REAL_INTAKE_V1"
        assert report["satisfied"] is False
        assert set(RETAINED_22_REASONS) <= set(report["refusal_reasons"])
        assert report["execution_authority"] is False

    def test_oversized_binding_file_refuses_before_parsing(self, tmp_path):
        binding_path = tmp_path / "BINDING.json"
        binding_path.write_bytes(b"{" + b" " * (MAX_INTAKE_BYTES + 1) + b"}")
        with pytest.raises(EvidenceIntakeError):
            run_real_evidence_intake(repo_docs_dir=tmp_path, binding_path=binding_path)


# =============================================================================
# No forbidden capability anywhere in the module under test.
# =============================================================================

class TestNoForbiddenCapability:
    def test_module_imports_no_networking_or_subprocess_module(self):
        import tools.v11_gate3_evidence_preflight_real_intake as mod
        tree = ast.parse(open(mod.__file__, "r", encoding="utf-8").read())
        forbidden = {"socket", "subprocess", "urllib", "http", "requests", "ctypes"}
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        assert not (imported & forbidden), f"forbidden import(s): {imported & forbidden}"
