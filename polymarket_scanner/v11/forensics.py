"""V10 snapshot-only development scorecard; never imports a mutable V10 store.

All aggregates are PAPER evidence. Unknown fields remain unknown. This reader
does not select thresholds, claim causality, certify calibration, or approve V11.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import closing
import json
import math
from pathlib import Path
import time

from tools.v11_snapshot import open_verified_snapshot, SnapshotError
from .measurement import score_binary


RESOLVED = {"WON", "LOST", "RESOLVED_PARTIAL"}
KNOWN_LANES = ("weather_forecast_raw_gap", "weather_same_day_friend_lock",
               "weather_official_extreme_new_exclusion", "weather_binary_pair_underround",
               "weather_complete_bucket_underround", "weather_maker_virtual_bid",
               "weather_result_lag", "weather_result_lag_research")
MISSING = "UNKNOWN_NOT_PERSISTED"


def number(value):
    return float(value) if type(value) in (int, float) and math.isfinite(value) else None


def payload(value):
    if not isinstance(value, str) or len(value) > 1024 * 1024:
        return {}
    try:
        parsed = json.loads(value)
    except (ValueError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def band(value, boundaries):
    value = number(value)
    if value is None:
        return MISSING
    for boundary in boundaries:
        if value < boundary:
            return f"<{boundary:g}"
    return f">={boundaries[-1]:g}"


def summarize(rows: list[dict]) -> dict:
    resolved = [r for r in rows if r["status"] in RESOLVED]
    valid_values = [r for r in resolved if not r["accounting_findings"]]
    capital = sum(r["capital_used"] for r in valid_values)
    pnl = sum(r["pnl"] for r in valid_values)
    return {
        "positions": len(rows), "statuses": dict(Counter(r["status"] for r in rows)),
        "resolved": len(resolved), "accounting_reconciled_resolved": len(valid_values),
        "unique_events": len({r["event_id"] for r in rows}),
        "unique_station_days": len({(r["station"], r["target_date"]) for r in rows
                                    if MISSING not in (r["station"], r["target_date"])}),
        "missing_station_day": sum(MISSING in (r["station"], r["target_date"]) for r in rows),
        "reconciled_paper_pnl": pnl,
        "resolved_simulated_capital_denominator": capital,
        "roi": pnl / capital if capital > 0 else None,
        "accounting_failures": sum(bool(r["accounting_findings"]) for r in rows),
        "no_fill_reasons": dict(Counter(r["no_fill_reason"] or "UNKNOWN" for r in rows if r["status"] == "NO_FILL")),
        "evidence_class": "V10_CONTROL_PAPER_DEVELOPMENT",
        "live_validated_pnl": None,
    }


def analyze_snapshot(directory: Path, *, max_positions: int = 100_000,
                     max_seconds: float = 30.0) -> dict:
    if type(max_positions) is not int or not 1 <= max_positions <= 100_000 or not 0 < max_seconds <= 120:
        raise SnapshotError("FORENSICS_BOUNDS_INVALID")
    db, manifest = open_verified_snapshot(directory)
    deadline = time.monotonic() + max_seconds
    with closing(db):
        db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_schema WHERE type='table'")}
        required = {"weather_paper_positions", "weather_paper_signals"}
        if not required <= tables:
            raise SnapshotError("V10_CORE_TABLES_MISSING")
        count = db.execute("SELECT COUNT(*) FROM weather_paper_positions").fetchone()[0]
        if count > max_positions:
            raise SnapshotError("FORENSICS_POSITION_LIMIT")
        pcols = {r[1] for r in db.execute("PRAGMA table_info(weather_paper_positions)")}
        scols = {r[1] for r in db.execute("PRAGMA table_info(weather_paper_signals)")}
        prequired = {"id", "signal_id", "lane", "event_id", "token_id", "side", "status", "filled_units", "entry_cost_per_unit", "capital_used", "pnl", "proceeds", "settlement_payout_per_unit"}
        srequired = {"id", "event_id", "token_id", "side", "lane", "payload_json", "model_probability"}
        if not prequired <= pcols or not srequired <= scols:
            raise SnapshotError("V10_CORE_SCHEMA_UNSUPPORTED")
        query = "SELECT p.*,s.id AS joined_signal_id,s.event_id AS signal_event_id,s.token_id AS signal_token_id,s.side AS signal_side,s.lane AS signal_lane,s.payload_json AS signal_payload,s.model_probability AS stored_probability FROM weather_paper_positions p LEFT JOIN weather_paper_signals s ON s.id=p.signal_id ORDER BY p.id"
        rows = []
        limitations = {
            "PAPER quotes and simulated fills do not prove live execution quality.",
            "V10 data inspected here is DEVELOPMENT evidence, not untouched holdout.",
            "Missing source/receipt/horizon/regime fields are not reconstructed from hindsight.",
            "A zero-trade lane alone establishes neither value nor absence of opportunity.",
            "Protocol versions are separated; exact runtime release epochs need separately captured release history.",
            "Raw stored member frequencies are not certified calibrated probabilities.",
            "This report describes stored outcomes; it does not independently re-attest venue settlement labels.",
        }
        for raw in db.execute(query):
            if time.monotonic() > deadline:
                raise SnapshotError("FORENSICS_DEADLINE")
            row = dict(raw)
            data = payload(row.pop("signal_payload"))
            findings = []
            if row["joined_signal_id"] is None:
                findings.append("SIGNAL_MISSING")
            elif row["signal_event_id"] != row["event_id"]:
                findings.append("SIGNAL_EVENT_MISMATCH")
            if row["joined_signal_id"] is not None:
                for field in ("token_id", "side", "lane"):
                    if row[field] != row["signal_" + field]:
                        findings.append("SIGNAL_" + field.upper() + "_MISMATCH")
            for field in ("filled_units", "entry_cost_per_unit", "capital_used"):
                row[field] = number(row[field])
                if row[field] is None or row[field] < 0:
                    findings.append("INVALID_" + field.upper())
            if all(row[f] is not None for f in ("filled_units", "entry_cost_per_unit", "capital_used")):
                if not math.isclose(row["filled_units"] * row["entry_cost_per_unit"], row["capital_used"], abs_tol=1e-6, rel_tol=1e-8):
                    findings.append("CAPITAL_NOT_UNITS_TIMES_COST")
            if row["status"] == "NO_FILL" and (row["filled_units"] != 0 or row["capital_used"] != 0):
                findings.append("NO_FILL_WITH_CAPITAL_OR_UNITS")
            if row["status"] in RESOLVED:
                for field in ("proceeds", "pnl", "settlement_payout_per_unit"):
                    row[field] = number(row[field])
                    if row[field] is None:
                        findings.append("MISSING_" + field.upper())
                if all(row[f] is not None for f in ("proceeds", "pnl", "capital_used")) and not math.isclose(row["proceeds"]-row["capital_used"], row["pnl"], abs_tol=1e-6):
                    findings.append("PNL_RECONCILIATION_FAILED")
                if all(row[f] is not None for f in ("proceeds", "filled_units", "settlement_payout_per_unit")) and not math.isclose(row["filled_units"]*row["settlement_payout_per_unit"], row["proceeds"], abs_tol=1e-6):
                    findings.append("SETTLEMENT_PROCEEDS_FAILED")
            for field in ("station", "target_date", "family", "country", "city", "source", "event_state"):
                row[field] = data.get(field) if isinstance(data.get(field), str) and data[field] else MISSING
            row["horizon_hours"] = number(data.get("forecast_horizon_hours"))
            row["raw_gap"] = number(data.get("raw_gap"))
            row["ask_size"] = number(data.get("ask_size"))
            row["raw_probability"] = number(data.get("raw_probability"))
            row["raw_probability_is_side_bound"] = data.get("side") == row.get("side") and row.get("side") in {"YES", "NO"}
            row["protocol"] = row.get("execution_protocol") or row.get("position_version") or MISSING
            row["validation"] = row.get("validation_state") or "LEGACY_UNVERIFIED"
            row["no_fill_reason"] = row.get("no_fill_reason")
            row["accounting_findings"] = findings
            rows.append(row)
        by_lane, by_epoch = defaultdict(list), defaultdict(list)
        slices = {key: defaultdict(list) for key in ("station", "horizon_hours", "price", "probability", "raw_gap", "liquidity", "family", "event_state")}
        for row in rows:
            by_lane[row["lane"]].append(row)
            by_epoch[(row["protocol"], row["validation"])].append(row)
            if row["lane"] == "weather_forecast_raw_gap":
                values = {"station": row["station"], "horizon_hours": band(row["horizon_hours"], (6,24,48,72)),
                          "price": band(row["entry_cost_per_unit"], (.1,.25,.5,.75,.9)),
                          "probability": band(row["raw_probability"], (.25,.5,.75,.9,.99)),
                          "raw_gap": band(row["raw_gap"], (.05,.1,.2,.4)),
                          "liquidity": band(row["ask_size"], (10,50,100,500)),
                          "family": row["family"], "event_state": row["event_state"]}
                for key,value in values.items():
                    # Protocol/quarantine separation is retained in every subgroup.
                    slices[key][(str(value), row["protocol"], row["validation"])].append(row)
        signal_counts = {str(r[0]): int(r[1]) for r in db.execute("SELECT lane,COUNT(*) FROM weather_paper_signals GROUP BY lane")}
        decision_reasons = []
        if "weather_paper_decisions" in tables:
            decision_reasons = [dict(r) for r in db.execute("SELECT outcome,reason,COUNT(*) AS n FROM weather_paper_decisions GROUP BY outcome,reason ORDER BY n DESC LIMIT 100")]
        auxiliary = {}
        for table in sorted(tables):
            if table.startswith(("weather_maker_", "weather_result_lag_", "weather_same_day_")):
                escaped = '"' + table.replace('"','""') + '"'
                columns = {r[1] for r in db.execute(f"PRAGMA table_info({escaped})")}
                values = {"rows": db.execute(f"SELECT COUNT(*) FROM {escaped}").fetchone()[0],
                          "included_in_core_pnl": False}
                for col in ("status", "reason", "state"):
                    if col in columns:
                        values[col] = [dict(r) for r in db.execute(f'SELECT "{col}",COUNT(*) AS n FROM {escaped} GROUP BY "{col}" ORDER BY n DESC LIMIT 100')]
                auxiliary[table] = values
        funnels = {}
        for lane in sorted(set(KNOWN_LANES) | set(signal_counts) | set(by_lane)):
            lane_rows = by_lane[lane]
            funnels[lane] = {"discovered": None, "evaluated": None, "candidates": None,
                             "persisted_signals": signal_counts.get(lane,0),
                             "simulated_positions": len(lane_rows),
                             "no_fill": sum(r["status"] == "NO_FILL" for r in lane_rows),
                             "resolved": sum(r["status"] in RESOLVED for r in lane_rows),
                             "diagnosis": "UNKNOWN_WITHOUT_PERSISTED_PRE_SIGNAL_FUNNEL",
                             "auxiliary_lane_records_separate": True}
        calibration = []
        for (protocol, validation), members in by_epoch.items():
            selected = [r for r in members if r["lane"] == "weather_forecast_raw_gap"
                        and r["status"] in RESOLVED and not r["accounting_findings"]
                        and r["raw_probability_is_side_bound"] and r["raw_probability"] is not None
                        and 0 <= r["raw_probability"] <= 1 and r["settlement_payout_per_unit"] in (0,1)]
            if selected:
                calibration.append({"protocol": protocol, "validation": validation,
                    "label_basis": "STORED_PAPER_OUTCOME_NOT_REATTESTED",
                    "selected_trade_sample_only": True,
                    **score_binary([r["raw_probability"] for r in selected],
                                   [int(r["settlement_payout_per_unit"]) for r in selected],
                                   groups=[r["event_id"] for r in selected])})
        def split_summary(members):
            grouped = defaultdict(list)
            for item in members:
                grouped[(item["protocol"], item["validation"])].append(item)
            return [{"protocol": p, "validation": v, **summarize(group)} for (p,v),group in sorted(grouped.items())]
        return {"version": "alpha_v11_v10_forensics_v1", "snapshot_sha256": manifest["snapshot_sha256"],
                "capture_completed_at": manifest["capture_completed_at"], "classification": "PRIVATE_DEVELOPMENT_CONTROL_EVIDENCE",
                "financial_authority": False, "thresholds_selected": False,
                "all_core_records_diagnostic_only": summarize(rows),
                "epochs": split_summary(rows),
                "lanes": {lane: split_summary(members) for lane,members in sorted(by_lane.items())},
                "future_forecast_slices": {key: [{"slice": k[0], "protocol": k[1], "validation": k[2], **summarize(v)} for k,v in sorted(groups.items())] for key,groups in slices.items()},
                "stored_probability_diagnostics": calibration,
                "zero_lane_funnels": funnels, "global_decision_reasons_not_lane_attributed": decision_reasons,
                "separate_research_and_capture_tables": auxiliary,
                "accounting_findings": [{"position_id": r["id"], "findings": r["accounting_findings"]} for r in rows if r["accounting_findings"]],
                "limitations": sorted(limitations),
                "legacy_rule_verdict": "UNPROVEN_NO_CAUSAL_ATTRIBUTION_FROM_AGGREGATES",
                "hypothesis_policy": "Freeze development hypotheses before separate later/event-disjoint validation."}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = analyze_snapshot(args.snapshot)
    # Never overwrite prior evidence; callers choose a fresh private output path.
    import os
    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(result, stream, sort_keys=True, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"status": "ANALYZED", "output": str(args.output),
                      "snapshot_sha256": result["snapshot_sha256"], "financial_authority": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
