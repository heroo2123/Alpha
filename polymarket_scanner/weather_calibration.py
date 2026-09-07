from __future__ import annotations

import json
import math
import sqlite3
from collections import defaultdict
from dataclasses import dataclass

from .models import Signal

WEATHER_DETECTORS = {"weather_late_lock", "weather_friend_lock"}
WEATHER_CALIBRATION_VERSION = "weather_empirical_bins_v1"

# Prospective evidence gates. These are deliberately conservative and are not an
# automatic strategy-promotion policy: they only decide whether an empirical
# probability floor is statistically mature enough to be considered by the
# TRADE NOW boundary. Promotion still requires an explicit certification registry
# entry in trade_only.py.
MIN_TOTAL_RESOLVED = 100
MIN_BIN_RESOLVED = 30
MIN_DISTINCT_STATIONS = 8
MAX_BRIER_SCORE = 0.08
WILSON_Z = 1.96

# Fixed before looking at future results. Keep these bins stable so the calibration
# sample cannot be tuned retrospectively around whichever scores happened to win.
SCORE_BINS: tuple[tuple[float, float], ...] = (
    (0.00, 0.94),
    (0.94, 0.96),
    (0.96, 0.98),
    (0.98, 1.000000001),
)


@dataclass(frozen=True, slots=True)
class WeatherCalibration:
    detector: str
    model_version: str
    total_resolved: int
    distinct_stations: int
    overall_brier: float | None
    bin_index: int | None
    bin_resolved: int
    bin_mean_score: float | None
    bin_empirical_payout: float | None
    bin_lower_bound: float | None
    ready: bool
    reason: str

    def as_metadata(self) -> dict:
        return {
            "weather_calibration_version": WEATHER_CALIBRATION_VERSION,
            "weather_calibration_detector": self.detector,
            "weather_calibration_model_version": self.model_version,
            "weather_calibration_n": self.total_resolved,
            "weather_calibration_distinct_stations": self.distinct_stations,
            "weather_calibration_brier": self.overall_brier,
            "weather_calibration_bin": self.bin_index,
            "weather_calibration_bin_n": self.bin_resolved,
            "weather_calibration_bin_mean_score": self.bin_mean_score,
            "weather_calibration_bin_empirical_payout": self.bin_empirical_payout,
            "calibrated_probability_lower_bound": self.bin_lower_bound,
            "weather_calibration_ready": self.ready,
            "weather_calibration_reason": self.reason,
        }


def _meta(raw: object) -> dict:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        decoded = json.loads(str(raw))
    except Exception:
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _finite_probability(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(number) or number < 0.0 or number > 1.0:
        return None
    return number


def _bin_index(score: float) -> int | None:
    for i, (lo, hi) in enumerate(SCORE_BINS):
        if lo <= score < hi:
            return i
    return None


def _wilson_lower(successes: float, n: int, z: float = WILSON_Z) -> float | None:
    """Wilson lower confidence bound for a bounded success fraction.

    Polymarket can occasionally settle a disputed outcome at a fractional payout
    such as 0.5. Treating the sum of final payouts as fractional successes is a
    conservative quasi-binomial approximation and, importantly, never upgrades a
    partial payout into a full win.
    """
    if n <= 0:
        return None
    p = min(1.0, max(0.0, float(successes) / float(n)))
    z2 = z * z
    denom = 1.0 + z2 / n
    center = p + z2 / (2.0 * n)
    radius = z * math.sqrt((p * (1.0 - p) + z2 / (4.0 * n)) / n)
    return max(0.0, (center - radius) / denom)


def _load_clean_samples(db_path: str, detector: str, model_version: str) -> list[dict]:
    if detector not in WEATHER_DETECTORS or not model_version:
        return []
    try:
        with sqlite3.connect(db_path) as c:
            c.row_factory = sqlite3.Row
            columns = {str(row[1]) for row in c.execute("PRAGMA table_info(signals)")}
            if "settlement_payout" not in columns:
                return []
            rows = c.execute(
                """
                SELECT detector,status,settlement_payout,metadata,created_at
                FROM signals
                WHERE detector=?
                  AND status IN ('WON','LOST','RESOLVED_PARTIAL')
                  AND settlement_payout IS NOT NULL
                ORDER BY id
                """,
                (detector,),
            ).fetchall()
    except sqlite3.Error:
        return []

    clean: list[dict] = []
    for row in rows:
        meta = _meta(row["metadata"])
        if str(meta.get("weather_model_version") or "") != model_version:
            continue
        if meta.get("settlement_source_verified") is not True:
            continue
        score = _finite_probability(meta.get("lock_probability"))
        payout = _finite_probability(row["settlement_payout"])
        station = str(meta.get("station") or "").strip().upper()
        if score is None or payout is None or not station:
            continue
        idx = _bin_index(score)
        if idx is None:
            continue
        clean.append({"score": score, "payout": payout, "station": station, "bin": idx})
    return clean


def calibration_for_score(db_path: str, detector: str, model_version: str, score: float) -> WeatherCalibration:
    score_f = _finite_probability(score)
    if detector not in WEATHER_DETECTORS:
        return WeatherCalibration(detector, model_version, 0, 0, None, None, 0, None, None, None, False, "not a weather detector")
    if not model_version:
        return WeatherCalibration(detector, model_version, 0, 0, None, None, 0, None, None, None, False, "weather model version missing")
    if score_f is None:
        return WeatherCalibration(detector, model_version, 0, 0, None, None, 0, None, None, None, False, "raw weather score invalid")

    target_bin = _bin_index(score_f)
    samples = _load_clean_samples(db_path, detector, model_version)
    total = len(samples)
    stations = len({x["station"] for x in samples})
    overall_brier = (
        sum((float(x["score"]) - float(x["payout"])) ** 2 for x in samples) / total
        if total else None
    )

    grouped: dict[int, list[dict]] = defaultdict(list)
    for sample in samples:
        grouped[int(sample["bin"])].append(sample)
    bucket = grouped.get(int(target_bin), []) if target_bin is not None else []
    bin_n = len(bucket)
    bin_mean_score = sum(float(x["score"]) for x in bucket) / bin_n if bin_n else None
    bin_empirical = sum(float(x["payout"]) for x in bucket) / bin_n if bin_n else None
    lower = _wilson_lower(sum(float(x["payout"]) for x in bucket), bin_n) if bin_n else None

    reasons = []
    if total < MIN_TOTAL_RESOLVED:
        reasons.append(f"need {MIN_TOTAL_RESOLVED} clean resolved samples; have {total}")
    if bin_n < MIN_BIN_RESOLVED:
        reasons.append(f"need {MIN_BIN_RESOLVED} samples in score bin; have {bin_n}")
    if stations < MIN_DISTINCT_STATIONS:
        reasons.append(f"need {MIN_DISTINCT_STATIONS} distinct stations; have {stations}")
    if overall_brier is None or overall_brier > MAX_BRIER_SCORE:
        brier_text = "n/a" if overall_brier is None else f"{overall_brier:.4f}"
        reasons.append(f"Brier score {brier_text} exceeds {MAX_BRIER_SCORE:.4f} gate")
    if lower is None:
        reasons.append("conservative probability lower bound unavailable")

    ready = not reasons
    return WeatherCalibration(
        detector=detector,
        model_version=model_version,
        total_resolved=total,
        distinct_stations=stations,
        overall_brier=overall_brier,
        bin_index=target_bin,
        bin_resolved=bin_n,
        bin_mean_score=bin_mean_score,
        bin_empirical_payout=bin_empirical,
        bin_lower_bound=lower,
        ready=ready,
        reason="calibration evidence gates passed" if ready else "; ".join(reasons),
    )


def apply_weather_calibration(signal: Signal, db_path: str) -> bool:
    """Attach prospective empirical calibration evidence to a weather candidate."""
    if signal.detector not in WEATHER_DETECTORS:
        return True
    model_version = str(signal.metadata.get("weather_model_version") or "")
    score = _finite_probability(signal.metadata.get("lock_probability"))
    if score is None:
        calibration = WeatherCalibration(
            signal.detector, model_version, 0, 0, None, None, 0, None, None, None,
            False, "raw weather lock score missing or invalid",
        )
    else:
        calibration = calibration_for_score(db_path, signal.detector, model_version, score)
    signal.metadata.update(calibration.as_metadata())
    return calibration.ready
