from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_production_release_paths_require_persisted_required_dependency_preflight():
    paths = [
        ROOT / "deploy/oracle/install.sh",
        ROOT / "deploy/oracle/update.sh",
        ROOT / "deploy/gcp/install.sh",
    ]
    for path in paths:
        text = path.read_text()
        assert "dependency-preflight.json" in text
        assert "polymarket_scanner.dependency_preflight" in text
        assert "--required-only" in text
        assert "--output" in text
        assert 'chmod 600 "${PREFLIGHT_FILE}"' in text


def test_dependency_preflight_occurs_before_any_release_restart_path():
    oracle_install = (ROOT / "deploy/oracle/install.sh").read_text()
    gcp_install = (ROOT / "deploy/gcp/install.sh").read_text()
    oracle_update = (ROOT / "deploy/oracle/update.sh").read_text()

    oracle_gate = oracle_install.index("polymarket_scanner.dependency_preflight")
    gcp_gate = gcp_install.index("polymarket_scanner.dependency_preflight")
    update_gate = oracle_update.index("polymarket_scanner.dependency_preflight")

    assert oracle_gate < oracle_install.index("systemctl restart")
    assert gcp_gate < gcp_install.index("systemctl restart")
    # setup-command-service.sh owns the restart on the Oracle update path.
    assert update_gate < oracle_update.index("setup-command-service.sh")
