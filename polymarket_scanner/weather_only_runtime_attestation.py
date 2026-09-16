from __future__ import annotations

"""Pure read-only attestation logic for the canonical weather-paper runtime.

R22 cannot be closed by a renderer or release marker alone. A later authorized host
check must prove the installed unit and, when active, the actual process are the
canonical corrective runtime with the isolated weather release/config paths.
"""

import hashlib
import json
import os
import shlex
from dataclasses import asdict, dataclass, field
from pathlib import Path


RUNTIME_ATTESTATION_VERSION = "weather_runtime_attestation_v3_full_isolated_unit_identity"
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
    if len(text) not in {40, 64} or any(ch not in "0123456789abcdef" for ch in text):
        raise WeatherRuntimeAttestationError(code)
    return text


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _directive_values(unit_text: str, name: str) -> tuple[str, ...]:
    prefix = f"{name}="
    return tuple(
        raw.strip().split("=", 1)[1].strip()
        for raw in str(unit_text or "").splitlines()
        if raw.strip().startswith(prefix)
    )


def _unique_directive(unit_text: str, name: str, code: str, *, allow_empty: bool = False) -> str:
    values = _directive_values(unit_text, name)
    if len(values) != 1 or (not allow_empty and not values[0]):
        raise WeatherRuntimeAttestationError(code)
    return values[0]


def _execstart_tokens(unit_text: str) -> tuple[str, ...]:
    line = _unique_directive(unit_text, "ExecStart", "WEATHER_RUNTIME_EXECSTART_NOT_UNIQUE")
    try:
        tokens = tuple(shlex.split(line))
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


def _require_arg_path(tokens: tuple[str, ...], name: str, expected: str, code: str) -> None:
    raw = _arg_value(tokens, name)
    if raw is None or _norm_path(raw, code) != expected:
        raise WeatherRuntimeAttestationError(code)


def _normalized_process_inventory(rows: tuple[tuple[str, ...], ...]) -> tuple[tuple[str, ...], ...]:
    return tuple(tuple(str(part) for part in row) for row in rows)


def _assert_inactive_process_inventory_empty(rows: tuple[tuple[str, ...], ...]) -> None:
    if _normalized_process_inventory(rows):
        raise WeatherRuntimeAttestationError("WEATHER_RUNTIME_ORPHAN_PROCESS_WHILE_SERVICE_INACTIVE")


def _attest_unit_hardening(unit_text: str) -> None:
    required = {
        "NoNewPrivileges": "true",
        "PrivateTmp": "true",
        "PrivateDevices": "true",
        "ProtectHome": "read-only",
        "ProtectSystem": "strict",
        "ProtectKernelTunables": "true",
        "ProtectKernelModules": "true",
        "ProtectControlGroups": "true",
        "RestrictSUIDSGID": "true",
        "LockPersonality": "true",
        "MemorySwapMax": "0",
    }
    for name, expected in required.items():
        value = _unique_directive(
            unit_text,
            name,
            f"WEATHER_RUNTIME_UNIT_HARDENING_{name.upper()}_INVALID",
        )
        if value != expected:
            raise WeatherRuntimeAttestationError(
                f"WEATHER_RUNTIME_UNIT_HARDENING_{name.upper()}_INVALID"
            )
    for name in ("CapabilityBoundingSet", "AmbientCapabilities"):
        value = _unique_directive(
            unit_text,
            name,
            f"WEATHER_RUNTIME_UNIT_HARDENING_{name.upper()}_INVALID",
            allow_empty=True,
        )
        if value != "":
            raise WeatherRuntimeAttestationError(
                f"WEATHER_RUNTIME_UNIT_HARDENING_{name.upper()}_INVALID"
            )


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
    expected_status_path: str | None
    expected_release_file: str | None
    expected_environment_file: str | None
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
    expected_status_path: str | Path | None = None,
    expected_release_file: str | Path | None = None,
    expected_environment_file: str | Path | None = None,
    expected_module: str = CANONICAL_MODULE,
) -> WeatherRuntimeAttestation:
    if not isinstance(facts, WeatherRuntimeFacts):
        raise WeatherRuntimeAttestationError("WEATHER_RUNTIME_FACTS_TYPE_INVALID")
    app_dir = _norm_path(expected_app_dir, "WEATHER_RUNTIME_APP_DIR_INVALID")
    python = _norm_path(expected_python, "WEATHER_RUNTIME_PYTHON_INVALID")
    db_path = _norm_path(expected_db_path, "WEATHER_RUNTIME_DB_PATH_INVALID")
    status_path = (
        _norm_path(expected_status_path, "WEATHER_RUNTIME_STATUS_PATH_INVALID")
        if expected_status_path is not None else None
    )
    release_file = (
        _norm_path(expected_release_file, "WEATHER_RUNTIME_RELEASE_FILE_INVALID")
        if expected_release_file is not None else None
    )
    environment_file = (
        _norm_path(expected_environment_file, "WEATHER_RUNTIME_ENV_FILE_INVALID")
        if expected_environment_file is not None else None
    )
    release = _sha(expected_release_sha, "WEATHER_RUNTIME_EXPECTED_RELEASE_INVALID")
    head = _sha(facts.repo_head_sha, "WEATHER_RUNTIME_HEAD_SHA_INVALID")
    marker = _sha(facts.release_marker_sha, "WEATHER_RUNTIME_MARKER_SHA_INVALID")
    if not isinstance(expected_module, str) or not expected_module.strip():
        raise WeatherRuntimeAttestationError("WEATHER_RUNTIME_MODULE_INVALID")
    if head != release or marker != release:
        raise WeatherRuntimeAttestationError("WEATHER_RUNTIME_RELEASE_IDENTITY_MISMATCH")

    unit_text = facts.unit_text
    tokens = _execstart_tokens(unit_text)
    checks: list[str] = []
    if _norm_path(tokens[0], "WEATHER_RUNTIME_UNIT_PYTHON_INVALID") != python:
        raise WeatherRuntimeAttestationError("WEATHER_RUNTIME_UNIT_INTERPRETER_MISMATCH")
    if len(tokens) < 3 or tokens[1] != "-m" or tokens[2] != expected_module:
        raise WeatherRuntimeAttestationError("WEATHER_RUNTIME_UNIT_ENTRYPOINT_MISMATCH")
    _require_arg_path(tokens, "--db", db_path, "WEATHER_RUNTIME_UNIT_DB_MISMATCH")
    if status_path is not None:
        _require_arg_path(tokens, "--status", status_path, "WEATHER_RUNTIME_UNIT_STATUS_MISMATCH")
    if release_file is not None:
        _require_arg_path(tokens, "--release-file", release_file, "WEATHER_RUNTIME_UNIT_RELEASE_FILE_MISMATCH")
    if "--once" in tokens:
        raise WeatherRuntimeAttestationError("WEATHER_RUNTIME_UNIT_ONCE_MODE_FORBIDDEN")
    if _norm_path(
        _unique_directive(unit_text, "WorkingDirectory", "WEATHER_RUNTIME_UNIT_WORKDIR_INVALID"),
        "WEATHER_RUNTIME_UNIT_WORKDIR_INVALID",
    ) != app_dir:
        raise WeatherRuntimeAttestationError("WEATHER_RUNTIME_UNIT_WORKDIR_MISMATCH")
    if environment_file is not None:
        env_value = _unique_directive(unit_text, "EnvironmentFile", "WEATHER_RUNTIME_UNIT_ENV_FILE_NOT_UNIQUE")
        if _norm_path(env_value, "WEATHER_RUNTIME_UNIT_ENV_FILE_INVALID") != environment_file:
            raise WeatherRuntimeAttestationError("WEATHER_RUNTIME_UNIT_ENV_FILE_MISMATCH")
    if release_file is not None:
        pre = _unique_directive(unit_text, "ExecStartPre", "WEATHER_RUNTIME_UNIT_PRESTART_NOT_UNIQUE")
        try:
            pre_tokens = tuple(shlex.split(pre))
        except ValueError:
            raise WeatherRuntimeAttestationError("WEATHER_RUNTIME_UNIT_PRESTART_PARSE_INVALID") from None
        expected_verifier = os.path.join(app_dir, "deploy", "verify-runtime-release.sh")
        if (
            len(pre_tokens) != 4
            or pre_tokens[0] != "/bin/bash"
            or _norm_path(pre_tokens[1], "WEATHER_RUNTIME_UNIT_PRESTART_VERIFIER_INVALID") != expected_verifier
            or _norm_path(pre_tokens[2], "WEATHER_RUNTIME_UNIT_PRESTART_APP_INVALID") != app_dir
            or _norm_path(pre_tokens[3], "WEATHER_RUNTIME_UNIT_PRESTART_RELEASE_INVALID") != release_file
        ):
            raise WeatherRuntimeAttestationError("WEATHER_RUNTIME_UNIT_PRESTART_IDENTITY_MISMATCH")
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
    _attest_unit_hardening(unit_text)
    checks.extend((
        "RELEASE_MATCH",
        "UNIT_PYTHON_MATCH",
        "UNIT_CANONICAL_ENTRYPOINT",
        "UNIT_DB_MATCH",
        "UNIT_WORKDIR_MATCH",
        "UNIT_HARDENING_MATCH",
    ))
    if status_path is not None:
        checks.append("UNIT_STATUS_MATCH")
    if release_file is not None:
        checks.extend(("UNIT_RELEASE_FILE_MATCH", "UNIT_PRESTART_RELEASE_CHECK"))
    if environment_file is not None:
        checks.append("UNIT_ISOLATED_ENV_FILE_MATCH")

    common = dict(
        version=RUNTIME_ATTESTATION_VERSION,
        unit_name=str(facts.unit_name),
        canonical_module=expected_module,
        expected_app_dir=app_dir,
        expected_python=python,
        expected_db_path=db_path,
        expected_status_path=status_path,
        expected_release_file=release_file,
        expected_environment_file=environment_file,
        release_sha=release,
    )

    if not facts.active:
        _assert_inactive_process_inventory_empty(facts.matching_weather_process_argvs)
        shell = WeatherRuntimeAttestation(
            **common,
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
    _require_arg_path(argv, "--db", db_path, "WEATHER_RUNTIME_PROCESS_DB_MISMATCH")
    if status_path is not None:
        _require_arg_path(argv, "--status", status_path, "WEATHER_RUNTIME_PROCESS_STATUS_MISMATCH")
    if release_file is not None:
        _require_arg_path(argv, "--release-file", release_file, "WEATHER_RUNTIME_PROCESS_RELEASE_FILE_MISMATCH")
    if "--once" in argv:
        raise WeatherRuntimeAttestationError("WEATHER_RUNTIME_PROCESS_ONCE_MODE_FORBIDDEN")

    matching = _normalized_process_inventory(facts.matching_weather_process_argvs)
    canonical_matches = [row for row in matching if len(row) >= 3 and row[1:3] == ("-m", expected_module)]
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
    if status_path is not None:
        checks.append("PROCESS_STATUS_MATCH")
    if release_file is not None:
        checks.append("PROCESS_RELEASE_FILE_MATCH")

    shell = WeatherRuntimeAttestation(
        **common,
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
