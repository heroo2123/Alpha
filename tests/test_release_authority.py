from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run(*args: str, cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(args),
        cwd=cwd,
        check=check,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _git(repo: Path, *args: str) -> str:
    return _run("git", *args, cwd=repo).stdout.strip()


def _init_repo(path: Path) -> None:
    _run("git", "init", "-b", "main", str(path))
    _git(path, "config", "user.email", "release-test@example.invalid")
    _git(path, "config", "user.name", "Release Test")


def test_production_deploy_scripts_do_not_select_mutable_main_as_runtime_revision():
    paths = [
        ROOT / "deploy/oracle/install.sh",
        ROOT / "deploy/oracle/update.sh",
        ROOT / "deploy/gcp/install.sh",
    ]
    for path in paths:
        text = path.read_text()
        assert "reset --hard origin/main" not in text
        assert "ALPHA_RELEASE_SHA" in text
        assert "release-pin.sh" in text
        assert "release.sha" in text


def test_systemd_paths_require_runtime_release_attestation():
    for path in [
        ROOT / "deploy/oracle/install.sh",
        ROOT / "deploy/oracle/setup-command-service.sh",
        ROOT / "deploy/gcp/install.sh",
    ]:
        text = path.read_text()
        assert "verify-runtime-release.sh" in text
        assert "ExecStartPre=" in text
        assert "app_trade_only:app" in text
        assert "command_worker_trade_only.py" in text


def test_runtime_release_verifier_accepts_exact_clean_commit_and_rejects_dirty_tree(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    (repo / "app.py").write_text("print('safe')\n")
    _git(repo, "add", "app.py")
    _git(repo, "commit", "-m", "safe")
    sha = _git(repo, "rev-parse", "HEAD")

    marker = tmp_path / "release.sha"
    marker.write_text(sha + "\n")
    verifier = ROOT / "deploy/verify-runtime-release.sh"

    ok = _run("bash", str(verifier), str(repo), str(marker), check=False)
    assert ok.returncode == 0, ok.stderr
    assert sha in ok.stdout

    (repo / "app.py").write_text("print('mutated')\n")
    dirty = _run("bash", str(verifier), str(repo), str(marker), check=False)
    assert dirty.returncode != 0
    assert "tracked working tree differs" in dirty.stderr


def test_release_pin_checks_out_explicit_main_ancestor_and_records_it(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    _init_repo(source)
    # The release helper intentionally refuses pre-hardening revisions that do not
    # carry the canonical trade-only runtime files.
    (source / "app_trade_only.py").write_text("app = object()\n")
    (source / "command_worker_trade_only.py").write_text("# worker\n")
    (source / "value.txt").write_text("reviewed\n")
    _git(source, "add", ".")
    _git(source, "commit", "-m", "reviewed")
    reviewed_sha = _git(source, "rev-parse", "HEAD")

    (source / "value.txt").write_text("newer mutable tip\n")
    _git(source, "add", "value.txt")
    _git(source, "commit", "-m", "newer")

    remote = tmp_path / "remote.git"
    _run("git", "clone", "--bare", str(source), str(remote))
    checkout = tmp_path / "checkout"
    _run("git", "clone", str(remote), str(checkout))

    marker = tmp_path / "config/release.sha"
    pin = ROOT / "deploy/release-pin.sh"
    result = _run(
        "bash", str(pin), str(checkout), reviewed_sha, str(marker), check=False
    )
    assert result.returncode == 0, result.stderr
    assert _git(checkout, "rev-parse", "HEAD") == reviewed_sha
    assert marker.read_text().strip() == reviewed_sha
    assert (checkout / "value.txt").read_text() == "reviewed\n"


def test_release_pin_rejects_malformed_release_id(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    marker = tmp_path / "release.sha"
    pin = ROOT / "deploy/release-pin.sh"
    result = _run("bash", str(pin), str(repo), "main", str(marker), check=False)
    assert result.returncode != 0
    assert "40 hexadecimal" in result.stderr
    assert not marker.exists()
