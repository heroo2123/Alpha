from __future__ import annotations

"""Secret-free attestation of the installed Python environment against requirements.txt."""

import hashlib
import re
from importlib import metadata
from pathlib import Path

DEPENDENCY_ATTESTATION_VERSION = "python_dist_versions_v1_locked_spec_sha256"
_NAME_NORMALIZER = re.compile(r"[-_.]+")


def _normalize_name(value: str) -> str:
    return _NAME_NORMALIZER.sub("-", value.strip().lower())


def parse_exact_requirements(path: str | Path) -> dict[str, str]:
    """Parse only exact ``name==version`` requirements; anything else fails closed."""
    target = Path(path).expanduser().resolve()
    expected: dict[str, str] = {}
    for raw in target.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("-r "):
            raise ValueError("runtime dependency attestation does not accept nested requirement includes")
        if "==" not in line or any(op in line for op in (">=", "<=", "~=", "!=", "===", ";")):
            raise ValueError(f"dependency is not an unconditional exact pin: {line}")
        name_part, version = line.split("==", 1)
        name_part = name_part.strip()
        version = version.strip()
        if "[" in name_part:
            name_part = name_part.split("[", 1)[0]
        name = _normalize_name(name_part)
        if not name or not version or name in expected:
            raise ValueError(f"invalid or duplicate dependency pin: {line}")
        expected[name] = version
    if not expected:
        raise ValueError("dependency lock is empty")
    return expected


def attest_dependency_environment(path: str | Path) -> dict:
    target = Path(path).expanduser().resolve()
    base = {
        "version": DEPENDENCY_ATTESTATION_VERSION,
        "requirements_file": str(target),
        "requirements_present": target.is_file(),
        "requirements_sha256": None,
        "compatible": False,
        "expected_count": 0,
        "matched_count": 0,
        "mismatches": {},
        "scope": (
            "EXACT_INSTALLED_DISTRIBUTION_VERSIONS_PLUS_REQUIREMENTS_FILE_SHA256; "
            "DOES_NOT_VERIFY_PYPI_WHEEL_OR_SDIST_CONTENT_HASHES"
        ),
    }
    if not target.is_file():
        base["reason"] = "runtime dependency lock file missing"
        return base

    payload = target.read_bytes()
    base["requirements_sha256"] = hashlib.sha256(payload).hexdigest()
    try:
        expected = parse_exact_requirements(target)
    except (OSError, UnicodeError, ValueError) as exc:
        base["reason"] = f"runtime dependency lock invalid: {exc}"
        return base

    mismatches: dict[str, dict[str, str | None]] = {}
    matched = 0
    for name, wanted in sorted(expected.items()):
        try:
            installed = metadata.version(name)
        except metadata.PackageNotFoundError:
            installed = None
        if installed == wanted:
            matched += 1
        else:
            mismatches[name] = {"expected": wanted, "installed": installed}

    compatible = not mismatches
    base.update(
        {
            "compatible": compatible,
            "expected_count": len(expected),
            "matched_count": matched,
            "mismatches": mismatches,
            "reason": (
                "installed Python distributions exactly match the reviewed runtime lock"
                if compatible
                else "installed Python distributions differ from the reviewed runtime lock"
            ),
        }
    )
    return base
