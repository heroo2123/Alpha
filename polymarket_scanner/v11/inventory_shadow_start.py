"""Bounded local start for the offline InventoryTransform SHADOW observer.

One invocation consumes explicitly named local fixtures (files, or the regular
``*.json`` entries of one directory), emits content-identified SHADOW
diagnostics into a dedicated directory, and exits. It is not a service and has
no provider, receipt, account, order, collateral, or weather runtime path.
See docs/V11_INVENTORY_SHADOW_START_CONTRACT.md.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import time

from . import inventory_shadow as shadow
from .inventory_shadow import ShadowInputError
from .structural_evidence import Coverage, EvidenceClass, Limits, load_offline_json


START_VERSION = "v11_inventory_shadow_start_contract_v1"
ACTIVATION = "OFFLINE_SHADOW_DIAGNOSTICS_ONLY"
MAX_BATCH_INPUTS = 32
MAX_OUTPUT_ENTRIES = 1024
MAX_RUN_SECONDS = 120.0
FIXTURE_FIELDS = frozenset({
    "fixture_version", "evidence_class", "chain_status", "source", "raw_source_file",
    "coverage", "payload", "expected", "unresolved", "synthetic_conversion",
})
_ARTIFACT_NAME = re.compile(r"inventory-shadow-[0-9a-f]{64}\.json")
_TEMPORARY_PREFIX = ".inventory-shadow-"
_ALLOWED_PROJECT_MODULES = frozenset({
    "polymarket_scanner", "polymarket_scanner.safe_logging", "polymarket_scanner.v11",
    "polymarket_scanner.v11.inventory_shadow", "polymarket_scanner.v11.inventory_shadow_start",
    "polymarket_scanner.v11.structural_evidence", "polymarket_scanner.v11.neg_risk_contract",
})
_DENIED_AUDIT_EVENTS = frozenset({
    "subprocess.Popen", "os.system", "os.exec", "os.fork", "os.forkpty",
    "os.posix_spawn", "os.spawn", "ctypes.dlopen", "ctypes.dlsym", "ctypes.dlsym/handle",
})
# _posixsubprocess.fork_exec(), a ctypes-obtained libc handle, and a cffi
# FFI().dlopen(None) handle can each reach a raw fork/exec or socket syscall
# without emitting any of the events above, so the modules themselves are
# denied at import time instead.
_DENIED_IMPORT_MODULES = frozenset({"_posixsubprocess", "ctypes", "_ctypes", "_cffi_backend"})


def _denied_import_match(name: str) -> bool:
    """True for a denied module loaded under its own name or a dotted alias.

    ``import aliaspkg._cffi_backend`` raises an ``import`` audit event with
    ``args[0] == "aliaspkg._cffi_backend"`` and leaves the same entry under
    that full name in ``sys.modules``; an exact-name check misses it even
    though the loader resolves the identical extension file.
    """
    return name.rpartition(".")[2] in _DENIED_IMPORT_MODULES


def artifact_name(source_file_sha256: str, event_slug: str) -> str:
    """The only file an (observer version, input bytes, event) triple may occupy."""
    identity = json.dumps([shadow.VERSION, source_file_sha256, event_slug],
                          separators=(",", ":"), ensure_ascii=True)
    return "inventory-shadow-" + hashlib.sha256(identity.encode("ascii")).hexdigest() + ".json"


def _open_directory(path: Path, code: str) -> int:
    """Open a directory without following a symlink at any component."""
    try:
        path = Path(path)
        absolute = path if path.is_absolute() else Path.cwd() / path
        parts = absolute.parts
        fd = os.open(parts[0], os.O_RDONLY | os.O_DIRECTORY)
        try:
            for component in parts[1:]:
                next_fd = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = next_fd
        except BaseException:
            os.close(fd)
            raise
    except (OSError, TypeError, ValueError) as exc:
        raise ShadowInputError(code) from exc
    return fd


def _entries(fd: int, cap: int, code: str) -> list[str]:
    names: list[str] = []
    with os.scandir(fd) as entries:
        for entry in entries:
            if len(names) >= cap:
                raise ShadowInputError(code)
            names.append(entry.name)
    return sorted(names)


def _batch(inputs: tuple[Path, ...], input_dir: Path | None) -> list[Path]:
    if input_dir is not None:
        if inputs:
            raise ShadowInputError("INPUT_SELECTION_AMBIGUOUS")
        fd = _open_directory(input_dir, "INPUT_DIR_REFUSED")
        try:
            names = _entries(fd, MAX_BATCH_INPUTS, "BATCH_INPUT_LIMIT")
            # The batch is the whole directory. One entry that is not a plain
            # regular *.json file refuses the start instead of being skipped.
            for name in names:
                if (name.startswith(".") or not name.endswith(".json")
                        or not stat.S_ISREG(os.stat(name, dir_fd=fd, follow_symlinks=False).st_mode)):
                    raise ShadowInputError("INPUT_DIR_ENTRY_REFUSED")
        except OSError as exc:
            raise ShadowInputError("INPUT_DIR_REFUSED") from exc
        finally:
            os.close(fd)
        paths = [Path(input_dir) / name for name in names]
    else:
        paths = [Path(path) for path in inputs]
        if len(paths) > MAX_BATCH_INPUTS:
            raise ShadowInputError("BATCH_INPUT_LIMIT")
    if not paths:
        raise ShadowInputError("EMPTY_BATCH")
    return paths


def _observe(path: Path, event_slug: str, require_complete: bool, limits: Limits) -> dict:
    """Observer report plus the start-only fixture requirements."""
    report = shadow.observe_file(path, event_slug, limits)
    try:
        loaded = load_offline_json(path, limits)
    except Exception as exc:
        raise ShadowInputError("INPUT_CHANGED_DURING_START") from exc
    fixture = loaded.payload
    if not isinstance(fixture, dict) or loaded.raw_sha256 != report["source_file_sha256"]:
        raise ShadowInputError("INPUT_CHANGED_DURING_START")
    if not set(fixture) <= FIXTURE_FIELDS:
        # Receipt-shaped or otherwise unreviewed material has no acceptance
        # path here; it is refused, never silently ignored.
        raise ShadowInputError("UNSUPPORTED_FIXTURE_FIELD")
    version = fixture.get("fixture_version")
    if type(version) is not str or not 1 <= len(version) <= 256:
        raise ShadowInputError("FIXTURE_VERSION_REQUIRED")
    if fixture.get("chain_status", "CHAIN_UNVERIFIED") != "CHAIN_UNVERIFIED":
        raise ShadowInputError("CHAIN_STATUS_UNSUPPORTED")
    if not isinstance(fixture.get("coverage"), dict):
        raise ShadowInputError("COVERAGE_DECLARATION_REQUIRED")
    if fixture["source"].get("raw_sha256") is None:
        raise ShadowInputError("LINEAGE_RAW_HASH_REQUIRED")
    if report["evidence_class_counts"][EvidenceClass.CHAIN_RECEIPT.value] != 0:
        raise ShadowInputError("CHAIN_RECEIPT_NOT_ACCEPTED")
    if require_complete and report["coverage"] != Coverage.COMPLETE.value:
        raise ShadowInputError("COMPLETENESS_REQUIRED_BUT_" + report["coverage"])
    return report


def _verify_artifact(fd: int, output_dir: Path, name: str) -> None:
    try:
        artifact = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    except OSError as exc:
        raise ShadowInputError("OUTPUT_NOT_REGULAR") from exc
    with os.fdopen(artifact, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ShadowInputError("OUTPUT_NOT_REGULAR")
        raw = stream.read(shadow.MAX_ARTIFACT_BYTES + 1)
    try:
        report = json.loads(raw)
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise ShadowInputError("OUTPUT_TAMPERED_INVALID_JSON") from exc
    if not isinstance(report, dict):
        raise ShadowInputError("OUTPUT_TAMPERED_INVALID_JSON")
    if report.get("version") != shadow.VERSION:
        raise ShadowInputError("OUTPUT_STALE_VERSION")
    digest, event_slug = report.get("source_file_sha256"), report.get("event_slug")
    if type(digest) is not str or type(event_slug) is not str or artifact_name(digest, event_slug) != name:
        raise ShadowInputError("OUTPUT_IDENTITY_MISMATCH")
    # The accepted writer is the verifier: it re-applies artifact policy and
    # content identity, then compares canonical bytes with the existing file
    # and never replaces it.
    try:
        created = shadow.write_artifact(Path(output_dir) / name, report, dir_fd=fd)
    except ShadowInputError as exc:
        raise ShadowInputError(f"OUTPUT_TAMPERED_{exc}") from exc
    except RecursionError as exc:
        raise ShadowInputError("OUTPUT_TAMPERED_INVALID_JSON") from exc
    if created:
        raise ShadowInputError("OUTPUT_CHANGED_DURING_START")


def run(event_slug: str, output_dir: Path, *, inputs: tuple[Path, ...] = (),
        input_dir: Path | None = None, require_complete: bool = False,
        limits: Limits = Limits()) -> dict:
    """Process one explicit batch and return the start summary, or refuse.

    Every input is observed before anything is written, so a first-pass
    refusal leaves the output directory unchanged. Each input is re-read
    immediately before its write, so a later refusal may leave earlier
    artifacts of the batch in place.
    """
    start = time.monotonic()

    def within_deadline() -> None:
        if time.monotonic() - start > MAX_RUN_SECONDS:
            raise ShadowInputError("RUN_TIME_LIMIT")

    if os.geteuid() == 0:
        raise ShadowInputError("ROOT_REFUSED")
    paths = _batch(tuple(inputs), input_dir)
    fd = _open_directory(output_dir, "OUTPUT_DIR_REFUSED")
    try:
        try:
            # One start per output directory; released when the descriptor closes.
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise ShadowInputError("OUTPUT_DIR_BUSY") from exc
        try:
            info = os.fstat(fd)
            if info.st_uid != os.geteuid() or info.st_mode & 0o022:
                raise ShadowInputError("OUTPUT_DIR_NOT_PRIVATE")
            names = _entries(fd, MAX_OUTPUT_ENTRIES, "OUTPUT_DIR_ENTRY_LIMIT")
        except OSError as exc:
            raise ShadowInputError("OUTPUT_DIR_REFUSED") from exc
        for name in names:
            if name.startswith(_TEMPORARY_PREFIX):
                raise ShadowInputError("OUTPUT_STALE_TEMPORARY")
            if not _ARTIFACT_NAME.fullmatch(name):
                raise ShadowInputError("OUTPUT_DIR_FOREIGN_ENTRY")
        for name in names:
            within_deadline()
            _verify_artifact(fd, output_dir, name)
        planned = []
        for path in paths:
            within_deadline()
            planned.append(_observe(path, event_slug, require_complete, limits)["observation_id"])
        artifacts = []
        for path, observation_id in zip(paths, planned):
            within_deadline()
            # Reports are not retained across the batch; the second pass must
            # reproduce the first exactly.
            report = _observe(path, event_slug, require_complete, limits)
            if report["observation_id"] != observation_id:
                raise ShadowInputError("INPUT_CHANGED_DURING_START")
            name = artifact_name(report["source_file_sha256"], event_slug)
            created = shadow.write_artifact(Path(output_dir) / name, report, dir_fd=fd)
            artifacts.append({"artifact": name, "source_file_sha256": report["source_file_sha256"],
                              "observation_id": observation_id, "coverage": report["coverage"],
                              "created": created})
    finally:
        os.close(fd)
    return {
        "version": START_VERSION, "activation": ACTIVATION, "mode": "V11_SHADOW",
        "financial_authority": False, "qualification": False, "transaction_level_proof": False,
        "account_effects": [], "order_effects": [],
        "event_slug": event_slug, "require_complete": require_complete,
        "evidence_class_counts": {EvidenceClass.CHAIN_RECEIPT.value: 0},
        "artifacts": artifacts,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bounded offline InventoryTransform SHADOW start")
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--input", type=Path, action="append")
    selection.add_argument("--input-dir", type=Path)
    parser.add_argument("--event", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--require-complete", action="store_true")
    args = parser.parse_args(argv)
    try:
        summary = run(args.event, args.output_dir, inputs=tuple(args.input or ()),
                      input_dir=args.input_dir, require_complete=args.require_complete)
    except ShadowInputError as exc:
        parser.exit(2, f"inventory shadow start refused: {exc}\n")
    sys.stdout.write(json.dumps(summary, sort_keys=True, separators=(",", ":")) + "\n")
    return 0


def _deny_ambient_access() -> None:
    """Fail any socket/DNS or child-process attempt for the rest of the process.

    An audit hook cannot be removed, so this is installed only by the process
    entry point. It is an in-process tripwire, not an OS sandbox.
    """
    def hook(event: str, args: tuple) -> None:
        if (event.startswith("socket.") or event in _DENIED_AUDIT_EVENTS
                or (event == "import" and args and _denied_import_match(args[0]))):
            raise RuntimeError("INVENTORY_SHADOW_AMBIENT_ACCESS_DENIED:" + event)
    sys.addaudithook(hook)


def _foreign_modules() -> list[str]:
    return sorted(name for name in sys.modules
                  if name.split(".")[0] == "polymarket_scanner" and name not in _ALLOWED_PROJECT_MODULES)


def _preloaded_denied_modules() -> list[str]:
    return sorted(name for name in sys.modules if _denied_import_match(name))


def guarded_main(argv: list[str] | None = None) -> int:
    """Process entry: deny ambient access, refuse any other project module."""
    _deny_ambient_access()
    # The import hook never fires for a module loaded before it was installed.
    if _preloaded_denied_modules():
        sys.stderr.write("inventory shadow start refused: DENIED_MODULE_PRELOADED\n")
        return 2
    if _foreign_modules():
        sys.stderr.write("inventory shadow start refused: MODULE_COUPLING_REFUSED\n")
        return 2
    code = main(argv)
    if _foreign_modules():
        sys.stderr.write("inventory shadow start refused: MODULE_COUPLING_DETECTED_AFTER_RUN\n")
        return 2
    return code


if __name__ == "__main__":
    raise SystemExit(guarded_main())
