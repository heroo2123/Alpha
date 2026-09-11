from __future__ import annotations

"""Machine-checkable preregistration for Stage W7 e2-micro silent-shadow acceptance.

The thresholds are frozen before the first W7 live run. They encode the weather-only
program's published resource targets plus the existing 350 MiB weather runtime RSS
bound. Passing this evaluator is research/shadow acceptance only; it can never promote
a detector, enable Telegram financial delivery or authorize an order.
"""

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass, field


WEATHER_W7_ACCEPTANCE_VERSION = "weather_w7_acceptance_v1_preregistered_45m_resource_containment"
WEATHER_W7_POLICY_ID = "WEATHER_W7_E2_MICRO_45M_V1"
MIN_DURATION_SECONDS = 45 * 60
MIN_SAMPLE_COUNT = 80
MAX_SAMPLE_GAP_SECONDS = 60.0
MAX_PROCESS_RSS_BYTES = 350 * 1024 * 1024
MAX_SWAP_USED_BYTES = 0
MIN_HOST_MEM_AVAILABLE_BYTES = 128 * 1024 * 1024
MAX_INCREMENTAL_EVALUATION_SECONDS = 2.0
MAX_SOURCE_UPDATE_CONFIRMATION_SECONDS = 5.0
MIN_SOURCE_UPDATE_SAMPLES = 1
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class WeatherW7AcceptanceError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


def _number(value: object, *, nonnegative: bool = True) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise WeatherW7AcceptanceError("W7_NUMBER_INVALID")
    number = float(value)
    if not math.isfinite(number) or (nonnegative and number < 0.0):
        raise WeatherW7AcceptanceError("W7_NUMBER_INVALID")
    return number


def _integer(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise WeatherW7AcceptanceError("W7_INTEGER_INVALID")
    return value


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def _sha(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class WeatherW7Policy:
    policy_id: str = WEATHER_W7_POLICY_ID
    min_duration_seconds: int = MIN_DURATION_SECONDS
    min_sample_count: int = MIN_SAMPLE_COUNT
    max_sample_gap_seconds: float = MAX_SAMPLE_GAP_SECONDS
    max_process_rss_bytes: int = MAX_PROCESS_RSS_BYTES
    max_swap_used_bytes: int = MAX_SWAP_USED_BYTES
    min_host_mem_available_bytes: int = MIN_HOST_MEM_AVAILABLE_BYTES
    max_incremental_evaluation_seconds: float = MAX_INCREMENTAL_EVALUATION_SECONDS
    max_source_update_confirmation_seconds: float = MAX_SOURCE_UPDATE_CONFIRMATION_SECONDS
    min_source_update_samples: int = MIN_SOURCE_UPDATE_SAMPLES

    def __post_init__(self):
        if self.policy_id != WEATHER_W7_POLICY_ID:
            raise WeatherW7AcceptanceError("W7_POLICY_ID_DRIFT")
        integer_fields = (
            self.min_duration_seconds,
            self.min_sample_count,
            self.max_process_rss_bytes,
            self.max_swap_used_bytes,
            self.min_host_mem_available_bytes,
            self.min_source_update_samples,
        )
        for value in integer_fields:
            _integer(value)
        for value in (
            self.max_sample_gap_seconds,
            self.max_incremental_evaluation_seconds,
            self.max_source_update_confirmation_seconds,
        ):
            if _number(value) <= 0.0:
                raise WeatherW7AcceptanceError("W7_POLICY_NUMBER_INVALID")
        if self.min_duration_seconds != MIN_DURATION_SECONDS or self.min_sample_count != MIN_SAMPLE_COUNT:
            raise WeatherW7AcceptanceError("W7_WINDOW_POLICY_DRIFT")
        if self.max_process_rss_bytes != MAX_PROCESS_RSS_BYTES or self.max_swap_used_bytes != 0:
            raise WeatherW7AcceptanceError("W7_RESOURCE_POLICY_DRIFT")
        if self.min_host_mem_available_bytes != MIN_HOST_MEM_AVAILABLE_BYTES:
            raise WeatherW7AcceptanceError("W7_RESOURCE_POLICY_DRIFT")
        if self.max_incremental_evaluation_seconds != MAX_INCREMENTAL_EVALUATION_SECONDS:
            raise WeatherW7AcceptanceError("W7_LATENCY_POLICY_DRIFT")
        if self.max_source_update_confirmation_seconds != MAX_SOURCE_UPDATE_CONFIRMATION_SECONDS:
            raise WeatherW7AcceptanceError("W7_LATENCY_POLICY_DRIFT")

    @property
    def policy_sha256(self) -> str:
        return _sha(asdict(self))


@dataclass(frozen=True, slots=True)
class WeatherW7Sample:
    observed_at: float
    cycle_ok: bool
    process_rss_bytes: int
    swap_used_bytes: int
    host_mem_available_bytes: int
    incremental_evaluation_seconds: float
    source_update_confirmation_seconds: float | None
    weather_event_count: int
    non_weather_materialized_count: int
    exact_clob_required_for_candidates: bool
    financial_authority: bool
    financial_delivery: bool
    automatic_order_placement: bool

    def __post_init__(self):
        _number(self.observed_at)
        for value in (
            self.process_rss_bytes,
            self.swap_used_bytes,
            self.host_mem_available_bytes,
            self.weather_event_count,
            self.non_weather_materialized_count,
        ):
            _integer(value)
        _number(self.incremental_evaluation_seconds)
        if self.source_update_confirmation_seconds is not None:
            _number(self.source_update_confirmation_seconds)
        for value in (
            self.cycle_ok,
            self.exact_clob_required_for_candidates,
            self.financial_authority,
            self.financial_delivery,
            self.automatic_order_placement,
        ):
            if type(value) is not bool:
                raise WeatherW7AcceptanceError("W7_BOOLEAN_INVALID")


@dataclass(frozen=True, slots=True)
class WeatherW7RunEvidence:
    release_sha: str
    samples: tuple[WeatherW7Sample, ...]
    telegram_outbox_before: int
    telegram_outbox_after: int
    detector_promotions: int
    order_attempts: int
    actual_orders_placed: int
    actual_fills_recorded: int
    service_restart_count: int

    def __post_init__(self):
        if not _SHA_RE.fullmatch(str(self.release_sha or "").lower()):
            raise WeatherW7AcceptanceError("W7_RELEASE_SHA_INVALID")
        if not isinstance(self.samples, tuple) or any(not isinstance(row, WeatherW7Sample) for row in self.samples):
            raise WeatherW7AcceptanceError("W7_SAMPLES_INVALID")
        for value in (
            self.telegram_outbox_before,
            self.telegram_outbox_after,
            self.detector_promotions,
            self.order_attempts,
            self.actual_orders_placed,
            self.actual_fills_recorded,
            self.service_restart_count,
        ):
            _integer(value)


@dataclass(frozen=True, slots=True)
class WeatherW7AcceptanceReport:
    version: str
    policy_id: str
    policy_sha256: str
    release_sha: str
    sample_count: int
    duration_seconds: float
    max_sample_gap_seconds: float | None
    max_process_rss_bytes: int | None
    max_swap_used_bytes: int | None
    min_host_mem_available_bytes: int | None
    max_incremental_evaluation_seconds: float | None
    source_update_sample_count: int
    max_source_update_confirmation_seconds: float | None
    passed: bool
    reasons: tuple[str, ...]
    financial_authority: bool = field(init=False, default=False)
    financial_delivery: bool = field(init=False, default=False)
    detector_promotion_authority: bool = field(init=False, default=False)
    automatic_order_placement: bool = field(init=False, default=False)

    def as_dict(self) -> dict:
        return asdict(self)


def evaluate_weather_w7_acceptance(
    evidence: WeatherW7RunEvidence,
    *,
    policy: WeatherW7Policy | None = None,
) -> WeatherW7AcceptanceReport:
    if not isinstance(evidence, WeatherW7RunEvidence):
        raise WeatherW7AcceptanceError("W7_EVIDENCE_TYPE_INVALID")
    frozen = policy or WeatherW7Policy()
    if not isinstance(frozen, WeatherW7Policy):
        raise WeatherW7AcceptanceError("W7_POLICY_TYPE_INVALID")

    samples = tuple(sorted(evidence.samples, key=lambda row: row.observed_at))
    reasons: list[str] = []
    count = len(samples)
    if count < frozen.min_sample_count:
        reasons.append(f"SAMPLE_COUNT_BELOW_MIN:{count}")
    duration = samples[-1].observed_at - samples[0].observed_at if count >= 2 else 0.0
    if duration + 1e-9 < frozen.min_duration_seconds:
        reasons.append(f"DURATION_BELOW_MIN:{duration:.3f}")

    gaps = [b.observed_at - a.observed_at for a, b in zip(samples, samples[1:])]
    max_gap = max(gaps) if gaps else None
    if any(gap <= 0.0 for gap in gaps):
        reasons.append("SAMPLE_TIMESTAMPS_NOT_STRICTLY_INCREASING")
    if max_gap is not None and max_gap > frozen.max_sample_gap_seconds + 1e-9:
        reasons.append(f"SAMPLE_GAP_EXCEEDED:{max_gap:.3f}")

    rss = max((row.process_rss_bytes for row in samples), default=None)
    swap = max((row.swap_used_bytes for row in samples), default=None)
    mem = min((row.host_mem_available_bytes for row in samples), default=None)
    eval_latency = max((row.incremental_evaluation_seconds for row in samples), default=None)
    updates = [row.source_update_confirmation_seconds for row in samples if row.source_update_confirmation_seconds is not None]
    update_max = max(updates) if updates else None

    if rss is None or rss > frozen.max_process_rss_bytes:
        reasons.append(f"PROCESS_RSS_EXCEEDED:{rss}")
    if swap is None or swap > frozen.max_swap_used_bytes:
        reasons.append(f"SWAP_USED:{swap}")
    if mem is None or mem < frozen.min_host_mem_available_bytes:
        reasons.append(f"HOST_MEM_AVAILABLE_BELOW_MIN:{mem}")
    if eval_latency is None or eval_latency > frozen.max_incremental_evaluation_seconds + 1e-9:
        reasons.append(f"INCREMENTAL_EVAL_LATENCY_EXCEEDED:{eval_latency}")
    if len(updates) < frozen.min_source_update_samples:
        reasons.append(f"SOURCE_UPDATE_SAMPLE_COUNT_BELOW_MIN:{len(updates)}")
    elif update_max is None or update_max > frozen.max_source_update_confirmation_seconds + 1e-9:
        reasons.append(f"SOURCE_UPDATE_LATENCY_EXCEEDED:{update_max}")

    for index, row in enumerate(samples):
        if row.cycle_ok is not True:
            reasons.append(f"CYCLE_UNHEALTHY:{index}")
        if row.weather_event_count <= 0:
            reasons.append(f"WEATHER_CATALOG_EMPTY:{index}")
        if row.non_weather_materialized_count != 0:
            reasons.append(f"NON_WEATHER_MATERIALIZED:{index}:{row.non_weather_materialized_count}")
        if row.exact_clob_required_for_candidates is not True:
            reasons.append(f"EXACT_CLOB_INVARIANT_LOST:{index}")
        if row.financial_authority is not False:
            reasons.append(f"FINANCIAL_AUTHORITY_ENABLED:{index}")
        if row.financial_delivery is not False:
            reasons.append(f"FINANCIAL_DELIVERY_ENABLED:{index}")
        if row.automatic_order_placement is not False:
            reasons.append(f"ORDER_PLACEMENT_ENABLED:{index}")

    if evidence.telegram_outbox_after != evidence.telegram_outbox_before:
        reasons.append("TELEGRAM_OUTBOX_CHANGED")
    if evidence.detector_promotions != 0:
        reasons.append(f"DETECTOR_PROMOTIONS:{evidence.detector_promotions}")
    if evidence.order_attempts != 0 or evidence.actual_orders_placed != 0 or evidence.actual_fills_recorded != 0:
        reasons.append("REAL_ORDER_OR_FILL_ACTIVITY_DETECTED")
    if evidence.service_restart_count != 0:
        reasons.append(f"SERVICE_RESTARTS:{evidence.service_restart_count}")

    unique_reasons = tuple(dict.fromkeys(reasons))
    return WeatherW7AcceptanceReport(
        version=WEATHER_W7_ACCEPTANCE_VERSION,
        policy_id=frozen.policy_id,
        policy_sha256=frozen.policy_sha256,
        release_sha=evidence.release_sha.lower(),
        sample_count=count,
        duration_seconds=duration,
        max_sample_gap_seconds=max_gap,
        max_process_rss_bytes=rss,
        max_swap_used_bytes=swap,
        min_host_mem_available_bytes=mem,
        max_incremental_evaluation_seconds=eval_latency,
        source_update_sample_count=len(updates),
        max_source_update_confirmation_seconds=update_max,
        passed=not unique_reasons,
        reasons=unique_reasons,
    )
