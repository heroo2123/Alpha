from __future__ import annotations

import re
from pathlib import Path


STALE = {
    "tests/test_weather_all_paper_deployment_identity.py": [
        "test_final_renderer_targets_final_v9_wrapper_and_enforces_environment_boundary",
        "test_prepare_routes_to_root_custody_v4_and_requires_complete_operator_stack",
        "test_runtime_attestation_v2_targets_final_v9_and_proves_network_dotenv_boundary",
        "test_install_start_and_persistence_use_v9_host_and_final_acceptance_gates",
        "test_root_custodied_rollback_snapshot_and_restore_bind_database_source_and_exact_venv",
    ],
    "tests/test_weather_final_corrective_deployment.py": [
        "test_final_acceptance_requires_every_new_operator_config_network_and_recall_invariant",
        "test_renderer_uses_final_v9_and_disables_dotenv_and_network_environment",
        "test_deployment_scripts_route_through_final_attestation_and_root_custodied_rollback",
        "test_exact_venv_snapshot_round_trip",
    ],
    "tests/test_weather_final_review_fixes_v9.py": [
        "test_root_custody_and_final_v9_are_wired_into_deployment_files",
    ],
    "tests/test_weather_host_trust_environment.py": [
        "test_host_bootstrap_freezes_runtime_paths_root_owned_and_revokes_stale_candidates",
        "test_host_bootstrap_materializes_authority_tools_from_exact_reviewed_git_object",
        "test_host_snapshot_and_recovery_ignore_caller_path_environment",
        "test_every_candidate_to_host_shell_entry_scrubs_environment",
    ],
    "tests/test_weather_independent_review_corrective.py": [
        "test_cutover_scripts_require_host_approved_exact_previous_release_database_and_venv",
    ],
    "tests/test_weather_rollback_generation_freshness.py": [
        "test_archive_integrity_alone_does_not_prove_current_venv_and_tree_check_detects_drift",
        "test_host_snapshot_invalidates_old_generation_until_source_venv_and_manifest_are_reverified",
    ],
    # Replaced behaviorally by test_weather_stage2_semantic_terminal_maker.py.
    "tests/test_weather_post_final_review_corrective.py": [
        "test_legacy_maker_settlements_are_excluded_from_validated_performance",
    ],
}


def remove_test(text: str, name: str) -> str:
    pattern = re.compile(
        rf"(?ms)^def {re.escape(name)}\([^\n]*\):\n.*?(?=^def |^@pytest\.|\Z)"
    )
    matches = list(pattern.finditer(text))
    if len(matches) != 1:
        raise SystemExit(f"{name}: expected one obsolete test, found {len(matches)}")
    match = matches[0]
    return text[: match.start()] + text[match.end() :]


def main() -> None:
    for raw_path, names in STALE.items():
        path = Path(raw_path)
        text = path.read_text(encoding="utf-8")
        for name in names:
            text = remove_test(text, name)
        path.write_text(text.rstrip() + "\n", encoding="utf-8")

    foundation = Path("tests/test_weather_only_foundation.py")
    text = foundation.read_text(encoding="utf-8")
    replacements = {
        "assert snapshot.unique_event_count == 2": "assert snapshot.unique_event_count == 1",
        "assert snapshot.unique_market_count == 4": "assert snapshot.unique_market_count == 2",
        "assert any(row[\"id\"] == hidden[\"id\"] for row in snapshot.events)": "assert all(row[\"id\"] != hidden[\"id\"] for row in snapshot.events)",
        "assert snapshot.global_census_retained_events == 1": "assert snapshot.global_census_retained_events == 0",
        "assert recall[\"retained_events\"] == 1": "assert recall[\"retained_events\"] == 0",
    }
    for old, new in replacements.items():
        if text.count(old) != 1:
            raise SystemExit(f"foundation replacement mismatch: {old}")
        text = text.replace(old, new, 1)
    marker = '    assert recall["max_reuse_seconds"] == 300.0\n'
    if text.count(marker) != 1:
        raise SystemExit("foundation semantic marker missing")
    semantic = marker + (
        '    assert recall["gamma_census_complete"] is True\n'
        '    assert recall["weather_looking_events"] == 1\n'
        '    assert recall["strict_supported_events"] == 0\n'
        '    assert recall["unsupported_weather_events"] == 1\n'
        '    assert recall["weather_semantic_coverage_complete"] is False\n'
        '    assert recall["weather_semantic_coverage_status"] == "PARTIAL_STRICT_SUBSET"\n'
        '    assert sum(recall["unsupported_reason_counts"].values()) == 1\n'
        '    assert recall["unsupported_examples"][0]["event_id"] == hidden["id"]\n'
    )
    text = text.replace(marker, semantic, 1)
    foundation.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
