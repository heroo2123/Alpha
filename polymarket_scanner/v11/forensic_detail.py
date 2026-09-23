"""Bounded descriptive diagnostics for preserved control evidence only.

No labels are re-attested here, no causal effect is estimated, and no policy is
selected. Missing timestamps, cohort links and source metadata stay explicit.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, time as day_time
import json
import math
from statistics import median
import time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from tools.v11_snapshot import SnapshotError


UNKNOWN = "UNKNOWN_NOT_PERSISTED"


def finite(value):
    return float(value) if type(value) in (int, float) and math.isfinite(value) else None


def mapping(value):
    return value if isinstance(value, dict) else {}


def text(value):
    return value if isinstance(value, str) and value else UNKNOWN


def enrich(row, data):
    source = mapping(data.get("forecast_raw_evidence"))
    contract = mapping(data.get("strict_contract_identity"))
    row.update(city=text(data.get("city") or contract.get("location")),
               source=text(data.get("source") or source.get("provider")),
               provider_model=text(source.get("provider_model")),
               release_marker=text(data.get("release_sha")),
               config_digest=text(data.get("decision_config_sha256")),
               member_count=finite(data.get("member_count")),
               member_hits=finite(data.get("member_hits")),
               forecast_run_age_known=data.get("forecast_run_age_known") is True,
               cohort="QUARANTINED" if row["status"] == "QUARANTINED"
                      or "QUARANTIN" in row["validation"] else "NOT_QUARANTINED",
               target_day_start_lead_hours=None,
               time_to_settlement_hours=finite(data.get("time_to_settlement_hours")))
    # This is calendar lead, NOT provider initialization age or contract finality.
    try:
        opened = finite(row.get("opened_at"))
        local_start = datetime.combine(date.fromisoformat(row["target_date"]),
                                       day_time(), ZoneInfo(data["station_timezone"]))
        if opened is not None:
            row["target_day_start_lead_hours"] = (local_start.timestamp() - opened) / 3600
    except (ValueError, TypeError, KeyError, ZoneInfoNotFoundError, OverflowError):
        pass


def performance(rows):
    resolved = [r for r in rows if r["status"] in {"WON", "LOST", "RESOLVED_PARTIAL"}
                and not r["accounting_findings"] and r["cohort"] != "QUARANTINED"]
    pnl = [r["pnl"] for r in resolved]
    wins, losses = sum(v for v in pnl if v > 0), -sum(v for v in pnl if v < 0)
    by_event, by_station = defaultdict(float), defaultdict(float)
    for r in resolved:
        by_event[r["event_id"]] += r["pnl"]
        by_station[r["station"]] += r["pnl"]
    total = sum(pnl)
    winning = sorted((v for v in pnl if v > 0), reverse=True)
    winning_events = sorted((v for v in by_event.values() if v > 0), reverse=True)
    best_station = max(by_station, key=lambda k: (by_station[k], k)) if by_station else None
    # Aggregate simultaneous settlement receipts before measuring realized drawdown;
    # arbitrary row ordering within one timestamp cannot create a fictitious peak.
    by_time = defaultdict(float)
    known_times = all(finite(r.get("settled_at")) is not None for r in resolved)
    if known_times:
        for r in resolved:
            by_time[r["settled_at"]] += r["pnl"]
    peak = running = drawdown = 0.0
    for timestamp in sorted(by_time):
        running += by_time[timestamp]
        peak = max(peak, running)
        drawdown = max(drawdown, peak - running)
    return {
        "selected_resolved_trades": len(resolved),
        "resolved_unique_events": len(by_event),
        "resolved_unique_station_days": len({(r["station"], r["target_date"]) for r in resolved
                                               if UNKNOWN not in (r["station"], r["target_date"])}),
        "independence_validated": False,
        "gross_positive_paper_pnl": wins, "gross_negative_paper_pnl": losses,
        "profit_factor": wins / losses if losses else None,
        "mean_paper_pnl": total / len(pnl) if pnl else None,
        "median_paper_pnl": median(pnl) if pnl else None,
        "worst_trade_paper_pnl": min(pnl) if pnl else None,
        "best_trade_paper_pnl": max(pnl) if pnl else None,
        "realized_only_drawdown": drawdown if pnl and known_times else None,
        "drawdown_excludes_unrealized_open_risk": True,
        "without_largest_winning_trade_pnl": total - sum(winning[:1]),
        "without_top_three_winning_trades_pnl": total - sum(winning[:3]),
        "without_largest_winning_event_pnl": total - sum(winning_events[:1]),
        "without_top_three_winning_events_pnl": total - sum(winning_events[:3]),
        "best_station": best_station,
        "without_best_station_pnl": total - by_station[best_station] if best_station else None,
        "station_paper_pnl": dict(sorted(by_station.items())),
        "open_simulated_capital": sum(r["capital_used"] for r in rows
                                       if r["status"] == "OPEN" and not r["accounting_findings"]),
        "quarantined_capital_excluded": sum(r["capital_used"] or 0 for r in rows
                                            if r["cohort"] == "QUARANTINED"),
        "fees_and_slippage_actual_live": None,
    }


def schema_inventory(db):
    result = []
    for row in db.execute("SELECT name FROM sqlite_schema WHERE type='table' ORDER BY name"):
        name = row[0]
        quoted = '"' + name.replace('"', '""') + '"'
        result.append({"table": name,
                       "rows": db.execute("SELECT COUNT(*) FROM " + quoted).fetchone()[0],
                       "columns": [dict(r) for r in db.execute("PRAGMA table_info(" + quoted + ")")]})
    return result


def signal_funnels(db, tables, lanes, *, deadline, max_rows):
    counts, unadmitted = defaultdict(Counter), defaultdict(Counter)
    decision_lanes = defaultdict(set)
    columns = {r[1] for r in db.execute("PRAGMA table_info(weather_paper_signals)")}
    detailed = {"status", "telegram_sent_at", "created_at"} <= columns
    if not detailed:
        return {"status": "UNKNOWN_SCHEMA_LACKS_DELIVERY_FIELDS"}
    query = """SELECT s.*,EXISTS(SELECT 1 FROM weather_paper_positions p WHERE p.signal_id=s.id)
               AS has_position FROM weather_paper_signals s ORDER BY s.id LIMIT ?"""
    signals = db.execute(query, (max_rows + 1,)).fetchall()
    if len(signals) > max_rows:
        raise SnapshotError("FORENSICS_SIGNAL_LIMIT")
    for r in signals:
        if time.monotonic() > deadline:
            raise SnapshotError("FORENSICS_DEADLINE")
        c = counts[r["lane"]]
        c["persisted_signal_records"] += 1
        c["persisted_delivery_receipts"] += r["telegram_sent_at"] is not None
        c["signals_linked_to_positions"] += r["has_position"]
        if not r["has_position"]:
            unadmitted[r["lane"]][r["status"]] += 1
        try:
            value = json.loads(r["payload_json"])
            decision = value.get("decision_id") if isinstance(value, dict) else None
            if isinstance(decision, str) and decision:
                decision_lanes[decision].add(r["lane"])
        except (TypeError, ValueError):
            c["unreadable_payloads"] += 1
    linked = defaultdict(Counter)
    if "weather_paper_decisions" in tables:
        cols = {r[1] for r in db.execute("PRAGMA table_info(weather_paper_decisions)")}
        if "decision_id" in cols:
            for index, r in enumerate(db.execute("SELECT decision_id,outcome,reason FROM weather_paper_decisions")):
                if index >= max_rows or time.monotonic() > deadline:
                    raise SnapshotError("FORENSICS_DECISION_LIMIT_OR_DEADLINE")
                matches = decision_lanes.get(r["decision_id"], set())
                lane = next(iter(matches)) if len(matches) == 1 else UNKNOWN
                linked[lane][(r["outcome"], r["reason"])] += 1
    return {"status": "PERSISTED_RECORD_COUNTS_NOT_A_SINGLE_COHORT_FUNNEL",
            "pre_signal_historical_evaluation_counts": None,
            "lanes": {lane: {**{key: counts[lane][key] for key in
                                ("persisted_signal_records", "persisted_delivery_receipts", "signals_linked_to_positions")},
                              "signal_statuses_without_position": dict(unadmitted[lane]),
                              "decision_records": [{"outcome": k[0], "reason": k[1], "records": v}
                                                   for k, v in linked[lane].most_common()]}
                      for lane in sorted(set(lanes) | set(counts) | set(linked))},
            "attribution": "Decision ID joins only; absent or ambiguous joins stay unknown."}


def capture_diagnostics(db, tables, *, deadline, max_rows):
    result = {"included_in_core_pnl": False, "financial_authority": False}
    if "weather_same_day_capture_attempts" in tables:
        result["attempts"] = [dict(r) for r in db.execute(
            "SELECT outcome,error_code,COUNT(*) AS records FROM weather_same_day_capture_attempts "
            "GROUP BY outcome,error_code ORDER BY records DESC")]
    if "weather_same_day_captures" not in tables:
        return result
    # Reuse only pure V10 decoding functions, never instantiate its writable store.
    from ..weather_only_same_day_capture_store_compressed import (
        _bounded_decompress, _strict_json_dict, _digest_payload,
        CURRENT_CAPTURE_ENCODING, LEGACY_CAPTURE_ENCODING, SameDayCaptureStoreError,
    )
    reasons, failures = Counter(), Counter()
    verified = 0
    for index, raw in enumerate(db.execute("SELECT * FROM weather_same_day_captures")):
        if index >= max_rows or time.monotonic() > deadline:
            raise SnapshotError("FORENSICS_CAPTURE_LIMIT_OR_DEADLINE")
        r = dict(raw)
        try:
            encoding = r.get("capture_encoding", LEGACY_CAPTURE_ENCODING)
            blob = r["capture_json"]
            if encoding == CURRENT_CAPTURE_ENCODING:
                decoded = _bounded_decompress(blob)
            elif encoding == LEGACY_CAPTURE_ENCODING:
                decoded = blob.encode() if isinstance(blob, str) else blob
            else:
                raise ValueError("UNSUPPORTED_ENCODING")
            value = _strict_json_dict(decoded)
            if (value.get("capture_sha256") != r["capture_sha256"]
                    or _digest_payload(value) != r["capture_sha256"]
                    or (r.get("capture_uncompressed_bytes") is not None
                        and r["capture_uncompressed_bytes"] != len(decoded))):
                raise ValueError("DIGEST_OR_LENGTH_MISMATCH")
            if any(value.get(key) != r[key] for key in
                   ("event_id", "station", "target_date", "family", "unit", "as_of", "status")):
                raise ValueError("SQL_PAYLOAD_IDENTITY_MISMATCH")
            block = value.get("block_reasons")
            if not isinstance(block, list) or block != json.loads(r["block_reasons_json"]):
                raise ValueError("REASON_IDENTITY_MISMATCH")
            if any(value.get(key) is not False for key in
                   ("included_in_validated_pnl", "calibrated_probability", "same_day_delivery_enabled",
                    "settlement_authority", "financial_authority")):
                raise ValueError("AUTHORITY_BOUNDARY_INVALID")
            reasons.update(block)
            verified += 1
        except (ValueError, TypeError, KeyError, UnicodeError, SameDayCaptureStoreError) as exc:
            # Do not emit raw compressed data or arbitrary provider errors.
            failures[type(exc).__name__] += 1
    result.update(verified_capture_payloads=verified, capture_integrity_failures=dict(failures),
                  verified_capture_block_reasons=dict(reasons),
                  reasons_overlap=True, calibration_certified=False)
    return result


def runtime_context(directory, manifest):
    files = {r["filename"] for r in manifest.get("context_files", [])}
    if "status-after.bin" not in files:
        return {"status": "UNKNOWN_NO_CAPTURED_STATUS"}
    data = json.loads((directory / "status-after.bin").read_bytes())
    fields = ("release_sha", "started_at", "finished_at", "cycle_ok", "cycle_seconds",
              "financial_authority", "automatic_order_placement", "wallet_or_order_api_loaded",
              "paper_tracker_ok", "operator_all_lanes_healthy", "pws_enabled",
              "discovered_weather_events", "strict_supported_weather_events", "unsupported_weather_events",
              "forecast_events_evaluated", "eligible_forecast_events_evaluated", "forecast_candidate_count",
              "new_signal_count", "telegram_sent_count", "dispatch_skipped_total", "forecast_nonfatal_skip_count",
              "global_weather_coverage_complete", "gamma_census_complete", "global_weather_recall_complete",
              "same_day_delivery_enabled", "same_day_paper_calibrated_probability", "structural_policy",
              "structural_opportunity_count", "structural_suppressed_total", "maker_queue_certified",
              "maker_proposals_sent_total", "maker_orders_activated_total", "maker_block_reason",
              "maker_stream_degraded", "result_lag_block_reason", "result_lag_validated_finality",
              "result_lag_research_evaluated", "result_lag_research_candidates", "result_lag_research_skipped_total",
              "source_shock_skipped_total", "forecast_nonfatal_skips", "unsupported_weather_reason_counts")
    context = {k: data.get(k) for k in fields}
    captured = datetime.fromisoformat(manifest["capture_completed_at"]).timestamp()
    finished = finite(data.get("finished_at"))
    context.update(status="CAPTURED_SELF_REPORT_NOT_INDEPENDENT_ATTESTATION",
                   cycle_age_at_capture_seconds=captured-finished if finished is not None else None,
                   cycle_freshness="UNKNOWN_WITHOUT_DECLARED_RUNTIME_SLA",
                   context_consistency=manifest.get("context_consistency"),
                   historical_funnel_reconstruction=False)
    for key in ("same_day_three_layer", "paper_settlement", "result_lag_research_settlement"):
        context[key] = data.get(key)
    return context
