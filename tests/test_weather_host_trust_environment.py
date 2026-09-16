from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _text(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_host_bootstrap_freezes_runtime_paths_root_owned():
    text = _text("deploy/install-weather-paper-host-trust.sh")
    assert 'HOST_PATHS="${ETC_DIR}/host-paths.conf"' in text
    assert "DB_PATH=" in text
    assert "printf 'APP_DIR=%q\\nCONFIG_DIR=%q\\nDB_PATH=%q\\nUNIT=%q\\n'" in text
    assert 'install -o root -g root -m 0444 "${TMP_PATHS}" "${HOST_PATHS}"' in text
    assert "BASH_ENV ENV CDPATH" in text
    assert "GIT_DIR GIT_WORK_TREE GIT_CONFIG_GLOBAL GIT_CONFIG_SYSTEM" in text


def test_host_snapshot_and_recovery_ignore_caller_path_environment():
    for name in (
        "deploy/weather-paper-host-snapshot.sh",
        "deploy/weather-paper-host-recovery.sh",
    ):
        text = _text(name)
        assert 'HOST_PATHS="/etc/polymarket-weather-paper/host-paths.conf"' in text
        assert 'source "${HOST_PATHS}"' in text
        assert 'stat -c \'%u\' "${HOST_PATHS}"' in text
        assert "writable by nonroot" in text
        assert "ALPHA_WEATHER_APP_DIR" not in text
        assert "ALPHA_CONFIG_DIR" not in text
        assert "WEATHER_PAPER_DB_PATH" not in text
        assert "${HOME}" not in text
        assert "unset BASH_ENV ENV CDPATH GIT_DIR GIT_WORK_TREE" in text


def test_every_candidate_to_host_shell_entry_scrubs_environment():
    for name in (
        "deploy/snapshot-all-paper-rollback.sh",
        "deploy/snapshot-all-paper-rollback-v2.sh",
        "deploy/restore-all-paper-rollback.sh",
        "deploy/restore-all-paper-rollback-v2.sh",
    ):
        text = _text(name)
        assert "/usr/bin/env -i" in text
        assert "HOME=/nonexistent" in text
        assert "GIT_CONFIG_NOSYSTEM=1" in text
        assert "/bin/bash --noprofile --norc" in text

    prepare = _text("deploy/prepare-all-paper-candidate-v3.sh")
    start = _text("deploy/start-all-paper-candidate.sh")
    for text in (prepare, start):
        assert "/usr/bin/env -i PATH=/usr/bin:/bin HOME=/nonexistent GIT_CONFIG_NOSYSTEM=1" in text
        assert "/bin/bash --noprofile --norc \"${HOST_RECOVERY}\"" in text
