from __future__ import annotations

"""Third independent-review corrective boundary for V5 weather evidence.

The execution ledger must not merely *perform* a post-Telegram weather refresh; the
refresh that justified admission must survive restart/replay and be part of the same
strong execution identity as the exact CLOB quote. This layer validates a compact
lane-specific weather-evidence record, binds its canonical digest into V5 execution
identity, and requires the appropriate record for each directional weather lane.

Multi-leg structural signals intentionally remain on V2's theoretical-only path and
therefore require no directional weather-evidence record and create no validated P&L.
No real-order, wallet, signing or financial authority is added.
"""

import hashlib
import json
import math

from .weather_only_independent_review_corrective_v2 import IndependentReviewPostReceiptStoreV2
from .weather_only_paper_positions import WeatherPaperPositionError


INDEPENDENT_REVIEW_CORRECTIVE_V3_VERSION = (
    "weather_all_paper_independent_review_corrective_v3_durable_weather_identity"
)
POST_RECEIPT_WEATHER_EVIDENCE_VERSION = "weather_post_receipt_evidence_v1"

FORECAST_LANE = "weather_forecast_raw_gap"
SAME_DAY_LANE = "weather_same_day_friend_lock"
SOURCE_SHOCK_LANE = "weather_official_extreme_new_exclusion"

FUTURE_DAY_KIND = "FUTURE_DAY_FRESH_PROVIDER"
SAME_DAY_KIND = "SAME_DAY_FRESH_THREE_LAYER"
SOURCE_SHOCK_KIND = "SOURCE_SHOCK_FRESH_WRH"

_EPS = 1e-9


def _finite(value: object, code: str) -> float:
    if value is None or isinstance(value, bool):
        raise WeatherPaperPositionError(code)
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise WeatherPaperPositionError(code) from None
    if not math.isfinite(number):
        raise WeatherPaperPositionError(code)
    return number


def _text(value: object, code: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise WeatherPaperPositionError(code)
    return text


def _sha64(value: object, code: str) -> str:
    text = _text(value, code).lower()
    if len(text) != 64 or any(ch not in "0123456789abcdef" for ch in text):
        raise WeatherPaperPositionError(code)
    return text


def _canonical_sha(value: object) -> str:
    try:
        raw = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError):
        raise WeatherPaperPositionError("V5_WEATHER_EVIDENCE_JSON_INVALID") from None
    return hashlib.sha256(raw).hexdigest()


def _normalized_weather_evidence(raw: object, *, recheck_started: float) -> dict:
    if not isinstance(raw, dict):
        raise WeatherPaperPositionError("V5_WEATHER_EVIDENCE_MISSING")
    if raw.get("version") != POST_RECEIPT_WEATHER_EVIDENCE_VERSION:
        raise WeatherPaperPositionError("V5_WEATHER_EVIDENCE_VERSION_INVALID")
    kind = _text(raw.get("kind"), "V5_WEATHER_EVIDENCE_KIND_MISSING")

    if kind == FUTURE_DAY_KIND:
        started = _finite(raw.get("provider_refresh_started_at"), "V5_FORECAST_REFRESH_TIME_INVALID")
        finished = _finite(raw.get("provider_refresh_finished_at"), "V5_FORECAST_REFRESH_TIME_INVALID")
        if started < 0.0 or finished < started or finished > recheck_started + _EPS:
            raise WeatherPaperPositionError("V5_FORECAST_REFRESH_NOT_BEFORE_CLOB")
        normalized = {
            "version": POST_RECEIPT_WEATHER_EVIDENCE_VERSION,
            "kind": kind,
            "provider_refresh_started_at": started,
            "provider_refresh_finished_at": finished,
            "forecast_source_evidence_sha256": _sha64(
                raw.get("forecast_source_evidence_sha256"), "V5_FORECAST_EVIDENCE_SHA_INVALID"
            ),
            "provider_run_age_known": bool(raw.get("provider_run_age_known")),
        }
        if normalized["provider_run_age_known"]:
            raise WeatherPaperPositionError("V5_FORECAST_RUN_AGE_UNPROVEN")
        return normalized

    if kind == SAME_DAY_KIND:
        as_of = _finite(raw.get("as_of"), "V5_THREE_LAYER_AS_OF_INVALID")
        if as_of < 0.0 or as_of > recheck_started + _EPS:
            raise WeatherPaperPositionError("V5_THREE_LAYER_NOT_BEFORE_CLOB")
        hits_raw = raw.get("gefs_hits")
        total_raw = raw.get("gefs_total")
        if isinstance(hits_raw, bool) or isinstance(total_raw, bool):
            raise WeatherPaperPositionError("V5_THREE_LAYER_GEFS_SUPPORT_INVALID")
        try:
            hits = int(hits_raw)
            total = int(total_raw)
        except (TypeError, ValueError, OverflowError):
            raise WeatherPaperPositionError("V5_THREE_LAYER_GEFS_SUPPORT_INVALID") from None
        if total != 31 or hits < 30 or hits > total:
            raise WeatherPaperPositionError("V5_THREE_LAYER_GEFS_SUPPORT_INVALID")
        return {
            "version": POST_RECEIPT_WEATHER_EVIDENCE_VERSION,
            "kind": kind,
            "as_of": as_of,
            "capture_sha256": _sha64(raw.get("capture_sha256"), "V5_THREE_LAYER_CAPTURE_SHA_INVALID"),
            "wrh_evidence_sha256": _sha64(raw.get("wrh_evidence_sha256"), "V5_THREE_LAYER_WRH_SHA_INVALID"),
            "nws_evidence_sha256": _sha64(raw.get("nws_evidence_sha256"), "V5_THREE_LAYER_NWS_SHA_INVALID"),
            "gefs_evidence_sha256": _sha64(raw.get("gefs_evidence_sha256"), "V5_THREE_LAYER_GEFS_SHA_INVALID"),
            "gefs_hits": hits,
            "gefs_total": total,
            "nws_sampled_extreme": _finite(raw.get("nws_sampled_extreme"), "V5_THREE_LAYER_NWS_EXTREME_INVALID"),
            "observed_extreme": _finite(raw.get("observed_extreme"), "V5_THREE_LAYER_OBSERVED_EXTREME_INVALID"),
        }

    if kind == SOURCE_SHOCK_KIND:
        received = _finite(raw.get("wrh_received_at"), "V5_SOURCE_SHOCK_WRH_TIME_INVALID")
        if received < 0.0 or received > recheck_started + _EPS:
            raise WeatherPaperPositionError("V5_SOURCE_SHOCK_WRH_NOT_BEFORE_CLOB")
        return {
            "version": POST_RECEIPT_WEATHER_EVIDENCE_VERSION,
            "kind": kind,
            "wrh_received_at": received,
            "wrh_evidence_sha256": _sha64(raw.get("wrh_evidence_sha256"), "V5_SOURCE_SHOCK_WRH_SHA_INVALID"),
            "observed_extreme": _finite(raw.get("observed_extreme"), "V5_SOURCE_SHOCK_EXTREME_INVALID"),
        }

    raise WeatherPaperPositionError("V5_WEATHER_EVIDENCE_KIND_INVALID")


class IndependentReviewPostReceiptStoreV3(IndependentReviewPostReceiptStoreV2):
    """Persist and identity-bind causal weather evidence for directional fills."""

    @staticmethod
    def _normalized_execution(execution: dict) -> dict:
        normalized = IndependentReviewPostReceiptStoreV2._normalized_execution(execution)
        raw = execution.get("post_receipt_weather_evidence") if isinstance(execution, dict) else None
        if raw is None:
            return normalized
        evidence = _normalized_weather_evidence(
            raw,
            recheck_started=float(normalized["post_receipt_recheck_started_at"]),
        )
        upgraded = dict(normalized)
        upgraded["post_receipt_weather_evidence"] = evidence
        upgraded["post_receipt_weather_evidence_sha256"] = _canonical_sha(evidence)
        return upgraded

    def admit_post_receipt_position(
        self, signal_id: int, target_stake_usd: float, execution: dict
    ) -> dict:
        sid = int(signal_id)
        with self._conn() as db:
            row = db.execute("SELECT lane FROM weather_paper_signals WHERE id=?", (sid,)).fetchone()
        if row is None:
            raise WeatherPaperPositionError("V5_SIGNAL_NOT_FOUND")
        lane = str(row["lane"] or "")
        expected = {
            FORECAST_LANE: FUTURE_DAY_KIND,
            SAME_DAY_LANE: SAME_DAY_KIND,
            SOURCE_SHOCK_LANE: SOURCE_SHOCK_KIND,
        }.get(lane)
        if expected is None:
            return super().admit_post_receipt_position(sid, target_stake_usd, execution)
        normalized = self._normalized_execution(execution)
        evidence = normalized.get("post_receipt_weather_evidence")
        if not isinstance(evidence, dict):
            raise WeatherPaperPositionError("V5_WEATHER_EVIDENCE_MISSING")
        if str(evidence.get("kind") or "") != expected:
            raise WeatherPaperPositionError("V5_WEATHER_EVIDENCE_LANE_MISMATCH")
        return super().admit_post_receipt_position(sid, target_stake_usd, normalized)
