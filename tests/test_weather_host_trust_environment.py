from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
def _text(name:str)->str: return (ROOT/name).read_text(encoding="utf-8")


def test_host_bootstrap_requires_external_digest_pinned_authority_and_refuses_replacement():
    text=_text("deploy/install-weather-paper-host-trust.sh")
    assert '--authority-source' in text and '--bundle-sha256' in text and '--bootstrap' in text
    assert 'authority source may not reside in candidate application tree' in text
    assert 'host authority already installed; bootstrap refuses replacement' in text
    assert 'git -C "${APP_DIR}" show' not in text
    assert 'candidate Git' in text or 'Candidate Git' in text


def test_host_authority_self_verifies_root_custody_file_digests_and_protected_candidate_blobs():
    text=_text("deploy/weather-paper-host-release-gate.py")
    assert 'AUTHORITY_VERSION = "weather-paper-host-release-authority-v2-independent"' in text
    assert 'HOST_AUTHORITY_FILE_DIGEST_MISMATCH' in text
    assert 'protected_candidate_blobs' in text
    assert 'HOST_RELEASE_CANDIDATE_REDEFINES_AUTHORITY' in text
    for path in ('deploy/weather-paper-host-release-gate.py','deploy/weather-paper-host-recovery.sh','deploy/weather-paper-host-snapshot.sh','deploy/weather-paper-venv-snapshot.py','deploy/install-weather-paper-host-trust.sh'):
        assert path in text


def test_host_snapshot_and_recovery_ignore_caller_path_and_loader_environment():
    for name in ("deploy/weather-paper-host-snapshot.sh","deploy/weather-paper-host-recovery.sh"):
        text=_text(name)
        assert 'HOST_PATHS="/etc/polymarket-weather-paper/host-paths.conf"' in text
        assert 'source "${HOST_PATHS}"' in text
        assert "ALPHA_WEATHER_APP_DIR" not in text and "ALPHA_CONFIG_DIR" not in text and "WEATHER_PAPER_DB_PATH" not in text and "${HOME}" not in text
        for forbidden in ("PYTHONPATH","PYTHONHOME","PYTHONUSERBASE","PYTHONSTARTUP","PYTHONINSPECT","LD_PRELOAD","LD_LIBRARY_PATH","BASH_ENV","ENV","CDPATH","GIT_DIR","GIT_WORK_TREE"):
            assert forbidden in text
        assert 'export PYTHONNOUSERSITE=1' in text


def test_every_candidate_to_host_shell_entry_scrubs_environment():
    for name in ("deploy/snapshot-all-paper-rollback.sh","deploy/snapshot-all-paper-rollback-v2.sh","deploy/restore-all-paper-rollback.sh","deploy/restore-all-paper-rollback-v2.sh"):
        text=_text(name); assert "/usr/bin/env -i" in text; assert "HOME=/nonexistent" in text; assert "GIT_CONFIG_NOSYSTEM=1" in text; assert "/bin/bash --noprofile --norc" in text
    prepare=_text("deploy/prepare-all-paper-candidate-v3.sh"); start=_text("deploy/start-all-paper-candidate.sh")
    for text in (prepare,start):
        assert "/usr/bin/env -i PATH=/usr/bin:/bin HOME=/nonexistent GIT_CONFIG_NOSYSTEM=1" in text
        assert "/bin/bash --noprofile --norc \"${HOST_RECOVERY}\"" in text
