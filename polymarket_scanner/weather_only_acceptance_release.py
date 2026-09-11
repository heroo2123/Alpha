from __future__ import annotations

"""Exact release provenance for weather W7 silent-shadow acceptance.

The W7 bundle already proves runtime behavior, containment and measured latency. This
module proves that the attached scanner actually ran from the immutable release it
claims. It performs local read-only checks only:

* release marker is a regular non-symlink 40-hex SHA;
* Git HEAD equals that marker and the expected candidate SHA;
* tracked working tree is clean (untracked files are intentionally ignored, matching
  deploy/verify-runtime-release.sh);
* the imported weather runtime module is the exact file under that checkout;
* /proc/<scanner-pid>/cwd resolves to that checkout.

The recorder captures this before and after the frozen run. No service control,
network access, database mutation, Telegram action or order path exists here.
"""

import hashlib
import inspect
import json
import math
import re
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import weather_only_runtime as runtime_module


WEATHER_W7_RELEASE_VERSION = "weather_w7_release_v1_git_head_clean_marker_runtime_cwd_before_after"
_SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
_SHA64_RE = re.compile(r"^[0-9a-f]{64}$")


class WeatherW7ReleaseError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


def _canonical(value: object) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    except (TypeError, ValueError):
        raise WeatherW7ReleaseError("W7_RELEASE_JSON_INVALID") from None


def _sha(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _sha64(value: object, code: str) -> str:
    text = str(value or "").strip().lower()
    if not _SHA64_RE.fullmatch(text):
        raise WeatherW7ReleaseError(code)
    return text


def _sha40(value: object, code: str) -> str:
    text = str(value or "").strip().lower()
    if not _SHA40_RE.fullmatch(text):
        raise WeatherW7ReleaseError(code)
    return text


def _finite(value: object, code: str) -> float:
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        raise WeatherW7ReleaseError(code)
    number = float(value)
    if not math.isfinite(number) or number < 0.0:
        raise WeatherW7ReleaseError(code)
    return number


def _positive_int(value: object, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise WeatherW7ReleaseError(code)
    return value


def _git(app_dir: Path, *args: str) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(app_dir), *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=5.0,
        )
    except (OSError, subprocess.SubprocessError):
        raise WeatherW7ReleaseError("W7_RELEASE_GIT_COMMAND_FAILED") from None
    if completed.returncode != 0:
        raise WeatherW7ReleaseError("W7_RELEASE_GIT_COMMAND_FAILED")
    return completed.stdout


@dataclass(frozen=True, slots=True)
class WeatherW7ReleaseAttestation:
    version: str
    captured_at: float
    release_sha: str
    git_head_sha: str
    release_marker_sha256: str
    app_dir_sha256: str
    scanner_cwd_sha256: str
    runtime_source_sha256: str
    scanner_process_id: int
    evidence_sha256: str
    tracked_tree_clean: bool = field(init=False, default=True)
    runtime_under_release_checkout: bool = field(init=False, default=True)
    scanner_cwd_matches_release_checkout: bool = field(init=False, default=True)
    financial_authority: bool = field(init=False, default=False)
    financial_delivery: bool = field(init=False, default=False)
    automatic_order_placement: bool = field(init=False, default=False)

    def as_dict(self) -> dict:
        return asdict(self)


def _attestation_payload(row: WeatherW7ReleaseAttestation) -> dict:
    value = row.as_dict()
    value.pop("evidence_sha256", None)
    return value


def validate_weather_w7_release_attestation(row: object) -> WeatherW7ReleaseAttestation:
    if not isinstance(row, WeatherW7ReleaseAttestation) or row.version != WEATHER_W7_RELEASE_VERSION:
        raise WeatherW7ReleaseError("W7_RELEASE_ATTESTATION_TYPE_OR_VERSION_INVALID")
    _finite(row.captured_at, "W7_RELEASE_CAPTURE_TIME_INVALID")
    release = _sha40(row.release_sha, "W7_RELEASE_SHA_INVALID")
    head = _sha40(row.git_head_sha, "W7_RELEASE_GIT_HEAD_INVALID")
    if release != head:
        raise WeatherW7ReleaseError("W7_RELEASE_GIT_HEAD_MISMATCH")
    _positive_int(row.scanner_process_id, "W7_RELEASE_PROCESS_ID_INVALID")
    for value in (
        row.release_marker_sha256,
        row.app_dir_sha256,
        row.scanner_cwd_sha256,
        row.runtime_source_sha256,
    ):
        _sha64(value, "W7_RELEASE_COMPONENT_SHA_INVALID")
    if row.app_dir_sha256 != row.scanner_cwd_sha256:
        raise WeatherW7ReleaseError("W7_RELEASE_SCANNER_CWD_MISMATCH")
    if any((
        row.tracked_tree_clean is not True,
        row.runtime_under_release_checkout is not True,
        row.scanner_cwd_matches_release_checkout is not True,
        row.financial_authority is not False,
        row.financial_delivery is not False,
        row.automatic_order_placement is not False,
    )):
        raise WeatherW7ReleaseError("W7_RELEASE_AUTHORITY_OR_IDENTITY_BOUNDARY_BROKEN")
    supplied = _sha64(row.evidence_sha256, "W7_RELEASE_EVIDENCE_SHA_INVALID")
    if supplied != _sha(_attestation_payload(row)):
        raise WeatherW7ReleaseError("W7_RELEASE_EVIDENCE_DIGEST_MISMATCH")
    return row


def attest_weather_w7_release(
    *,
    app_dir: str | Path,
    release_file: str | Path,
    scanner_process_id: int,
    expected_release_sha: str,
    proc_root: str | Path = "/proc",
    captured_at: float | None = None,
) -> WeatherW7ReleaseAttestation:
    expected = _sha40(expected_release_sha, "W7_RELEASE_EXPECTED_SHA_INVALID")
    pid = _positive_int(scanner_process_id, "W7_RELEASE_PROCESS_ID_INVALID")
    app = Path(app_dir)
    marker = Path(release_file)
    if not app.is_absolute() or app.is_symlink() or not app.is_dir() or not (app / ".git").exists():
        raise WeatherW7ReleaseError("W7_RELEASE_APP_CHECKOUT_INVALID")
    app_resolved = app.resolve()
    if not marker.is_absolute() or marker.is_symlink() or not marker.is_file():
        raise WeatherW7ReleaseError("W7_RELEASE_MARKER_INVALID")
    try:
        marker_bytes = marker.read_bytes()
        marker_sha = _sha40(marker_bytes.decode("ascii").strip(), "W7_RELEASE_MARKER_SHA_INVALID")
    except (OSError, UnicodeError):
        raise WeatherW7ReleaseError("W7_RELEASE_MARKER_READ_FAILED") from None
    if marker_sha != expected:
        raise WeatherW7ReleaseError("W7_RELEASE_MARKER_EXPECTED_MISMATCH")

    head = _sha40(_git(app_resolved, "rev-parse", "HEAD").strip(), "W7_RELEASE_GIT_HEAD_INVALID")
    if head != expected:
        raise WeatherW7ReleaseError("W7_RELEASE_GIT_HEAD_MISMATCH")
    if _git(app_resolved, "status", "--porcelain", "--untracked-files=no").strip():
        raise WeatherW7ReleaseError("W7_RELEASE_TRACKED_TREE_DIRTY")

    source = inspect.getsourcefile(runtime_module)
    if not source:
        raise WeatherW7ReleaseError("W7_RELEASE_RUNTIME_SOURCE_UNRESOLVED")
    source_path = Path(source).resolve()
    expected_source = app_resolved / "polymarket_scanner" / "weather_only_runtime.py"
    if source_path != expected_source:
        raise WeatherW7ReleaseError("W7_RELEASE_RUNTIME_OUTSIDE_CHECKOUT")
    try:
        runtime_source_sha = hashlib.sha256(source_path.read_bytes()).hexdigest()
    except OSError:
        raise WeatherW7ReleaseError("W7_RELEASE_RUNTIME_SOURCE_READ_FAILED") from None

    root = Path(proc_root)
    try:
        scanner_cwd = (root / str(pid) / "cwd").resolve(strict=True)
    except OSError:
        raise WeatherW7ReleaseError("W7_RELEASE_SCANNER_CWD_READ_FAILED") from None
    if scanner_cwd != app_resolved:
        raise WeatherW7ReleaseError("W7_RELEASE_SCANNER_CWD_MISMATCH")

    captured = time.time() if captured_at is None else _finite(captured_at, "W7_RELEASE_CAPTURE_TIME_INVALID")
    app_sha = hashlib.sha256(str(app_resolved).encode("utf-8")).hexdigest()
    shell = WeatherW7ReleaseAttestation(
        version=WEATHER_W7_RELEASE_VERSION,
        captured_at=captured,
        release_sha=expected,
        git_head_sha=head,
        release_marker_sha256=hashlib.sha256(marker_bytes).hexdigest(),
        app_dir_sha256=app_sha,
        scanner_cwd_sha256=hashlib.sha256(str(scanner_cwd).encode("utf-8")).hexdigest(),
        runtime_source_sha256=runtime_source_sha,
        scanner_process_id=pid,
        evidence_sha256="0" * 64,
    )
    final = WeatherW7ReleaseAttestation(
        **{
            name: getattr(shell, name)
            for name, definition in shell.__dataclass_fields__.items()
            if definition.init and name != "evidence_sha256"
        },
        evidence_sha256=_sha(_attestation_payload(shell)),
    )
    return validate_weather_w7_release_attestation(final)


@dataclass(frozen=True, slots=True)
class WeatherW7ReleaseManifest:
    version: str
    before: WeatherW7ReleaseAttestation
    after: WeatherW7ReleaseAttestation
    evidence_sha256: str
    financial_authority: bool = field(init=False, default=False)
    financial_delivery: bool = field(init=False, default=False)
    automatic_order_placement: bool = field(init=False, default=False)

    def as_dict(self) -> dict:
        return {
            "version": self.version,
            "before": self.before.as_dict(),
            "after": self.after.as_dict(),
            "evidence_sha256": self.evidence_sha256,
            "financial_authority": False,
            "financial_delivery": False,
            "automatic_order_placement": False,
        }


def _manifest_payload(row: WeatherW7ReleaseManifest) -> dict:
    value = row.as_dict()
    value.pop("evidence_sha256", None)
    return value


def validate_weather_w7_release_manifest(row: object) -> WeatherW7ReleaseManifest:
    if not isinstance(row, WeatherW7ReleaseManifest) or row.version != WEATHER_W7_RELEASE_VERSION:
        raise WeatherW7ReleaseError("W7_RELEASE_MANIFEST_TYPE_OR_VERSION_INVALID")
    before = validate_weather_w7_release_attestation(row.before)
    after = validate_weather_w7_release_attestation(row.after)
    if after.captured_at < before.captured_at:
        raise WeatherW7ReleaseError("W7_RELEASE_CAPTURE_TIME_ORDER_INVALID")
    stable_before = (
        before.release_sha,
        before.git_head_sha,
        before.release_marker_sha256,
        before.app_dir_sha256,
        before.scanner_cwd_sha256,
        before.runtime_source_sha256,
        before.scanner_process_id,
    )
    stable_after = (
        after.release_sha,
        after.git_head_sha,
        after.release_marker_sha256,
        after.app_dir_sha256,
        after.scanner_cwd_sha256,
        after.runtime_source_sha256,
        after.scanner_process_id,
    )
    if stable_before != stable_after:
        raise WeatherW7ReleaseError("W7_RELEASE_IDENTITY_CHANGED_DURING_RUN")
    if any((row.financial_authority is not False, row.financial_delivery is not False, row.automatic_order_placement is not False)):
        raise WeatherW7ReleaseError("W7_RELEASE_AUTHORITY_BOUNDARY_BROKEN")
    if _sha64(row.evidence_sha256, "W7_RELEASE_MANIFEST_SHA_INVALID") != _sha(_manifest_payload(row)):
        raise WeatherW7ReleaseError("W7_RELEASE_MANIFEST_DIGEST_MISMATCH")
    return row


def build_weather_w7_release_manifest(
    *,
    before: WeatherW7ReleaseAttestation,
    after: WeatherW7ReleaseAttestation,
) -> WeatherW7ReleaseManifest:
    before_valid = validate_weather_w7_release_attestation(before)
    after_valid = validate_weather_w7_release_attestation(after)
    shell = WeatherW7ReleaseManifest(
        version=WEATHER_W7_RELEASE_VERSION,
        before=before_valid,
        after=after_valid,
        evidence_sha256="0" * 64,
    )
    final = WeatherW7ReleaseManifest(
        version=shell.version,
        before=shell.before,
        after=shell.after,
        evidence_sha256=_sha(_manifest_payload(shell)),
    )
    return validate_weather_w7_release_manifest(final)
