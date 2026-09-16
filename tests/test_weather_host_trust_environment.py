from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AUTHORITY = "/usr/local/libexec/polymarket-weather-paper/v2/authority.py"


def _text(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_candidate_bootstrap_is_verification_only_and_cannot_install_host_authority():
    text = _text("deploy/install-weather-paper-host-trust.sh")
    assert AUTHORITY in text
    assert "verify-authority" in text
    assert "did NOT install or replace" in text
    assert "git show" not in text
    assert "install -o root" not in text
    assert "approved =" not in text
    assert "daemon-reload" not in text
    assert "/usr/local/libexec/polymarket-weather-paper/snapshot-rollback.sh" not in text
    assert "/usr/local/libexec/polymarket-weather-paper/restore-rollback.sh" not in text


def test_candidate_release_snapshot_recovery_files_are_only_host_authority_shims():
    release_gate = _text("deploy/weather-paper-host-release-gate.py")
    snapshot = _text("deploy/weather-paper-host-snapshot.sh")
    recovery = _text("deploy/weather-paper-host-recovery.sh")
    venv_snapshot = _text("deploy/weather-paper-venv-snapshot.py")

    assert AUTHORITY in release_gate
    assert "os.execv" in release_gate
    assert "verify_object_policy" not in release_gate
    assert AUTHORITY in snapshot and " snapshot --candidate-sha " in snapshot
    assert AUTHORITY in recovery and " recover --generation-id " in recovery
    assert "candidate code cannot snapshot/restore trusted venvs" in venv_snapshot
    assert "tarfile" not in venv_snapshot


def test_every_candidate_to_host_entry_uses_clean_environment_and_exact_identity():
    snapshot_wrappers = (
        "deploy/snapshot-all-paper-rollback.sh",
        "deploy/snapshot-all-paper-rollback-v2.sh",
        "deploy/weather-paper-host-snapshot.sh",
    )
    recovery_wrappers = (
        "deploy/restore-all-paper-rollback.sh",
        "deploy/restore-all-paper-rollback-v2.sh",
        "deploy/weather-paper-host-recovery.sh",
    )
    for name in snapshot_wrappers:
        text = _text(name)
        assert AUTHORITY in text
        assert "/usr/bin/env -i" in text
        assert "HOME=/nonexistent" in text
        assert "GIT_CONFIG_NOSYSTEM=1" in text
        assert "--candidate-sha" in text
    for name in recovery_wrappers:
        text = _text(name)
        assert AUTHORITY in text
        assert "/usr/bin/env -i" in text
        assert "HOME=/nonexistent" in text
        assert "GIT_CONFIG_NOSYSTEM=1" in text
        assert "--generation-id" in text


def test_candidate_lifecycle_scrubs_python_native_loader_and_network_environment():
    names = (
        "deploy/prepare-all-paper-candidate-v3.sh",
        "deploy/preflight-all-paper-deployment.sh",
        "deploy/start-all-paper-candidate.sh",
        "deploy/enable-all-paper-persistence.sh",
    )
    for name in names:
        text = _text(name)
        for key in (
            "PYTHONPATH",
            "PYTHONHOME",
            "PYTHONUSERBASE",
            "PYTHONSTARTUP",
            "PYTHONINSPECT",
            "LD_PRELOAD",
            "LD_LIBRARY_PATH",
        ):
            assert key in text, (name, key)
        assert "PYTHONNOUSERSITE=1" in text


def test_unit_renderer_uses_clean_env_isolated_python_and_only_telegram_credentials():
    text = _text("deploy/render-all-paper-unit.py")
    assert '"PYTHONPATH"' in text
    assert '"PYTHONHOME"' in text
    assert '"PYTHONUSERBASE"' in text
    assert '"PYTHONSTARTUP"' in text
    assert '"PYTHONINSPECT"' in text
    assert '"LD_PRELOAD"' in text
    assert '"LD_LIBRARY_PATH"' in text
    assert '"PYTHONNOUSERSITE=1"' in text
    assert '"/usr/bin/env -i PATH=/usr/bin:/bin HOME=/nonexistent "' in text
    assert '"TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN} "' in text
    assert '"TELEGRAM_CHAT_ID=${TELEGRAM_CHAT_ID} "' in text
    assert 'f"{python} -I -s -m {ALL_PAPER_MODULE} "' in text
    for forbidden in ("PRIVATE_KEY", "WALLET", "API_SECRET", "POLYMARKET_API"):
        assert forbidden not in text
