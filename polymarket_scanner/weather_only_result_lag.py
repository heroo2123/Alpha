from __future__ import annotations

"""Deterministic WRH official-result-lag lane for the weather-only shadow program.

This is the first source-dependent WX2/WX8 candidate path. It deliberately starts
*after* exact WRH correction-window finality is prospectively proven. The finalized
daily high/low mechanically selects exactly one frozen contract bucket; only that
bucket's YES side can become a candidate.

A candidate is recorded only after two sequential exact CLOB snapshots both show the
winning YES executable with enough visible size and the configured conservative edge.
The second snapshot is the confirmation authority for the research record. Nothing in
this module sends Telegram, places orders, promotes a detector or grants financial
authority.
"""

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from typing import Protocol

from .weather_only_acceptance_latency import (
    CHANGE_FINALITY_TRANSITION,
    WeatherW7ConfirmedShadowCandidate,
    WeatherW7SourceLatencyError,
    WeatherW7SourceUpdateTrigger,
    build_w7_confirmed_shadow_candidate,
)
from .weather_only_calibration_capture import WRH_CALIBRATION_FINALITY_POLICY
from .weather_only_clob import (
    MAX_BOOK_AGE_SECONDS,
    WeatherExecutionSnapshot,
    conservative_taker_fee_per_share,
)
from .weather_only_contracts import (
    DAILY_HIGH,
    DAILY_LOW,
    SOURCE_NWS_WRH,
    CompiledWeatherEvent,
    WeatherBucket,
    compile_weather_event,
)
from .weather_only_rules import apply_rule_authority, compile_temperature_rule_authority
from .weather_only_wrh import WRHSourceSnapshot
from .weather_only_wrh_finality import WRHFinalizedRuleState, certify_wrh_first_following_transition


WEATHER_RESULT_LAG_VERSION = "weather_result_lag_v1_wrh_finality_double_exact_clob_shadow"
WEATHER_RESULT_LAG_POLICY_ID = "WRH_RESULT_LAG_SHADOW_EDGE_1PCT_V1"
DEFAULT_MIN_EDGE_PER_SHARE = 0.01
DEFAULT_MIN_VISIBLE_SHARES = 1.0


class WeatherResultLagError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


class ExactEventSnapshotClient(Protocol):
    async def exact_event_snapshot(self, compiled: CompiledWeatherEvent) -> WeatherExecutionSnapshot: ...


def _canonical(value: object) -> str:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
    except (TypeError, ValueError):
        raise WeatherResultLagError("RESULT_LAG_JSON_INVALID") from None


def _sha(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _finite(value: object, code: str) -> float:
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        raise WeatherResultLagError(code)
    number = float(value)
    if not math.isfinite(number) or number < 0.0:
        raise WeatherResultLagError(code)
    return number


@dataclass(frozen=True, slots=True)
class WeatherResultLagShadowPolicy:
    policy_id: str = WEATHER_RESULT_LAG_POLICY_ID
    min_edge_per_share: float = DEFAULT_MIN_EDGE_PER_SHARE
    min_visible_shares: float = DEFAULT_MIN_VISIBLE_SHARES

    def __post_init__(self) -> None:
        if self.policy_id != WEATHER_RESULT_LAG_POLICY_ID:
            raise WeatherResultLagError("RESULT_LAG_POLICY_ID_DRIFT")
        edge = _finite(self.min_edge_per_share, "RESULT_LAG_POLICY_EDGE_INVALID")
        size = _finite(self.min_visible_shares, "RESULT_LAG_POLICY_SIZE_INVALID")
        if edge != DEFAULT_MIN_EDGE_PER_SHARE or size != DEFAULT_MIN_VISIBLE_SHARES:
            raise WeatherResultLagError("RESULT_LAG_POLICY_DRIFT")
        if edge >= 1.0 or size <= 0.0:
            raise WeatherResultLagError("RESULT_LAG_POLICY_INVALID")

    @property
    def policy_sha256(self) -> str:
        return _sha(asdict(self))


@dataclass(frozen=True, slots=True)
class WeatherOfficialResultLagCandidate:
    version: str
    policy_id: str
    policy_sha256: str
    event_id: str
    market_id: str
    condition_id: str
    token_id: str
    station: str
    target_date: str
    family: str
    finalized_value_f: int
    finality_evidence_sha256: str
    source_snapshot_sha256: str
    rule_evidence_sha256: str
    first_clob_evidence_sha256: str
    confirmed_clob_evidence_sha256: str
    executable_ask: float
    visible_ask_shares: float
    fee_rate: float
    fee_exponent: int
    conservative_fee_per_share: float
    total_cost_per_share: float
    payout_per_share: float
    deterministic_edge_per_share: float
    minimum_required_edge_per_share: float
    confirmed_at: float
    candidate_evidence_sha256: str
    deterministic_result: bool = field(init=False, default=True)
    exact_clob_rechecked: bool = field(init=False, default=True)
    financial_authority: bool = field(init=False, default=False)
    financial_delivery: bool = field(init=False, default=False)
    automatic_order_placement: bool = field(init=False, default=False)

    def as_dict(self) -> dict:
        return asdict(self)


def _candidate_digest_payload(candidate: WeatherOfficialResultLagCandidate) -> dict:
    payload = candidate.as_dict()
    payload.pop("candidate_evidence_sha256", None)
    return payload


def _bucket_contains(bucket: WeatherBucket, value: int) -> bool:
    if bucket.lower is not None and value < bucket.lower:
        return False
    if bucket.upper is not None and value > bucket.upper:
        return False
    return True


def _compiled_with_rule_authority(event: dict) -> tuple[CompiledWeatherEvent, str]:
    if not isinstance(event, dict):
        raise WeatherResultLagError("RESULT_LAG_EVENT_INVALID")
    raw = compile_weather_event(event)
    authority = compile_temperature_rule_authority(event, raw)
    compiled = apply_rule_authority(raw, authority)
    if compiled.source_family != SOURCE_NWS_WRH:
        raise WeatherResultLagError("RESULT_LAG_SOURCE_UNSUPPORTED")
    if compiled.family not in {DAILY_HIGH, DAILY_LOW} or compiled.unit != "F":
        raise WeatherResultLagError("RESULT_LAG_CONTRACT_PROFILE_UNSUPPORTED")
    if compiled.target_date is None or not compiled.partition_shape_complete:
        raise WeatherResultLagError("RESULT_LAG_CONTRACT_PARTITION_UNPROVEN")
    if not authority.rule_semantics_proven or not authority.exactly_one_outcome_proven:
        raise WeatherResultLagError("RESULT_LAG_RULE_AUTHORITY_UNPROVEN")
    # The certified source parser in this lane reconstructs WRH's Hourly Data table.
    # An ALL_TIMES contract is a different observation population and must not borrow
    # Hourly Data settlement authority merely because other rule text is similar.
    if authority.observation_population != "WRH_HOURLY_DATA":
        raise WeatherResultLagError("RESULT_LAG_RULE_OBSERVATION_POPULATION_MISMATCH")
    if authority.precision != "WHOLE_DEGREE_F":
        raise WeatherResultLagError("RESULT_LAG_RULE_PRECISION_MISMATCH")
    if authority.finality_policy != "FIRST_FOLLOWING_DATE_DATAPOINT_OR_NEXT_DAY_2359_ET":
        raise WeatherResultLagError("RESULT_LAG_RULE_FINALITY_POLICY_MISMATCH")
    if authority.correction_policy != "ACCEPT_REVISIONS_UNTIL_FIRST_FOLLOWING_DATE_DATAPOINT":
        raise WeatherResultLagError("RESULT_LAG_RULE_CORRECTION_POLICY_MISMATCH")
    if authority.fallback_policy != "WEATHER_UNDERGROUND_IF_WRH_UNAVAILABLE_BY_NEXT_DAY_2359_ET":
        raise WeatherResultLagError("RESULT_LAG_RULE_FALLBACK_POLICY_MISMATCH")
    if authority.no_data_outcome != "LOWEST_BRACKET":
        raise WeatherResultLagError("RESULT_LAG_RULE_NO_DATA_POLICY_MISMATCH")
    if not compiled.exactly_one_outcome_proven:
        raise WeatherResultLagError("RESULT_LAG_RULE_UPGRADE_FAILED")
    if compiled.financial_authority or authority.financial_authority:
        raise WeatherResultLagError("RESULT_LAG_RULE_AUTHORITY_BOUNDARY_BROKEN")
    rule_sha = _sha({"compiled": compiled.as_dict(), "rule_authority": authority.as_dict()})
    return compiled, rule_sha


def _winning_bucket(compiled: CompiledWeatherEvent, finality: WRHFinalizedRuleState) -> tuple[int, WeatherBucket]:
    if finality.station != str(compiled.station_hint or "").strip().upper():
        raise WeatherResultLagError("RESULT_LAG_FINALITY_STATION_MISMATCH")
    if finality.target_date != compiled.target_date:
        raise WeatherResultLagError("RESULT_LAG_FINALITY_DATE_MISMATCH")
    if not finality.settlement_label_authority or finality.financial_authority:
        raise WeatherResultLagError("RESULT_LAG_FINALITY_AUTHORITY_INVALID")
    if compiled.family == DAILY_HIGH:
        value = int(finality.target_high_f)
    else:
        value = int(finality.target_low_f)
    winners = tuple(bucket for bucket in compiled.buckets if _bucket_contains(bucket, value))
    if len(winners) != 1:
        raise WeatherResultLagError("RESULT_LAG_WINNER_NOT_EXACTLY_ONE")
    winner = winners[0]
    if not winner.trade_open or not winner.yes_token or not winner.condition_id:
        raise WeatherResultLagError("RESULT_LAG_WINNING_BUCKET_NOT_EXECUTABLE")
    return value, winner


def _execution_digest(compiled: CompiledWeatherEvent, snapshot: object, *, source_received_at: float) -> str:
    if not isinstance(snapshot, WeatherExecutionSnapshot):
        raise WeatherResultLagError("RESULT_LAG_CLOB_SNAPSHOT_TYPE_INVALID")
    if snapshot.event_id != compiled.event_id or snapshot.exact_clob is not True or snapshot.financial_authority is not False:
        raise WeatherResultLagError("RESULT_LAG_CLOB_IDENTITY_INVALID")
    started = _finite(snapshot.started_at, "RESULT_LAG_CLOB_STARTED_AT_INVALID")
    finished = _finite(snapshot.finished_at, "RESULT_LAG_CLOB_FINISHED_AT_INVALID")
    if finished < started or started + 1e-9 < source_received_at:
        raise WeatherResultLagError("RESULT_LAG_CLOB_CAUSALITY_INVALID")

    expected_conditions = {bucket.condition_id for bucket in compiled.buckets if bucket.trade_open}
    expected_tokens = {
        token
        for bucket in compiled.buckets if bucket.trade_open
        for token in (bucket.yes_token, bucket.no_token)
        if token
    }
    if set(snapshot.parameters) != expected_conditions or set(snapshot.books) != expected_tokens:
        raise WeatherResultLagError("RESULT_LAG_CLOB_COVERAGE_INVALID")
    for bucket in compiled.buckets:
        if not bucket.trade_open:
            continue
        params = snapshot.parameters.get(bucket.condition_id)
        if params is None or params.condition_id != bucket.condition_id:
            raise WeatherResultLagError("RESULT_LAG_CLOB_PARAMETER_IDENTITY_INVALID")
        if {token for token, _ in params.token_outcomes} != {bucket.yes_token, bucket.no_token}:
            raise WeatherResultLagError("RESULT_LAG_CLOB_PARAMETER_TOKEN_MISMATCH")
        for token in (bucket.yes_token, bucket.no_token):
            book = snapshot.books.get(str(token))
            if book is None or book.token_id != token or book.received_at is None:
                raise WeatherResultLagError("RESULT_LAG_CLOB_BOOK_IDENTITY_INVALID")
            book_received = _finite(book.received_at, "RESULT_LAG_CLOB_BOOK_RECEIPT_INVALID")
            age = finished - book_received
            if age < -1e-9 or age > MAX_BOOK_AGE_SECONDS + 1e-9:
                raise WeatherResultLagError("RESULT_LAG_CLOB_BOOK_STALE")
    return _sha(asdict(snapshot))


def _winning_quote(
    winner: WeatherBucket,
    snapshot: WeatherExecutionSnapshot,
    *,
    policy: WeatherResultLagShadowPolicy,
) -> tuple[float, float, float, int, float, float, float] | None:
    book = snapshot.books[winner.yes_token]
    if book.best_ask is None or book.best_ask_size <= 0.0:
        return None
    ask = float(book.best_ask)
    size = float(book.best_ask_size)
    if not 0.0 < ask < 1.0:
        raise WeatherResultLagError("RESULT_LAG_ASK_INVALID")
    params = snapshot.parameters[winner.condition_id]
    if params.fee_rate > 0.0 and params.taker_only is not True:
        raise WeatherResultLagError("RESULT_LAG_DYNAMIC_FEE_SEMANTICS_UNPROVEN")
    fee = conservative_taker_fee_per_share(ask, params.fee_rate, params.fee_exponent)
    total = ask + fee
    edge = 1.0 - total
    required_size = max(policy.min_visible_shares, float(params.minimum_order_size))
    if size + 1e-12 < required_size or edge + 1e-12 < policy.min_edge_per_share:
        return None
    return ask, size, params.fee_rate, params.fee_exponent, fee, total, edge


async def evaluate_wrh_official_result_lag(
    event: dict,
    previous_snapshot: WRHSourceSnapshot,
    current_snapshot: WRHSourceSnapshot,
    *,
    clob: ExactEventSnapshotClient,
    policy: WeatherResultLagShadowPolicy | None = None,
) -> tuple[WeatherOfficialResultLagCandidate | None, WeatherExecutionSnapshot | None]:
    """Return one deterministic shadow candidate only after a surviving exact recheck."""
    frozen = policy or WeatherResultLagShadowPolicy()
    if not isinstance(frozen, WeatherResultLagShadowPolicy):
        raise WeatherResultLagError("RESULT_LAG_POLICY_TYPE_INVALID")
    compiled, rule_sha = _compiled_with_rule_authority(event)
    try:
        finality = certify_wrh_first_following_transition(
            previous_snapshot,
            current_snapshot,
            policy=WRH_CALIBRATION_FINALITY_POLICY,
        )
    except Exception as exc:
        code = getattr(exc, "code", type(exc).__name__)
        raise WeatherResultLagError(f"RESULT_LAG_FINALITY_INVALID:{code}") from exc
    value, winner = _winning_bucket(compiled, finality)
    if finality.current_snapshot_sha256 != current_snapshot.evidence_sha256:
        raise WeatherResultLagError("RESULT_LAG_CURRENT_SOURCE_DIGEST_MISMATCH")

    first = await clob.exact_event_snapshot(compiled)
    first_sha = _execution_digest(compiled, first, source_received_at=current_snapshot.received_at)
    if _winning_quote(winner, first, policy=frozen) is None:
        return None, None

    confirmed = await clob.exact_event_snapshot(compiled)
    confirmed_sha = _execution_digest(compiled, confirmed, source_received_at=current_snapshot.received_at)
    if confirmed.started_at + 1e-9 < first.finished_at:
        raise WeatherResultLagError("RESULT_LAG_RECHECK_TIME_ORDER_INVALID")
    quote = _winning_quote(winner, confirmed, policy=frozen)
    if quote is None:
        return None, None
    ask, size, rate, exponent, fee, total, edge = quote

    shell = WeatherOfficialResultLagCandidate(
        version=WEATHER_RESULT_LAG_VERSION,
        policy_id=frozen.policy_id,
        policy_sha256=frozen.policy_sha256,
        event_id=compiled.event_id,
        market_id=winner.market_id,
        condition_id=winner.condition_id,
        token_id=str(winner.yes_token),
        station=finality.station,
        target_date=finality.target_date.isoformat(),
        family=compiled.family,
        finalized_value_f=value,
        finality_evidence_sha256=finality.finality_evidence_sha256,
        source_snapshot_sha256=current_snapshot.evidence_sha256,
        rule_evidence_sha256=rule_sha,
        first_clob_evidence_sha256=first_sha,
        confirmed_clob_evidence_sha256=confirmed_sha,
        executable_ask=ask,
        visible_ask_shares=size,
        fee_rate=rate,
        fee_exponent=exponent,
        conservative_fee_per_share=fee,
        total_cost_per_share=total,
        payout_per_share=1.0,
        deterministic_edge_per_share=edge,
        minimum_required_edge_per_share=frozen.min_edge_per_share,
        confirmed_at=confirmed.finished_at,
        candidate_evidence_sha256="0" * 64,
    )
    candidate = WeatherOfficialResultLagCandidate(
        **{
            name: getattr(shell, name)
            for name, definition in shell.__dataclass_fields__.items()
            if definition.init and name != "candidate_evidence_sha256"
        },
        candidate_evidence_sha256=_sha(_candidate_digest_payload(shell)),
    )
    return candidate, confirmed


async def evaluate_wrh_official_result_lag_for_w7(
    trigger: WeatherW7SourceUpdateTrigger,
    event: dict,
    previous_snapshot: WRHSourceSnapshot,
    current_snapshot: WRHSourceSnapshot,
    *,
    clob: ExactEventSnapshotClient,
    policy: WeatherResultLagShadowPolicy | None = None,
) -> WeatherW7ConfirmedShadowCandidate | None:
    """W7 evaluator: material finality trigger -> deterministic result-lag confirmation."""
    if not isinstance(trigger, WeatherW7SourceUpdateTrigger):
        raise WeatherResultLagError("RESULT_LAG_W7_TRIGGER_TYPE_INVALID")
    if trigger.change_kind != CHANGE_FINALITY_TRANSITION:
        return None
    if trigger.current_snapshot_sha256 != current_snapshot.evidence_sha256:
        raise WeatherResultLagError("RESULT_LAG_W7_SOURCE_TRIGGER_MISMATCH")
    compiled, _ = _compiled_with_rule_authority(event)
    if (
        trigger.event_id != compiled.event_id
        or trigger.station != str(compiled.station_hint or "").strip().upper()
        or trigger.target_date != compiled.target_date
        or trigger.family != compiled.family
    ):
        raise WeatherResultLagError("RESULT_LAG_W7_CONTRACT_TRIGGER_MISMATCH")

    candidate, confirmed = await evaluate_wrh_official_result_lag(
        event,
        previous_snapshot,
        current_snapshot,
        clob=clob,
        policy=policy,
    )
    if candidate is None or confirmed is None:
        return None
    try:
        return build_w7_confirmed_shadow_candidate(
            trigger,
            confirmed,
            candidate_evidence_sha256=candidate.candidate_evidence_sha256,
            candidate_source_snapshot_sha256=candidate.source_snapshot_sha256,
        )
    except WeatherW7SourceLatencyError as exc:
        raise WeatherResultLagError(f"RESULT_LAG_W7_CONFIRMATION_INVALID:{exc.code}") from exc
