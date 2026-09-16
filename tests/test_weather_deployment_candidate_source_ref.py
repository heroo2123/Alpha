from pathlib import Path

CORRECTIVE_BRANCH = "weather-stage1-findings1-4-corrective-2026-09-16"
SUPERSEDED_BRANCH = "weather-three-layer-live-grammar-corrective-2026-09-15"


def test_prepare_candidate_defaults_to_current_corrective_branch():
    source = Path("deploy/prepare-weather-paper-candidate.sh").read_text(encoding="utf-8")
    assert f"${{2:-{CORRECTIVE_BRANCH}}}" in source
    assert f"${{2:-{SUPERSEDED_BRANCH}}}" not in source


def test_prepare_candidate_still_requires_exact_release_generation_and_three_layer_entrypoint():
    source = Path("deploy/prepare-weather-paper-candidate.sh").read_text(encoding="utf-8")
    assert 'RELEASE_SHA="${1:-}"' in source
    assert 'merge-base --is-ancestor "${RELEASE_SHA}" FETCH_HEAD' in source
    assert 'polymarket_scanner.weather_only_live_paper_three_layer_validation' in source
    assert 'GENERATION_ID=' in source
    assert 'verify-generation --generation-id "${GENERATION_ID}" --sha "${RELEASE_SHA}"' in source
