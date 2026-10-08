"""Offline, read-only PAPER R08/R09 readiness CLI.

Per `/tmp/alpha-paper-r89-static-map-20261007.report.md` sec. 3a/5: the only
concrete, genuinely fillable gap found for PAPER requirements 8 and 9 is that
`tools/v11_r08_scenario_reservation_readiness.py`'s
`evaluate_scenario_reservation_readiness` and
`tools/v11_r09_pws_lead_readiness.py`'s `evaluate_pws_lead_readiness` are
called only from their own test files -- every real readiness check against a
live retained ledger so far has been an ad-hoc script run by hand each cycle.
This module is that missing wiring: a small CLI that builds the exact typed
objects (`EvidenceStore`, `PaperCoordinator`, `PaperAccountPolicy`,
`CorrelationMap`, `ScenarioLimits`, and -- for R09 -- `EventContext`,
`RuleFingerprint`, a `ReleaseBinding`-shaped dict, and a tuple of payout
admission ids) those two already-reviewed evaluators require, from one
caller-supplied local JSON configuration file, and prints their real,
unmodified result.

It does not re-derive or relax any gate: both evaluators are imported and
called exactly as `tests/test_v11_r08_scenario_reservation_readiness.py` and
`tests/test_v11_r09_pws_lead_readiness.py` already call them. It opens no
socket, imports no HTTP/provider client, runs no subprocess, accepts no
credential, and places no order. Every argument to those evaluators is built
from the caller's own explicit JSON file; nothing is measured, guessed, or
invented here, and a missing or malformed input is refused, never
silently filled in or treated as passing.

The EvidenceStore the config file names must already exist as a regular file
before this CLI runs: `EvidenceStore.__init__` creates a brand-new, empty
ledger database if the given path does not yet exist, which is a write this
read-only checker must never trigger merely because a caller mistyped a path.
This module checks the path's existence itself, before ever constructing an
`EvidenceStore`, and refuses (`STORE_PATH_MUST_PREEXIST`) rather than letting
that constructor run.

This CLI never opens the caller's own store file for anything other than an
anchored `mode=ro` SQLite backup into a private, bounded, 0700 scratch copy
(the same technique `tools/v11_snapshot.py` already uses): every real read
-- including the identity/foreign-database/namespace checks inside
`EvidenceStore.__init__` itself -- runs only against that disposable copy,
which is deleted when this process exits the evaluation, win or lose.
`EvidenceStore._connect()` always opens its target read-write (it sets
`PRAGMA journal_mode=WAL`, may checkpoint an existing WAL, etc., even for a
pure read), so pointing it at the caller's own path even once -- on a
success path, a refusal path, or merely to inspect it -- would have changed
that file's bytes or left new `-wal`/`-shm` sidecars behind. Routing every
read through a private copy instead keeps the caller's own store file
byte- and mtime-identical on every outcome, including refusal, with two
narrow documented exceptions. First, if the caller's file already has
live, uncheckpointed `-wal`/`-shm` sidecars when this CLI runs, the
anchored snapshot's own read-only connection may update bytes *inside* the
pre-existing `-shm` file (standard SQLite reader bookkeeping; its size does
not change and no file is added, removed, or renamed) without touching the
main file or the `-wal` file. Second, if the caller's file has *no*
pre-existing `-wal`/`-shm` sidecars, this CLI's own `mode=ro` connection
causes SQLite to create an empty `-wal` and a `-shm` next to it (the same
thing `EvidenceStore.__init__`'s own `mode=ro` inspection already does),
and this CLI deliberately never removes either one: a sidecar it just
created is not necessarily one it owns alone, because SQLite identifies
`-wal`/`-shm` files by path, not by which process created them, and a
concurrent writer that attaches during this CLI's snapshot shares the very
files it created. Removing one could delete a write that writer has
already told its caller succeeded, with no error surfaced to anyone. So
this CLI's directory listing is byte-identical except that it may grow an
inert, empty `-wal`/`-shm` pair; it never shrinks and never loses a record.
A store directory that forbids creating new files (e.g. read-only media)
still cannot be read if the store has no pre-existing sidecars to reuse,
because SQLite itself cannot open a WAL-mode database for any purpose
without being able to create its wal-index; this CLI fails closed in that
case rather than guessing.

A result of `GENUINE_ZERO_AUTHORITY_SCENARIO_RESERVATION_DEMONSTRATED` or
`PWS_OBSERVED_AND_NETTED_AS_LEAD_ONLY_DEMONSTRATED` confers no execution,
order, model, promotion, or funding authority -- see the two reused modules'
own docstrings. This CLI is read-only with respect to every store it opens:
it never calls `EvidenceStore.audit`/`.safety_audit` or
`PaperCoordinator.coordinate`, and its own output is printed to stdout, never
written back into any evidence store.

This module is deliberately not installed, scheduled, or started by itself;
wiring it into an actual periodic scheduler remains a separate, later step
outside this change's scope, and still requires its own independent review.

Configuration file shape (a single local JSON object, UTF-8, <= 65536 bytes):

    {
      "store": {"path": "<absolute path to an existing EvidenceStore db>",
                "namespace": "<EvidenceStore namespace, e.g. V11_PAPER>"},
      "account_policy": {<exact PaperAccountPolicy field names/values>},
      "correlation": {"version": str, "evidence_sha256": str,
                      "memberships": [{"station": str, "city": str, "region": str,
                                       "weather_groups": [str, ...], "source_groups": [str, ...],
                                       "model_groups": [str, ...], "metadata_fingerprint": str}, ...]},
      "scenario_limits": {<exact ScenarioLimits field names/values>},
      "r09": null
             | {"preconfirmation_id": str,
                "context": {<exact EventContext field names/values>},
                "rule": {<exact RuleFingerprint field names/values>},
                "binding": {<exact ReleaseBinding field names/values>},
                "payout_admission_ids": [str, ...]}
    }

`r09` may be omitted or `null`: requirement 9 is then reported as
`R09_NOT_REQUESTED`, never silently evaluated with invented arguments.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import stat
import sys
import tempfile
import time
from contextlib import closing, contextmanager
from dataclasses import asdict, fields
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from polymarket_scanner.v11.evidence import EvidenceError, EvidenceStore, ReleaseBinding, canonical
from polymarket_scanner.v11.event_risk import EventContext
from polymarket_scanner.v11.model_artifacts import parse_data
from polymarket_scanner.v11.paper_coordinator import PaperAccountPolicy, PaperCoordinator
from polymarket_scanner.v11.rules import RuleFingerprint
from polymarket_scanner.v11.scenario_risk import CorrelationMap, ScenarioLimits, StationMembership
from tools.v11_r08_scenario_reservation_readiness import evaluate_scenario_reservation_readiness
from tools.v11_r09_pws_lead_readiness import evaluate_pws_lead_readiness

SCHEMA = "PAPER_R08_R09_READINESS_CLI_V1"
MAX_CONFIG_BYTES = 65536
MAX_STORE_SNAPSHOT_BYTES = 256 * 1024 * 1024
STORE_SNAPSHOT_MINIMUM_FREE_BYTES = 64 * 1024 * 1024
STORE_SNAPSHOT_DEADLINE_SECONDS = 30.0
TOP_LEVEL_REQUIRED = frozenset({"store", "account_policy", "correlation", "scenario_limits"})
TOP_LEVEL_ALL = TOP_LEVEL_REQUIRED | {"r09"}
_MEMBERSHIP_KEYS = frozenset({
    "station", "city", "region", "weather_groups", "source_groups", "model_groups", "metadata_fingerprint",
})
_GROUP_FIELDS = ("weather_groups", "source_groups", "model_groups")
_CORRELATION_KEYS = frozenset({"version", "evidence_sha256", "memberships"})
_STORE_KEYS = frozenset({"path", "namespace"})
_R09_KEYS = frozenset({"preconfirmation_id", "context", "rule", "binding", "payout_admission_ids"})
_FAIL_CLOSED_ERRORS = (EvidenceError, sqlite3.Error, OSError)


def _closed(value: object, keys: frozenset, label: str) -> dict:
    if type(value) is not dict or set(value) != keys:
        raise EvidenceError(f"{label}_SCHEMA")
    return value


def _typed(cls: type, value: object, label: str):
    _closed(value, frozenset(f.name for f in fields(cls)), label)
    return cls(**value)


def _build_membership(value: object) -> StationMembership:
    _closed(value, _MEMBERSHIP_KEYS, "CORRELATION_MEMBERSHIP")
    groups = {}
    for key in _GROUP_FIELDS:
        raw = value[key]
        if type(raw) is not list or not all(type(item) is str for item in raw):
            raise EvidenceError("CORRELATION_MEMBERSHIP_GROUPS_TYPE")
        groups[key] = tuple(raw)
    return StationMembership(station=value["station"], city=value["city"], region=value["region"],
                             metadata_fingerprint=value["metadata_fingerprint"], **groups)


def _build_correlation(value: object) -> CorrelationMap:
    _closed(value, _CORRELATION_KEYS, "CORRELATION")
    memberships = value["memberships"]
    if type(memberships) is not list:
        raise EvidenceError("CORRELATION_MEMBERSHIPS_TYPE")
    return CorrelationMap(version=value["version"], evidence_sha256=value["evidence_sha256"],
                          memberships=tuple(_build_membership(m) for m in memberships))


def _check_source_identity(path: Path) -> None:
    """Reject an unsafe or absent source path without ever opening it as a
    database. Mirrors `EvidenceStore.__init__`'s own path-safety checks (same
    outcomes, same order) so callers see identical refusal reasons, but using
    only `stat`/`lstat`, which cannot mutate the file being inspected.
    """
    # Checked before any database connection is ever opened against this
    # path: EvidenceStore's constructor creates a brand-new, empty ledger
    # file when the path does not yet exist, which this read-only checker
    # must never trigger (see module docstring).
    if not path.exists() or not path.is_file():
        raise EvidenceError("STORE_PATH_MUST_PREEXIST")
    if ".." in path.parts:
        raise EvidenceError("ABSOLUTE_PATH_REQUIRED")
    for p in (path, *path.parents):
        if p.is_symlink():
            raise EvidenceError("SYMLINK_PATH_REFUSED")
    if not path.parent.is_dir() or path.parent.stat().st_mode & 0o077:
        raise EvidenceError("PRIVATE_PARENT_REQUIRED")
    if path.stat().st_mode & 0o077:
        raise EvidenceError("PRIVATE_REGULAR_DATABASE_REQUIRED")


@contextmanager
def _anchored_snapshot(path: Path):
    """Yield a private, disposable copy of ``path`` instead of ever handing
    the real evaluators the caller's own file.

    `EvidenceStore._connect()` always opens read-write (WAL pragma, possible
    checkpoint) even for a pure read, so the only way to guarantee the
    caller's own store is byte-identical afterwards -- on success *and* on
    refusal -- is to never let any evaluator touch it. This pins a
    consistent read with a `mode=ro` connection and a held transaction,
    copies it via the SQLite backup API (as `tools/v11_snapshot.py` already
    does for V10 control snapshots) into a fresh 0700 temporary directory,
    and removes that directory again on every exit path.

    This never unlinks a `-wal`/`-shm` sidecar next to the caller's file,
    including one this inspection's own `mode=ro` connection causes SQLite
    to create when none already exist. SQLite identifies those files by
    path, not by which process created them, so a concurrent writer that
    attaches during this snapshot shares exactly the sidecars this
    inspection just created; removing one afterwards could delete a write
    that writer has already been told succeeded, with the loss surfaced to
    no one. The only accepted side effect on a store that had no sidecars
    at all is therefore that it may keep an empty `-wal` and a `-shm` after
    this call returns, the same residue `EvidenceStore.__init__`'s own
    `mode=ro` inspection already leaves.

    The identity anchor uses `lstat`, not `stat`, and requires a regular
    file both before and after the backup: `_check_source_identity` already
    refuses a symlinked path, but `stat()`/`sqlite3.connect()` both follow
    symlinks, so a symlink swapped in during the narrow window after that
    check runs would otherwise go undetected. `lstat` sees the symlink
    itself rather than its target. A swap completed entirely within that
    window (i.e. restored before this function's own `lstat` calls run) is
    not detectable this way; that residual requires write access to the
    0700 private parent directory, which is owner-equivalent access.
    """
    _check_source_identity(path)
    before_stat = os.lstat(path)
    if not stat.S_ISREG(before_stat.st_mode):
        raise EvidenceError("STORE_PATH_REPLACED_DURING_CAPTURE")
    scratch = Path(tempfile.mkdtemp(prefix=".v11-paper-readiness-snapshot-"))
    os.chmod(scratch, 0o700)
    try:
        copy_path = scratch / "store.sqlite"
        deadline = time.monotonic() + STORE_SNAPSHOT_DEADLINE_SECONDS
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=1.0)) as src:
            src.execute("PRAGMA query_only=ON")
            src.execute("BEGIN")
            src.execute("SELECT COUNT(*) FROM sqlite_schema").fetchone()
            page_count = src.execute("PRAGMA page_count").fetchone()[0]
            page_size = src.execute("PRAGMA page_size").fetchone()[0]
            if page_count * page_size > MAX_STORE_SNAPSHOT_BYTES:
                raise EvidenceError("STORE_SNAPSHOT_BYTES_LIMIT")
            if shutil.disk_usage(scratch).free < page_count * page_size + STORE_SNAPSHOT_MINIMUM_FREE_BYTES:
                raise EvidenceError("STORE_SNAPSHOT_DISK_HEADROOM")

            def progress(status: int, remaining: int, total: int) -> None:
                if time.monotonic() > deadline:
                    raise EvidenceError("STORE_SNAPSHOT_DEADLINE")
                if total * page_size > MAX_STORE_SNAPSHOT_BYTES:
                    raise EvidenceError("STORE_SNAPSHOT_BYTES_LIMIT")

            fd = os.open(copy_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(fd)
            with closing(sqlite3.connect(copy_path, timeout=1.0)) as dst:
                src.backup(dst, pages=64, progress=progress, sleep=0.01)
            src.rollback()
        after_stat = os.lstat(path)
        if not stat.S_ISREG(after_stat.st_mode) or (
            (before_stat.st_dev, before_stat.st_ino) != (after_stat.st_dev, after_stat.st_ino)
        ):
            raise EvidenceError("STORE_PATH_REPLACED_DURING_CAPTURE")
        os.chmod(copy_path, 0o600)
        yield copy_path
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


@contextmanager
def _build_store(value: object, *, clock):
    _closed(value, _STORE_KEYS, "STORE")
    path_value, namespace = value["path"], value["namespace"]
    if type(path_value) is not str or not path_value:
        raise EvidenceError("STORE_PATH_TYPE")
    if type(namespace) is not str or not namespace:
        raise EvidenceError("STORE_NAMESPACE_TYPE")
    path = Path(path_value)
    if not path.is_absolute():
        raise EvidenceError("STORE_PATH_MUST_BE_ABSOLUTE")
    with _anchored_snapshot(path) as snapshot_path:
        yield EvidenceStore(snapshot_path, namespace, clock=clock)


def _build_binding(value: object) -> dict:
    return asdict(_typed(ReleaseBinding, value, "R09_BINDING"))


def _build_payout_admission_ids(value: object) -> tuple:
    if type(value) is not list or not value or any(type(item) is not str for item in value):
        raise EvidenceError("R09_PAYOUT_ADMISSION_IDS_TYPE")
    return tuple(value)


def _build_r09(value: object) -> dict:
    _closed(value, _R09_KEYS, "R09")
    preconfirmation_id = value["preconfirmation_id"]
    if type(preconfirmation_id) is not str or not preconfirmation_id:
        raise EvidenceError("R09_PRECONFIRMATION_ID_TYPE")
    return dict(
        preconfirmation_id=preconfirmation_id,
        context=_typed(EventContext, value["context"], "R09_CONTEXT"),
        rule=_typed(RuleFingerprint, value["rule"], "R09_RULE"),
        binding=_build_binding(value["binding"]),
        payout_admission_ids=_build_payout_admission_ids(value["payout_admission_ids"]),
    )


def _load_config(config_path: Path) -> dict:
    if not config_path.is_file() or config_path.is_symlink():
        raise EvidenceError("CONFIG_FILE_MISSING")
    if config_path.stat().st_size > MAX_CONFIG_BYTES:
        raise EvidenceError("CONFIG_BYTES_BOUND")
    return parse_data(config_path.read_bytes(), max_bytes=MAX_CONFIG_BYTES)


def evaluate(config: dict, *, clock=time.time) -> dict:
    """Build the reviewed evaluators' real inputs from ``config`` and run them.

    Grants no execution, order, model, or financial authority. Every typed
    object is constructed from ``config`` alone; nothing here is measured or
    invented. A result's R08/R09 outcome is whatever the reused, unmodified
    evaluator actually computed against the caller's own evidence store.
    """
    if type(config) is not dict or not TOP_LEVEL_REQUIRED <= set(config) <= TOP_LEVEL_ALL:
        raise EvidenceError("CONFIG_TOP_LEVEL_SCHEMA")
    with _build_store(config["store"], clock=clock) as store:
        policy = _typed(PaperAccountPolicy, config["account_policy"], "ACCOUNT_POLICY")
        correlation = _build_correlation(config["correlation"])
        limits = _typed(ScenarioLimits, config["scenario_limits"], "SCENARIO_LIMITS")
        coordinator = PaperCoordinator(store, policy=policy, correlation=correlation, limits=limits)

        r08 = evaluate_scenario_reservation_readiness(coordinator)

        r09_config = config.get("r09")
        if r09_config is None:
            r09_status, r09 = "R09_NOT_REQUESTED", None
        else:
            built = _build_r09(r09_config)
            r09_status, r09 = "R09_EVALUATED", evaluate_pws_lead_readiness(coordinator, **built)

        result = {
            "schema": SCHEMA,
            "generated_at": store.clock(),
            "financial_authority": False,
            "r08": r08.to_dict(),
            "r09_status": r09_status,
            "r09": r09.to_dict() if r09 is not None else None,
        }
    return result


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(description=(
        "Offline, read-only PAPER R08/R09 readiness CLI. Reuses the reviewed "
        "tools.v11_r08_scenario_reservation_readiness / tools.v11_r09_pws_lead_readiness "
        "evaluators against one explicitly supplied, already-existing local EvidenceStore "
        "and account/policy configuration file. Grants no execution, order, or financial "
        "authority; makes no network or provider call; never creates a new evidence store."))
    parser.add_argument("config", type=Path, help="Local JSON configuration file (see module docstring).")
    args = parser.parse_args(argv)
    try:
        result = evaluate(_load_config(args.config))
    except _FAIL_CLOSED_ERRORS as exc:
        print(canonical({"schema": SCHEMA, "outcome": "READINESS_CLI_REFUSED", "reason": str(exc)}),
              file=sys.stderr)
        return 1
    print(canonical(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
