"""Read-only, offline intake of the real retained Gate 3 evidence-preflight
package/restriction-history/protocol bytes through the independently
reviewed checker (``tools/v11_gate3_evidence_preflight_checker.py``), using
honest current clock/resource observations in place of a fixed historical
snapshot.

Every prior evaluation of this exact retained package was either driven by
hand-assembled ``ClockObservation``/``ResourceObservation`` values recorded
once in a review document, or exercised the checker against hand-written
synthetic stand-in bytes (``tests/v11_gate3_preflight_synthetic_cases.py``).
This module is the first thing in the repository that reads the actual
retained private bytes at their own bound path -- verifying each against
its own declared SHA-256/length before use -- and hands them, unmodified,
together with a fresh real measurement of this host's current clock and
free disk/memory, to the same pure ``check_evidence_preflight_package`` call
an independent reviewer already exercised on this package once before. It
lets any future cycle re-confirm the package's actual *current* status
(the frozen window closes at a fixed UTC instant, so the honest answer
necessarily changes over time) without a human re-deriving clock/resource
inputs by hand each time.

Why not the offline attempt model (``tools/v11_gate3_preflight_attempt_
model.py``, ``AttemptModelGuard``) instead? Its ``admit_synthetic`` entry
point walks every parsed package/restrictions/binding object and refuses
outright (``NON_SYNTHETIC_PATH_OR_INVALID_JSON``) the instant it finds a
``"path"``/``"private_root"`` key that is not ``synthetic://``-prefixed
(see that module's ``_walk_synthetic_paths``) -- a deliberate, code-level
boundary, not merely a test-suite convention, that makes it structurally
impossible for that layer to admit bytes containing real filesystem paths.
The real retained ``package.json`` genuinely contains such paths (for
example ``prerequisites.owner_directive_original_record.path``), so routing
it through ``admit_synthetic`` would always short-circuit to that one
uninformative reason before the richer checker evaluation below ever runs
-- confirmed empirically while building this module; see
``tests/test_v11_gate3_evidence_preflight_real_intake.py``. The checker
module itself has no such restriction and was already independently
reviewed against this exact real package.

Safety boundary, repeated from the controlling protocol/handoff documents:
  * No network, no subprocess, no decode. Only bounded, regular-file reads
    on paths the public binding JSON itself already discloses, plus this
    host's own free-disk/free-memory counters and wall clock.
  * Every retained byte string is read once, verified against its own bound
    SHA-256/length before use, and never written back or mutated.
  * The clock/resource observations built here are honest current host
    measurements, never a fabricated passing value: this host has no
    qualified clock-calibration recorder or physical storage reservation
    mechanism yet (both remain open external prerequisites -- see
    ``docs/V11_R09_GATE3_G3L_RECONCILIATION_20261002.md``'s ``clocks.*`` and
    ``storage.*`` rows), so ``current_clock_observation`` reports an explicit,
    documented "not qualified" sentinel rather than guessing a value that
    might happen to pass.
  * This module grants no execution/provider/capture authority, no G3-L
    credit and no score change. A refusal here is the expected, correct
    outcome given the retained evidence; a ``SATISFIED`` outcome would still
    confer nothing beyond what the checker's own module docstring documents
    (``CHECKER_SCHEMA_AND_POLICY_SATISFIED_NOT_EXECUTABLE``).
"""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from tools.v11_gate3_evidence_preflight_checker import (
    CheckResult, ClockObservation, OUTCOME_SATISFIED, ResourceObservation,
    StateLedger, check_evidence_preflight_package, strict_json_loads,
)

SCHEMA = "ALPHA_V11_EVIDENCE_PREFLIGHT_REAL_INTAKE_V1"
MAX_INTAKE_BYTES = 1_048_576
READ_CHUNK_BYTES = 65_536

# Explicit "no qualified calibration method exists for this host" sentinel --
# never a best-effort estimate of real uncertainty/calibration age. Both
# values are far beyond the checker's real clock_uncertainty_seconds=1 /
# clock_calibration_max_age_seconds=60 floors, so this can never be mistaken
# for, or accidentally drift into, a passing measurement.
NO_QUALIFIED_CLOCK_SECONDS = 86_400.0


class EvidenceIntakeError(ValueError):
    """Raised instead of silently proceeding on unreadable, oversized or
    hash/length-mismatched retained evidence bytes."""


@dataclass(frozen=True)
class RetainedRef:
    path: str
    sha256: str
    byte_length: int


def _read_bounded_regular_file(path: Path, *, label: str,
                               max_bytes: int = MAX_INTAKE_BYTES) -> bytes:
    """Refuse links/special files and oversized regular files before reading.

    The nonblocking, no-follow open and descriptor check also cover a final
    path replacement between lstat and open. A growing file can consume at
    most ``max_bytes + 1`` bytes before refusal; no read requests EOF size.
    """
    try:
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode):
            raise EvidenceIntakeError(f"{label} is not a regular file")
        flags = os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC
        fd = os.open(path, flags)
        try:
            opened = os.fstat(fd)
            if not stat.S_ISREG(opened.st_mode):
                raise EvidenceIntakeError(f"{label} is not a regular file")
            if opened.st_size > max_bytes:
                raise EvidenceIntakeError(
                    f"{label} bytes exceed the {max_bytes}-byte intake cap")
            chunks = []
            total = 0
            while True:
                chunk = os.read(fd, min(READ_CHUNK_BYTES, max_bytes + 1 - total))
                if not chunk:
                    return b"".join(chunks)
                total += len(chunk)
                if total > max_bytes:
                    raise EvidenceIntakeError(
                        f"{label} bytes exceed the {max_bytes}-byte intake cap")
                chunks.append(chunk)
        finally:
            os.close(fd)
    except FileNotFoundError:
        # Preserve the existing missing-binding contract for offline callers.
        raise
    except OSError as exc:
        raise EvidenceIntakeError(f"{label} could not be read as a regular file") from exc


def parse_binding(binding_raw: bytes) -> dict:
    binding = strict_json_loads(binding_raw)
    if not isinstance(binding, dict):
        raise EvidenceIntakeError("binding JSON did not parse to an object")
    return binding


def _retained_ref(binding: dict, key: str) -> RetainedRef:
    entry = binding.get(key)
    if not isinstance(entry, dict):
        raise EvidenceIntakeError(f"binding missing or malformed {key!r} reference")
    try:
        return RetainedRef(path=entry["path"], sha256=entry["sha256"],
                            byte_length=entry["byte_length"])
    except KeyError as exc:
        raise EvidenceIntakeError(f"binding {key!r} reference missing {exc}") from exc


def verify_retained_bytes(raw: bytes, ref: RetainedRef, *, label: str,
                           max_bytes: int = MAX_INTAKE_BYTES) -> bytes:
    """Return ``raw`` unchanged only if it matches ``ref``'s own declared
    length/hash and stays under the intake cap; refuse otherwise rather than
    evaluating bytes the binding does not actually vouch for."""
    if type(raw) is not bytes:
        raise EvidenceIntakeError(f"{label} bytes must be type bytes")
    if len(raw) > max_bytes:
        raise EvidenceIntakeError(f"{label} bytes exceed the {max_bytes}-byte intake cap")
    if len(raw) != ref.byte_length:
        raise EvidenceIntakeError(
            f"{label} byte length {len(raw)} != bound {ref.byte_length}")
    digest = hashlib.sha256(raw).hexdigest()
    if digest != ref.sha256:
        raise EvidenceIntakeError(f"{label} sha256 {digest} != bound {ref.sha256}")
    return raw


def read_private_evidence(binding: dict) -> tuple[bytes, bytes]:
    """Read-only: open the real retained package/restriction-history bytes
    at the exact absolute path the public binding JSON itself discloses,
    and refuse before returning anything that does not match that binding's
    own hash/length. Never writes, deletes or opens any other path."""
    package_ref = _retained_ref(binding, "private_package")
    restrictions_ref = _retained_ref(binding, "private_restrictions")
    package_raw = verify_retained_bytes(
        _read_bounded_regular_file(Path(package_ref.path), label="private_package"),
        package_ref, label="private_package")
    restrictions_raw = verify_retained_bytes(
        _read_bounded_regular_file(Path(restrictions_ref.path), label="private_restrictions"),
        restrictions_ref, label="private_restrictions")
    return package_raw, restrictions_raw


def read_protocol_bytes(binding: dict, *, repo_docs_dir: Path) -> bytes:
    """Read the protocol document from this repository's own tracked copy
    (never from the absolute authoring-time path recorded in the binding,
    which may not exist in, or may differ from, the current worktree), and
    refuse before returning anything that does not match the binding's own
    bound hash/length for it."""
    protocol_ref = _retained_ref(binding, "protocol")
    protocol_path = repo_docs_dir / Path(protocol_ref.path).name
    return verify_retained_bytes(
        _read_bounded_regular_file(protocol_path, label="protocol"),
        protocol_ref, label="protocol")


def current_resource_observation() -> ResourceObservation:
    """Honest, real, current host measurement. ``physically_reserved_bytes``
    is honestly 0: no real physical-reservation mechanism exists on this
    host yet (the retained package's own
    ``storage_qualification.physically_reserved_bytes`` is likewise 0), so
    this must never report a fabricated reservation."""
    free_disk = shutil.disk_usage("/").free
    return ResourceObservation(
        free_disk_bytes_after_reservation=free_disk,
        mem_available_bytes_after_reservation=_read_mem_available_bytes(),
        physically_reserved_bytes=0,
    )


def _read_mem_available_bytes() -> int:
    with open("/proc/meminfo", "r", encoding="ascii") as handle:
        for line in handle:
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) * 1024
    raise EvidenceIntakeError("MemAvailable not found in /proc/meminfo")


def current_clock_observation() -> ClockObservation:
    """Honest current wall-clock reading with an explicit, documented
    "not qualified" calibration sentinel -- see ``NO_QUALIFIED_CLOCK_SECONDS``.
    Never fabricates a passing calibration/uncertainty value."""
    measured_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
    return ClockObservation(
        measured_utc=measured_utc,
        uncertainty_seconds=NO_QUALIFIED_CLOCK_SECONDS,
        calibration_age_seconds=NO_QUALIFIED_CLOCK_SECONDS,
        monotonic_consistent=True,
    )


def evaluate_real_evidence(
        package_raw: bytes, restrictions_raw: bytes, protocol_raw: bytes,
        binding_raw: bytes, *, clock: ClockObservation,
        resources: ResourceObservation, ledger: Optional[StateLedger] = None,
        review_terminal: Optional[dict] = None,
        restart_requested: bool = False) -> CheckResult:
    """Run retained bytes through the same already-reviewed
    ``check_evidence_preflight_package`` call. Pure/offline: no network, no
    dispatch, no mutation of any input."""
    return check_evidence_preflight_package(
        package_raw=package_raw, restrictions_raw=restrictions_raw,
        binding_raw=binding_raw, protocol_raw=protocol_raw,
        clock=clock, resources=resources,
        ledger=ledger if ledger is not None else StateLedger(()),
        review_terminal=review_terminal, restart_requested=restart_requested)


def build_report(check_result: CheckResult, *, generated_at_utc: str) -> dict:
    """Safe-to-commit summary: reason codes and booleans only, never raw
    private bytes, paths or evidence content."""
    report = check_result.to_dict()
    report["intake_schema"] = SCHEMA
    report["generated_at_utc"] = generated_at_utc
    report["satisfied"] = check_result.outcome == OUTCOME_SATISFIED
    report["execution_authority"] = False
    report["provider_authority"] = False
    report["capture_authority"] = False
    return report


def verify_binding_self_consistent(binding_raw: bytes, *, max_bytes: int = MAX_INTAKE_BYTES) -> bytes:
    """The public binding JSON is read directly from the repo and has no
    separate outer hash to check itself against; only bound the read before
    it is parsed."""
    if len(binding_raw) > max_bytes:
        raise EvidenceIntakeError(f"binding bytes exceed the {max_bytes}-byte intake cap")
    return binding_raw


def run_real_evidence_intake(*, repo_docs_dir: Path, binding_path: Path) -> dict:
    """End-to-end, read-only orchestration: load the real binding, read and
    verify the real retained private/protocol bytes, take honest current
    clock/resource measurements, and report the actual current checker
    outcome. Never writes, deletes or dispatches."""
    binding_raw = verify_binding_self_consistent(
        _read_bounded_regular_file(binding_path, label="binding"))
    binding = parse_binding(binding_raw)
    package_raw, restrictions_raw = read_private_evidence(binding)
    protocol_raw = read_protocol_bytes(binding, repo_docs_dir=repo_docs_dir)
    clock = current_clock_observation()
    check_result = evaluate_real_evidence(
        package_raw, restrictions_raw, protocol_raw, binding_raw,
        clock=clock, resources=current_resource_observation())
    return build_report(check_result, generated_at_utc=clock.measured_utc)


if __name__ == "__main__":
    import json
    import sys

    _repo_root = Path(__file__).resolve().parent.parent
    _report = run_real_evidence_intake(
        repo_docs_dir=_repo_root / "docs",
        binding_path=_repo_root / "docs" / "V11_R09_GATE3_EVIDENCE_PREFLIGHT_PACKAGE_20261002.json",
    )
    json.dump(_report, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
