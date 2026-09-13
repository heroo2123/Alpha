from __future__ import annotations

"""Pure read-only attestation logic for the canonical weather-paper runtime.

R22 cannot be closed by a renderer or release marker alone.  A later authorized VM
check must prove the installed unit and, when active, the actual process are the
canonical corrective runtime.  This module keeps the decision logic deterministic and
testable; a separate read-only host collector can provide facts from systemd/proc.

No service is started, stopped, enabled or modified here.
"""

import hashlib
import json
import os
import shlex
from dataclasses import asdict, dataclass, field
from pathlib import Path


RUNTIME_ATTESTATION_VERSION = "weather_runtime_attestation_v2_inactive_orphan_guard"
CANONICAL_MODULE = "polymarket_scanner.weather_only_live_paper_corrective"


class WeatherRuntimeAttestationError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


def _norm_path(value: object, code: str) -> str:
    text = str(value or "").strip()
    if not text or not os.path.isabs(text):
        raise WeatherRuntimeAttestationError(code)
    return os.path.realpath(text)


def _sha(value: object, code: str) -> str:
    text = str(value or "").strip().lower()
    if len(text) != 40 and len(text) != 64:
        raise WeatherRuntimeAttestationError(code)
    if any(ch not in "0123456789abcdef" for ch in text):
        raise WeatherRuntimeAttestationError(code)
    return text


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _execstart_tokens(unit_text: str) -> tuple[str, ...]:
    lines = []
    for raw in str(unit_text or "").splitlines():
        line = raw.strip()
        if line.startswith("ExecStart=") and not line.startswith("ExecStartPre="):
            lines.append(line.split("=", 1)[1].strip())
    if len(lines) != 1:
        raise WeatherRuntimeAttestationError("WEATHER_RUNTIME_EXECSTART_NOT_UNIQUE")
    try:
        tokens = tuple(shlex.split(lines[0]))
    except ValueError:
        raise WeatherRuntimeAttestationError("WEATHER_RUNTIME_EXECSTART_PARSE_INVALID") from None
    if not tokens:
        raise WeatherRuntimeAttestationError("WEATHER_RUNTIME_EXECSTART_EMPTY")
    return tokens


def _arg_value(tokens: tuple[str, ...], name: str) -> str | None:
    matches = [index for index, token in enumerate(tokens) if token == name]
    if len(matches) != 1:
        return None
    index = matches[0]
    if index + 1 >= len(tokens):
        return None
    return tokens[index + 1]


def _normalized_process_inventory(
    rows: tuple[tuple[str, ...], ...],
) -> tuple[tuple[str, ...], ...]:
    return tuple(tuple(str(part) for part in row) for row in rows)


def _assert_inactive_process_inventory_empty(
    rows: tuple[tuple[str, ...], ...],
) -> None:
    matching = _normalized_process_inventory(rows)
    if matching:
        raise WeatherRuntimeAttestationError("WEATHER_RUNTIME_ORPHAN_PROCESS_WHILE_SERVICE_INACTIVE")


@dataclass(frozen=True, slots=True)
class WeatherRuntimeFacts:
    unit_name: str
    unit_text: str
    active: bool
    main_pid: int | None
    process_cwd: str | None
    process_executable: str | None
    process_argv: tuple[str, ...]
    repo_head_sha: str
    release_marker_sha: str
    matching_weather_process_argvs: tuple[tuple[str, ...], ...]

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class WeatherRuntimeAttestation:
    version: str
    unit_name: str
    canonical_module: str
    expected_app_dir: str
    expected_python: str
    expected_db_path: str
    release_sha: str
    installed_unit_attested: bool
    process_attested: bool
    inactive_safe_state: bool
    checks: tuple[str, ...]
    evidence_sha256: str
    deployment_proven: bool = field(init=False)
    financial_authority: bool = field(init=False, default=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "deployment_proven", bool(self.installed_unit_attested and self.process_attested))

    def as_dict(self) -> dict:
        return asdict(self)


def attest_weather_runtime(
    facts: WeatherRuntimeFacts,
    *,
    expected_app_dir: str | Path,
    expected_python: str | Path,
    expected_db_path: str | Path,
    expected_release_sha: str,
    expected_module: str = CANONICAL_MODULE,
) -> WeatherRuntimeAttestation:
    if not isinstance(facts, WeatherRuntimeFacts):
        raise WeatherRuntimeAttestationError("WEATHER_RUNTIME_FACTS_TYPE_INVALID")
    app_dir = _norm_path(expected_app_dir, "WEATHER_RUNTIME_APP_DIR_INVALID")
    python = _norm_path(expected_python, "WEATHER_RUNTIME_PYTHON_INVALID")
    db_path = _norm_path(expected_db_path, "WEATHER_RUNTIME_DB_PATH_INVALID")
    release = _sha(expected_release_sha, "WEATHER_RUNTIME_EXPECTED_RELEASE_INVALID")
    head = _sha(facts.repo_head_sha, "WEATHER_RUNTIME_HEAD_SHA_INVALID")
    marker = _sha(facts.release_marker_sha, "WEATHER_RUNTIME_MARKER_SHA_INVALID")
    if not isinstance(expected_module, str) or not expected_module.strip():
        raise WeatherRuntimeAttestationError("WEATHER_RUNTIME_MODULE_INVALID")
    if head != release or marker != release:
        raise WeatherRuntimeAttestationError("WEATHER_RUNTIME_RELEASE_IDENTITY_MISMATCH")

    tokens = _execstart_tokens(facts.unit_text)
    checks: list[str] = []
    if _norm_path(tokens[0], "WEATHER_RUNTIME_UNIT_PYTHON_INVALID") != python:
        raise WeatherRuntimeAttestationError("WEATHER_RUNTIME_UNIT_INTERPRETER_MISMATCH")
    if len(tokens) < 3 or tokens[1] != "-m" or tokens[2] != expected_module:
        raise WeatherRuntimeAttestationError("WEATHER_RUNTIME_UNIT_ENTRYPOINT_MISMATCH")
    raw_db = _arg_value(tokens, "--db")
    if raw_db is None or _norm_path(raw_db, "WEATHER_RUNTIME_UNIT_DB_INVALID") != db_path:
        raise WeatherRuntimeAttestationError("WEATHER_RUNTIME_UNIT_DB_MISMATCH")
    if any(
        forbidden in " ".join(tokens)
        for forbidden in (
            "weather_only_live_paper_v2",
            "weather_only_live_paper_v3",
            "weather_only_live_paper_v4",
            "app_trade_only",
            "command_worker",
        )
    ):
        raise WeatherRuntimeAttestationError("WEATHER_RUNTIME_UNIT_OBSOLETE_ENTRYPOINT")
    checks.extend(("RELEASE_MATCH", "UNIT_PYTHON_MATCH", "UNIT_CANONICAL_ENTRYPOINT", "UNIT_DB_MATCH"))

    if not facts.active:
        # systemd being inactive is not sufficient.  A manually launched or orphaned
        # old weather runtime would otherwise let preflight pass and create a second
        # scanner when the canonical service is started later.
        _assert_inactive_process_inventory_empty(facts.matching_weather_process_argvs)
        shell = WeatherRuntimeAttestation(
            version=RUNTIME_ATTESTATION_VERSION,
            unit_name=str(facts.unit_name),
            canonical_module=expected_module,
            expected_app_dir=app_dir,
            expected_python=python,
            expected_db_path=db_path,
            release_sha=release,
            installed_unit_attested=True,
            process_attested=False,
            inactive_safe_state=True,
            checks=tuple((*checks, "NO_ORPHAN_WEATHER_PROCESS", "SERVICE_INACTIVE_NO_DEPLOYMENT_CLAIM")),
            evidence_sha256="0" * 64,
        )
        payload = shell.as_dict()
        payload.pop("evidence_sha256", None)
        return WeatherRuntimeAttestation(
            **{
                name: getattr(shell, name)
                for name, definition in shell.__dataclass_fields__.items()
                if definition.init and name != "evidence_sha256"
            },
            evidence_sha256=_digest(payload),
        )

    if isinstance(facts.main_pid, bool) or not isinstance(facts.main_pid, int) or facts.main_pid <= 1:
        raise WeatherRuntimeAttestationError("WEATHER_RUNTIME_MAIN_PID_INVALID")
    if _norm_path(facts.process_cwd, "WEATHER_RUNTIME_PROCESS_CWD_INVALID") != app_dir:
        raise WeatherRuntimeAttestationError("WEATHER_RUNTIME_PROCESS_CWD_MISMATCH")
    if _norm_path(facts.process_executable, "WEATHER_RUNTIME_PROCESS_EXE_INVALID") != python:
        raise WeatherRuntimeAttestationError("WEATHER_RUNTIME_PROCESS_INTERPRETER_MISMATCH")
    argv = tuple(str(value) for value in facts.process_argv)
    if len(argv) < 3 or _norm_path(argv[0], "WEATHER_RUNTIME_PROCESS_ARGV0_INVALID") != python:
        raise WeatherRuntimeAttestationError("WEATHER_RUNTIME_PROCESS_ARGV0_MISMATCH")
    if argv[1:3] != ("-m", expected_module):
        raise WeatherRuntimeAttestationError("WEATHER_RUNTIME_PROCESS_ENTRYPOINT_MISMATCH")
    process_db = _arg_value(argv, "--db")
    if process_db is None or _norm_path(process_db, "WEATHER_RUNTIME_PROCESS_DB_INVALID") != db_path:
        raise WeatherRuntimeAttestationError("WEATHER_RUNTIME_PROCESS_DB_MISMATCH")

    matching = _normalized_process_inventory(facts.matching_weather_process_argvs)
    canonical_matches = [
        row for row in matching
        if len(row) >= 3 and row[1:3] == ("-m", expected_module)
    ]
    obsolete = [
        row for row in matching
        if any(
            name in " ".join(row)
            for name in (
                "weather_only_live_paper_v2",
                "weather_only_live_paper_v3",
                "weather_only_live_paper_v4",
                "weather_only_live_paper.py",
            )
        )
    ]
    if len(canonical_matches) != 1 or obsolete or len(matching) != 1:
        raise WeatherRuntimeAttestationError("WEATHER_RUNTIME_DUPLICATE_OR_OBSOLETE_PROCESS")
    checks.extend((
        "PROCESS_PID_VALID",
        "PROCESS_CWD_MATCH",
        "PROCESS_INTERPRETER_MATCH",
        "PROCESS_CANONICAL_ENTRYPOINT",
        "PROCESS_DB_MATCH",
        "SINGLE_WEATHER_PROCESS",
    ))

    shell = WeatherRuntimeAttestation(
        version=RUNTIME_ATTESTATION_VERSION,
        unit_name=str(facts.unit_name),
        canonical_module=expected_module,
        expected_app_dir=app_dir,
        expected_python=python,
        expected_db_path=db_path,
        release_sha=release,
        installed_unit_attested=True,
        process_attested=True,
        inactive_safe_state=False,
        checks=tuple(checks),
        evidence_sha256="0" * 64,
    )
    payload = shell.as_dict()
    payload.pop("evidence_sha256", None)
    return WeatherRuntimeAttestation(
        **{
            name: getattr(shell, name)
            for name, definition in shell.__dataclass_fields__.items()
            if definition.init and name != "evidence_sha256"
        },
        evidence_sha256=_digest(payload),
    )
