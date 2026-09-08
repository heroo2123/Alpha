from pathlib import Path


def test_trade_only_runtime_health_is_wired_to_manifest_and_version_constants():
    source = Path("app_trade_only.py").read_text(encoding="utf-8")
    assert "from polymarket_scanner.runtime_manifest import build_runtime_manifest" in source
    assert "SPORTS_MAPPING_VERSION" in source
    assert "TRADE_READY_VERSION" in source
    assert 'base.state["runtime_manifest"] = runtime_manifest' in source
    assert 'base.state["production_release_attested"] = runtime_manifest["production_release_attested"]' in source
    assert 'base.state["runtime_policy_sha256"] = runtime_manifest["nonsecret_safety_policy_sha256"]' in source
    assert 'base.state["sports_detector_version"] = SPORTS_MAPPING_VERSION' in source
    assert '"home_away_v3_match_moneyline_only"' not in source


def test_runtime_manifest_never_names_secret_configuration_fields():
    source = Path("polymarket_scanner/runtime_manifest.py").read_text(encoding="utf-8")
    policy_section = source.split("def _nonsecret_policy()", 1)[1].split("def _policy_hash", 1)[0]
    assert "telegram_bot_token" not in policy_section
    assert "telegram_chat_id" not in policy_section
    assert "bls_api_key" not in policy_section
