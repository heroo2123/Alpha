"""Offline source inventory, not evidence of exclusive runtime writer custody.

Review a changed snapshot before updating the fixture.  The tracked-file boundary,
source hashes, and deliberately broad SQLite census make this a change detector,
not a proof that all possible Python or host writers have been found.  It does
not resolve dynamic imports, computed path aliases, arbitrary file mutations in
noncritical modules without protected literals, or installed/generated code.
"""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).with_name("v11_forward_writer_inventory.json")

# These files carry the reviewed ledger and protected-path operations.  Hashing
# their entire source also guards assignments and helper calls feeding the sinks.
CRITICAL = {
    "polymarket_scanner/v11/evidence.py": "ledger API: create, WAL, append",
    "tools/v11_daily_evidence_rollover.py": "ledger rotation: copy, checkpoint, sidecars, replace",
    "tools/v11_continuous_day_manager.py": "ledger RW read and rotation caller",
    "tools/v11_perpetual_brain_preparer.py": "ledger RO source and copied-ledger writer",
    "tools/v11_brain_forward_score.py": "copied-ledger EvidenceStore",
    "polymarket_scanner/v11/shadow_commission.py": "caller-selected EvidenceStore",
    "polymarket_scanner/v11/paper_guardian.py": "config-selected EvidenceStore",
    "polymarket_scanner/v11/model_artifacts.py": "generic artifact object writer",
    "host_trust/v11-model-authority/authority.py": "protected model-state replacement",
    "tools/v11_daily_review_authority.py": "protected review-manifest replacement",
    "tools/v11_daily_review_rollforward_publisher.py": "protected rollforward-manifest replacement",
    "host_trust/v11-daily-review-authority/authority.py": "third, repository-only manifest publisher",
    "tools/v11_daily_review_tick.sh": "protected status redirection and mv; invokes publisher",
}

# These are known raw SQLite/store families outside the forward evidence ledger.
# Their call signatures are still frozen in the fixture, so an operation change
# is not silently excused by this classification.
UNRELATED = {
    "polymarket_scanner/v11/microcanary_prep.py": "separate canary journal",
    "tools/v11_microcanary_adversarial_probe.py": "foreign temporary canary probe",
    "tools/v11_snapshot.py": "V10 control snapshot",
    "tools/v11_ecmwf_historical_backfill.py": "weather backfill database",
}
READERS = {
    "polymarket_scanner/v11/learning_sources.py": "ledger raw RO reader",
    "tools/v11_brain_label_attestation.py": "ledger raw RO reader",
    "tools/v11_multimodel_panel.py": "ledger raw RO immutable reader",
    "tools/v11_r09_gate3_message_sizes.py": "arbitrary input raw RO reader",
}

SINKS = {"open", "write_text", "write_bytes", "unlink", "rename", "replace",
         "backup", "execute", "executemany", "executescript", "commit", "move",
         "copy", "copy2", "copyfile", "rmtree", "mkstemp", "NamedTemporaryFile",
         "link", "remove", "chmod"}
MARKER = re.compile(r"/etc/alpha-v11|/var/lib/alpha-v11|/var/lock/alpha-v11|"
                    r"daily-[^\s]*\.sqlite|source\.sqlite|v11_records|v11_meta|"
                    r"-wal|-shm|-journal|wal_checkpoint")


def _tracked_sources() -> list[str]:
    found = subprocess.check_output(["git", "ls-files", "-z", "--", "*.py", "*.sh"], cwd=ROOT)
    return sorted(p.decode() for p in found.split(b"\0") if p and not p.startswith(b"tests/"))


def _call_name(node: ast.expr, aliases: dict[str, str]) -> str:
    if isinstance(node, ast.Name):
        return aliases.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        return _call_name(node.value, aliases) + "." + node.attr
    return ast.unparse(node)


def _scan_py(path: str, source: str) -> tuple[list[dict], list[str]]:
    tree = ast.parse(source, filename=path)
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "sqlite3":
                    aliases[alias.asname or alias.name] = "sqlite3"
        elif isinstance(node, ast.ImportFrom) and node.module in {"sqlite3", "polymarket_scanner.v11.evidence", "polymarket_scanner.v11.model_artifacts"}:
            for alias in node.names:
                aliases[alias.asname or alias.name] = alias.name if node.module != "sqlite3" else "sqlite3." + alias.name

    calls: list[dict] = []

    class Visitor(ast.NodeVisitor):
        def __init__(self):
            self.scope: list[str] = []

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self.scope.append(node.name)
            self.generic_visit(node)
            self.scope.pop()

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            self.scope.append(node.name)
            self.generic_visit(node)
            self.scope.pop()

        def visit_Call(self, node: ast.Call) -> None:
            name = _call_name(node.func, aliases)
            tail = name.rsplit(".", 1)[-1]
            if name == "sqlite3.connect":
                operation = "sqlite.connect"
            elif tail in {"EvidenceStore", "ArtifactStore"}:
                operation = "store." + tail
            elif path in CRITICAL and tail in SINKS:
                operation = "critical." + tail
            else:
                operation = None
            if operation:
                args = [ast.unparse(arg) for arg in node.args]
                kwargs = {kw.arg or "**": ast.unparse(kw.value) for kw in node.keywords}
                target = args[0] if args else kwargs.get("database", "")
                mode = "ro" if "mode=ro" in target and kwargs.get("uri") == "True" else "rw-or-unknown"
                if operation == "sqlite.connect" and "mode=ro" in target and mode != "ro":
                    mode = "ro-uri-unconfirmed"
                calls.append({"path": path, "scope": ".".join(self.scope) or "<module>",
                              "operation": operation, "target": target, "mode": mode if operation == "sqlite.connect" else "n/a",
                              "args": args, "kwargs": kwargs,
                              "class": UNRELATED.get(path, READERS.get(path, CRITICAL.get(path, "other tracked source")))})
            self.generic_visit(node)

    Visitor().visit(tree)
    markers = sorted({node.value for node in ast.walk(tree)
                      if isinstance(node, ast.Constant) and isinstance(node.value, str)
                      and MARKER.search(node.value)})
    return calls, markers


def inventory(overrides: dict[str, str] | None = None) -> dict:
    overrides = overrides or {}
    paths = sorted(set(_tracked_sources()) | set(overrides))
    calls: list[dict] = []
    markers: dict[str, list[str]] = {}
    hashes: dict[str, dict[str, str]] = {}
    for path in paths:
        source = overrides.get(path)
        if source is None:
            source = (ROOT / path).read_text(encoding="utf-8")
        if path in CRITICAL:
            hashes[path] = {"class": CRITICAL[path], "sha256": hashlib.sha256(source.encode()).hexdigest()}
        if path.endswith(".py"):
            found, literals = _scan_py(path, source)
            calls.extend(found)
            if literals:
                markers[path] = literals
        elif path.endswith(".sh") and (path in CRITICAL or MARKER.search(source)):
            markers[path] = [line.strip() for line in source.splitlines()
                             if line.strip() and (MARKER.search(line) or "mv -f" in line or ">" in line)]
            if not markers[path]:
                del markers[path]
    calls.sort(key=lambda x: json.dumps(x, sort_keys=True))
    return {"calls": calls, "markers": markers, "critical_source": hashes,
            "non_repository_writers": [
                "root cron and installed tick/publishers: deployment identity not checked",
                "manual owner approval/object/policy/state installs and backup/restore",
                "out-of-repo observer rotation, perpetual preparer, generated modules, capture/label scripts",
                "same-uid or root processes, sqlite3 CLI, and other external file writers",
            ]}


def _changed(expected: dict, actual: dict) -> str:
    if expected == actual:
        return ""
    messages = []
    for key in ("calls", "markers", "critical_source", "non_repository_writers"):
        if expected.get(key) != actual.get(key):
            messages.append(key)
    return "writer inventory changed: " + ", ".join(messages)


def test_repository_writer_inventory() -> None:
    expected = json.loads(FIXTURE.read_text(encoding="utf-8"))
    changed = _changed(expected, inventory())
    if changed:
        raise AssertionError(changed + "; review operations, paths, URI modes and the fixture")


def test_synthetic_writer_changes_are_refused() -> None:
    expected = json.loads(FIXTURE.read_text(encoding="utf-8"))
    cases = {
        "new sqlite writer": {"tools/v11_unlisted_writer_probe.py": "import sqlite3\ndef write(daily_db):\n    return sqlite3.connect(daily_db)\n"},
        "read-only to read-write": {"polymarket_scanner/v11/evidence.py":
            (ROOT / "polymarket_scanner/v11/evidence.py").read_text().replace('self.path.as_uri() + "?mode=ro", uri=True', 'self.path')},
        "protected manifest replacement": {"tools/v11_daily_review_authority.py":
            (ROOT / "tools/v11_daily_review_authority.py").read_text() + "\ndef unlisted_manifest(temp, manifest):\n    os.replace(temp, manifest)\n"},
        "WAL sidecar mutation": {"tools/v11_daily_evidence_rollover.py":
            (ROOT / "tools/v11_daily_evidence_rollover.py").read_text() + "\ndef unlisted_wal(source):\n    Path(str(source) + '-wal').unlink()\n"},
    }
    expected_changes = {
        "new sqlite writer": ("sqlite.connect", "tools/v11_unlisted_writer_probe.py"),
        "read-only to read-write": ("sqlite.connect", "polymarket_scanner/v11/evidence.py"),
        "protected manifest replacement": ("critical.replace", "tools/v11_daily_review_authority.py"),
        "WAL sidecar mutation": ("critical.unlink", "tools/v11_daily_evidence_rollover.py"),
    }
    for label, overrides in cases.items():
        actual = inventory(overrides)
        changed = _changed(expected, actual)
        operation, path = expected_changes[label]
        new = [entry for entry in actual["calls"] if entry not in expected["calls"]]
        if not changed or not any(entry["operation"] == operation and entry["path"] == path for entry in new):
            raise AssertionError(label + " escaped the operation inventory")
        if label == "read-only to read-write" and not any(
                entry["mode"] == "rw-or-unknown" and entry["target"] == "self.path" for entry in new):
            raise AssertionError("read-only to read-write did not change the recorded mode")
