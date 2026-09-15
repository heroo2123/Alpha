from pathlib import Path


CORRECTIVE_BRANCH = "weather-three-layer-live-grammar-corrective-2026-09-15"
SUPERSEDED_BRANCH = "weather-three-layer-meticulous-final-2026-09-15"


def test_prepare_candidate_defaults_to_current_corrective_branch():
    source = Path("deploy/prepare-weather-paper-candidate.sh").read_text(encoding="utf-8")
    assert f"${{2:-{CORRECTIVE_BRANCH}}}" in source
    assert f"${{2:-{SUPERSEDED_BRANCH}}}" not in source


def test_prepare_candidate_still_requires_exact_release_and_three_layer_entrypoint():
    source = Path("deploy/prepare-weather-paper-candidate.sh").read_text(encoding="utf-8")
    assert 'RELEASE_SHA="${1:-}"' in source
    assert 'merge-base --is-ancestor "${RELEASE_SHA}" FETCH_HEAD' in source
    assert 'polymarket_scanner.weather_only_live_paper_three_layer_validation' in source
    assert 'verify-runtime-release.sh' in source
