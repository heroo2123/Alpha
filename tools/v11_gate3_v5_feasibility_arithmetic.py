"""Public, offline arithmetic only; no project imports, transport or admission.

Run with --check to reproduce the committed source pins and results. --write
regenerates only the companion JSON for a new independently reviewed candidate.
Source expressions are interpreted with a tiny arithmetic AST evaluator, never
executed/imported. Checks use exceptions so optimized Python preserves them.
"""
import argparse
import ast
import hashlib
import json
import operator
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "docs/V11_GATE3_V5_FEASIBILITY_20261003.arithmetic.json"
BASELINE = "e73fb9b66c0cf2591c0b8017925c764aac6cff1e"
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


def build():
    raw = {p: (ROOT / p).read_bytes() for p in SOURCE_PATHS}
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
        "baseline_tree": "8afe7a24c6e437b0b6345a1fadc08e136797a2c1",
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--write", action="store_true")
    args = parser.parse_args()
    report = build()
    encoded = json.dumps(report, sort_keys=True, indent=2, allow_nan=False) + "\n"
    if args.write:
        REPORT.write_text(encoded, encoding="utf-8")
    elif REPORT.read_text(encoding="utf-8") != encoded:
        raise ValueError("source pins or arithmetic report differ; independent review required")
    print(f"PASS: {len(report['checks_passed'])} arithmetic/source checks; "
          f"{len(SOURCE_PATHS)} public source pins; full cohort BLOCKED; no authority")


if __name__ == "__main__":
    main()
