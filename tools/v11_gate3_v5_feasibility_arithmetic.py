"""Public, offline arithmetic only; no project imports, transport or admission.

Run with --check for strict current-source verification, or --replay-baseline
to reproduce the reviewed report from its fixed historical Git commit/tree.
The reviewed companion is read-only in both modes.
Source expressions are interpreted with a tiny arithmetic AST evaluator, never
executed/imported. Checks use exceptions so optimized Python preserves them.
"""
import argparse
import ast
import hashlib
import json
import operator
import os
from pathlib import Path
import selectors
import signal
import stat
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "docs/V11_GATE3_V5_FEASIBILITY_20261003.arithmetic.json"
BASELINE = "e73fb9b66c0cf2591c0b8017925c764aac6cff1e"
BASELINE_TREE = "8afe7a24c6e437b0b6345a1fadc08e136797a2c1"
SOURCE_PATHS = (
    "docs/V11_WORK_CHECKPOINT.md",
    "docs/V11_REQUIREMENTS_MATRIX.md",
    "docs/V11_ENGINEERING_PROGRESS.md",
    "docs/V11_GATE3_V5_EVIDENCE_ROLE_CONTRACT_20261003.md",
    "docs/V11_GATE3_V5_EVIDENCE_ROLE_CONTRACT_20261003.sources.json",
    "docs/V11_GATE3_V5_EVIDENCE_ROLE_CONTRACT_20261003.verification.json",
    "docs/V11_R09_GATE3_COLLECTION_PROTOCOL.md",
    "docs/V11_R09_GATE3_LAUNCH_CONTRACT_ADJUDICATION.md",
    "docs/V11_GATE3_HOST_RESOURCE_FEASIBILITY_20261003.md",
    "docs/V11_GATE3_RESOURCE_RESERVATION_DESIGN_20261003.md",
    "docs/V11_BRAIN_IFS_AIFS_NATIVE_ADAPTER_FINDINGS_20261003.md",
    "config/v11/r09_gate3_observed_message_sizes_20260930.json",
    "tools/v11_r09_gate3_launch.py",
    "tools/v11_r09_gate3_launch_v4.py",
    "tools/v11_r09_gate3_runtime.py",
    "tools/v11_r09_gate3_store_v1.py",
    "tools/v11_gate3_offline_resource_budget.py",
    "tools/v11_r09_gate3_a7_decoder.py",
    "tools/v11_r09_gate3_collector.py",
    "polymarket_scanner/v11/ecmwf_sources.py",
    "polymarket_scanner/v11/ecmwf_grib.py",
    "polymarket_scanner/v11/gefs_sources.py",
)
MIB = 1024**2
GIB = 1024**3
OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
       ast.Pow: operator.pow, ast.FloorDiv: operator.floordiv}
OBJECT_LIMIT = 8 * MIB
REPORT_LIMIT = MIB
GIT_TIMEOUT = 5.0


def arithmetic(node, env):
    if isinstance(node, ast.Constant) and type(node.value) in (int, str):
        return node.value
    if isinstance(node, ast.Name):
        return env[node.id]
    if isinstance(node, ast.BinOp) and type(node.op) in OPS:
        return OPS[type(node.op)](arithmetic(node.left, env), arithmetic(node.right, env))
    if isinstance(node, ast.Subscript):
        return arithmetic(node.value, env)[arithmetic(node.slice, env)]
    if isinstance(node, ast.Dict):
        return {arithmetic(k, env): arithmetic(v, env) for k, v in zip(node.keys, node.values)}
    if isinstance(node, ast.Tuple):
        return tuple(arithmetic(x, env) for x in node.elts)
    raise ValueError("unsupported source arithmetic: " + ast.dump(node))


def assignment(tree, name):
    matches = [node.value for node in ast.walk(tree) if isinstance(node, ast.Assign)
               and any(isinstance(t, ast.Name) and t.id == name for t in node.targets)]
    if len(matches) != 1:
        raise ValueError("assignment must be unique: " + name)
    return matches[0]


def aggregates(n):
    total = 0
    while n > 1:
        n = (n + 255) // 256
        total += n
    return total


def objects(n):
    return 2 * n + aggregates(n)


def serial(n, fields, deadline=1, spacing=2):
    return n * deadline + (n - 1) * spacing + max(60, fields) + 60


def git_object(kind, oid):
    """Read an object by full ID and independently verify its raw Git digest.

    Git environment overrides are discarded. Replacement refs and grafted
    ancestry cannot supply a different object under the requested identity.
    """
    if kind not in ("commit", "tree", "blob") or len(oid) != 40 or any(
            c not in "0123456789abcdef" for c in oid):
        raise ValueError("invalid Git object request")
    env = {"PATH": os.defpath, "LC_ALL": "C", "GIT_NO_REPLACE_OBJECTS": "1",
           "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
           "GIT_NO_LAZY_FETCH": "1", "GIT_ALLOW_PROTOCOL": ""}
    command = ["git", "-c", "protocol.allow=never", "-C", str(ROOT),
               "cat-file", kind, oid]
    proc = subprocess.Popen(command, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, start_new_session=True)
    output = bytearray()
    errors = bytearray()
    deadline = time.monotonic() + GIT_TIMEOUT
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(proc.stdout, selectors.EVENT_READ, output)
            selector.register(proc.stderr, selectors.EVENT_READ, errors)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ValueError(f"baseline {kind} object read timed out: {oid}")
                if not (ready := selector.select(remaining)):
                    raise ValueError(f"baseline {kind} object read timed out: {oid}")
                for key, _ in ready:
                    chunk = os.read(key.fd, 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    key.data.extend(chunk)
                    if len(key.data) > OBJECT_LIMIT:
                        raise ValueError(f"baseline {kind} object output too large: {oid}")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ValueError(f"baseline {kind} object read timed out: {oid}")
        try:
            proc.wait(timeout=remaining)
        except subprocess.TimeoutExpired as exc:
            raise ValueError(f"baseline {kind} object read timed out: {oid}") from exc
    except BaseException:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait()
        raise
    finally:
        proc.stdout.close()
        proc.stderr.close()
    if proc.returncode:
        raise ValueError(f"missing or unreadable baseline {kind} object: {oid}")
    data = bytes(output)
    digest = hashlib.sha1(f"{kind} {len(data)}\0".encode() + data).hexdigest()
    if digest != oid:
        raise ValueError(f"baseline {kind} object hash mismatch: {oid}")
    return data


def tree_entries(data):
    entries = {}
    offset = 0
    while offset < len(data):
        space = data.find(b" ", offset)
        nul = data.find(b"\0", space + 1)
        if space < 0 or nul < 0 or nul + 21 > len(data):
            raise ValueError("malformed baseline tree")
        mode = data[offset:space]
        name = data[space + 1:nul]
        if not name or b"/" in name or name in (b".", b"..") or name in entries:
            raise ValueError("invalid baseline tree entry")
        entries[name] = (mode, data[nul + 1:nul + 21].hex())
        offset = nul + 21
    return entries


def baseline_sources():
    commit = git_object("commit", BASELINE)
    header = commit.split(b"\n\n", 1)[0]
    trees = [line[5:].decode("ascii") for line in header.split(b"\n")
             if line.startswith(b"tree ")]
    if trees != [BASELINE_TREE]:
        raise ValueError("declared baseline commit/tree mismatch")
    cache = {}

    def entries(oid):
        if oid not in cache:
            cache[oid] = tree_entries(git_object("tree", oid))
        return cache[oid]

    raw = {}
    for path in SOURCE_PATHS:
        parts = path.encode("ascii").split(b"/")
        tree = BASELINE_TREE
        for part in parts[:-1]:
            mode, tree = entries(tree)[part]
            if mode != b"40000":
                raise ValueError(f"non-tree baseline path: {path}")
        mode, blob = entries(tree)[parts[-1]]
        if mode not in (b"100644", b"100755"):
            raise ValueError(f"non-regular baseline source: {path}")
        raw[path] = git_object("blob", blob)
    return raw


def build(raw=None):
    if raw is None:
        raw = current_sources(baseline_sources())
    trees = {p: ast.parse(b) for p, b in raw.items() if p.endswith(".py")}
    launch = trees["tools/v11_r09_gate3_launch.py"]
    v4 = trees["tools/v11_r09_gate3_launch_v4.py"]
    runtime = trees["tools/v11_r09_gate3_runtime.py"]
    capacity = next(n for n in ast.walk(runtime)
                    if isinstance(n, ast.ClassDef) and n.name == "CapacityPlan")
    slots = arithmetic(assignment(launch, "SLOTS"), {})
    caps = arithmetic(assignment(launch, "FIELD_LIMITS"), {})
    counts = {p: members * len(range(0, 73, cadence))
              for p, (members, cadence) in slots.items()}
    n = sum(counts.values())
    body = sum(counts[p] * caps[p] for p in counts)
    checks = []

    def equal(name, actual, expected):
        if actual != expected:
            raise ValueError(f"{name}: {actual!r} != {expected!r}")
        checks.append(name)

    equal("native provider counts", counts, {"GEFS": 775, "IFS": 1275, "AIFS": 663})
    equal("source denominator", n, arithmetic(assignment(launch, "SLOT_COUNT"), {}))
    equal("receipt ceiling", arithmetic(assignment(launch, "MAX_BYTES"), {}), GIB)
    equal("full FIELD cap sum", body, 9753853952)
    equal("fanout boundaries", [aggregates(x) for x in (1, 255, 256, 257, 2713)],
          [0, 1, 1, 3, 12])
    equal("object limit exact boundary", [objects(2043), objects(2044)], [4095, 4097])
    equal("store event boundary", [4 * x + 1 for x in (2499, 2500)], [9997, 10001])
    equal("time boundary", [serial(x, x) for x in (2685, 2686)], [10798, 10802])
    equal("full minimum time", serial(n, n), 10910)
    equal("overhead increases minimum time", serial(n + 1, n) - serial(n, n), 3)
    equal("full deadline-30 time", serial(n, n, 30), 89587)
    equal("generic IFS omitted slots", counts["IFS"] - 51 * len(range(0, 73, 6)), 612)
    equal("role cells", 3 * n, 8139)
    equal("two-event links", 2 * n, 5426)
    equal("selected one-byte caps still fail time", serial(n, n) > 10800, True)
    equal("selected one-byte caps still fail objects", objects(n) > 4096, True)
    equal("byte exact boundary and plus one", [GIB <= GIB, GIB + 1 <= GIB], [True, False])
    equal("all-max byte reservation refuses", body <= GIB, False)
    equal("max-only GEFS exceeds receipt cap", counts["GEFS"] * caps["GEFS"] > GIB, True)
    equal("minimum source serial expression", arithmetic(assignment(v4, "serial_seconds"), {
        "count": n, "limits": {"request_deadline_seconds": 1, "min_start_interval_seconds": 2},
        "schedule": {"processing_seconds": n, "finalization_seconds": 60}}), serial(n, n))
    equal("source object expression", arithmetic(assignment(v4, "expected_objects"), {
        "count": n, "aggregates": aggregates(n)}), objects(n))
    history = json.loads(raw["config/v11/r09_gate3_observed_message_sizes_20260930.json"])
    historical = sum(counts[p] * history["provider_message_size_estimate_bytes"][p] for p in counts)
    equal("historical estimate, not future bound", historical, 1469234173)
    q_v4_without_decoded = 4 * 64 * MIB + 16 * MIB + objects(n) * 4 * MIB + body
    equal("source V4 quota expression", arithmetic(assignment(v4, "prospective_bytes"), {
        "JOURNAL_MAX_BYTES": 64 * MIB, "storage": {"report_reserve_bytes": 16 * MIB},
        "expected_objects": objects(n), "STORE_OBJECT_MAX_BYTES": 4 * MIB,
        "limits": {"decoded": 1}, "total": body}), q_v4_without_decoded + 1)
    runtime_cases = []
    for events in (1, 2):
        a = events * aggregates(n)
        records = 35 * n
        store_events = 3 * (n + a)
        disk = 2 * body + 2 * a * 4 * MIB + (records + store_events + 24 * n + 4) * 65536 + 16 * MIB + 4096
        env = {"n": n, "chunks": 32 * n, "aggregate_nodes": a, "body": body,
               "records": records, "store_events": store_events, "REPORT_RESERVE_BYTES": 16 * MIB}
        for name, expected in (("records", records), ("store_events", store_events), ("disk", disk)):
            equal(f"runtime source {name}, {events} events", arithmetic(assignment(capacity, name), env), expected)
        runtime_cases.append({"cohort_events": events, "aggregate_nodes": a,
                              "store_events": store_events, "disk_bytes": disk})
    equal("budget journal fresh boundary", [35 * x * 65536 <= 64 * MIB for x in (29, 30)], [True, False])
    equal("even one-byte reservation journal failure", 4 * n * 65536 > 64 * MIB, True)
    equal("session event ceiling fails", 20 * n + 1 > 32768, True)
    return {
        "schema": "G3_V5_FEASIBILITY_ARITHMETIC_1", "baseline_commit": BASELINE,
        "baseline_tree": BASELINE_TREE,
        "verdict": "FULL_COHORT_BLOCKED_UNDER_RETAINED_RESERVATIONS",
        "authority": False, "qualification_credit": 0,
        "source_pins": [{"path": p, "sha256": hashlib.sha256(b).hexdigest(), "byte_length": len(b)}
                        for p, b in raw.items()],
        "results": {
            "provider_slots": counts, "mandatory_slots": n, "request_headroom": 3600 - n,
            "provider_FIELD_reservations": {p: counts[p] * caps[p] for p in counts},
            "FIELD_reservation_bytes": body, "receipt_deficit_bytes": body - GIB,
            "common_integer_FIELD_cap_max_without_overhead": GIB // n,
            "historical_raw_estimate_bytes_NOT_future_bound": historical,
            "minimum_inherited_seconds": serial(n, n), "deadline_30_seconds": serial(n, n, 30),
            "V4_aggregates": aggregates(n), "V4_objects": objects(n), "V4_store_events": 4 * n + 1,
            "V4_session_events": 8 * n + 1, "V4_budget_events": 34 * n + 1,
            "V4_quota_bytes_excluding_decoded": q_v4_without_decoded,
            "runtime_cases": runtime_cases, "runtime_RAW_memory_bytes": 2 * max(caps.values()) + 4 * 65536,
            "runtime_budget_records": 35 * n, "runtime_budget_journal_bytes": 35 * n * 65536,
            "runtime_session_events": 20 * n + 1, "runtime_session_journal_bytes": (20 * n + 1) * 65536,
            "runtime_denial_events": 4 * n, "runtime_denial_journal_bytes": 4 * n * 65536,
            "minimum_one_byte_budget_journal_bytes": 4 * n * 65536,
            "role_cells": 3 * n, "two_event_links": 2 * n,
            "external_edge_remaining_if_three_distinct_direct_proofs_per_FIELD": 8192 - 3 * n,
            "generic_six_hour_IFS_missing_fields": 612,
            "A7_default_ECMWF_wall_envelope_seconds": (counts["IFS"] + counts["AIFS"]) * 20,
            "unknown": ["current native field sizes", "qualified decode bounds", "proof graph closure cost",
                        "host reservation and foreign consumption", "provider rights and authenticated custody"]},
        "checks_passed": checks,
    }


def read_regular_bytes(path, limit):
    """Read only a bounded regular file, including when a path is replaced."""
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError(f"non-regular source or report: {path}")
        data = bytearray()
        while len(data) <= limit:
            chunk = os.read(fd, min(65536, limit + 1 - len(data)))
            if not chunk:
                return bytes(data)
            data.extend(chunk)
        raise ValueError(f"source or report exceeds byte limit: {path}")
    finally:
        os.close(fd)


def current_sources(baseline):
    """Reject current drift before parsing or evaluating any current source."""
    raw = {}
    for path in SOURCE_PATHS:
        pinned = baseline[path]
        try:
            data = read_regular_bytes(ROOT / path, len(pinned))
        except (OSError, ValueError) as exc:
            raise ValueError("source pins or arithmetic report differ; independent review required") from exc
        if len(data) != len(pinned) or hashlib.sha256(data).digest() != hashlib.sha256(pinned).digest():
            raise ValueError("source pins or arithmetic report differ; independent review required")
        raw[path] = data
    return raw


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--replay-baseline", action="store_true")
    args = parser.parse_args()
    baseline = baseline_sources()
    if args.check:
        current_sources(baseline)
    report = build(baseline)
    encoded = json.dumps(report, sort_keys=True, indent=2, allow_nan=False) + "\n"
    if read_regular_bytes(REPORT, REPORT_LIMIT) != encoded.encode("utf-8"):
        raise ValueError("source pins or arithmetic report differ; independent review required")
    print(f"PASS: {len(report['checks_passed'])} arithmetic/source checks; "
          f"{len(SOURCE_PATHS)} public source pins; full cohort BLOCKED; no authority")


if __name__ == "__main__":
    main()
