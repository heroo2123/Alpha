from __future__ import annotations

"""Exact semantic sidecar for same-day weather research.

The broad ``CompiledWeatherEvent`` intentionally predates the three-layer model and
does not carry every contract rule needed to interpret observations.  This sidecar
binds a strict compiled event to its certified rule-authority fields so a stored or
replayed same-day decision cannot forget the observation population, precision,
fallback, finality or correction policy.

Current Layer-1 WRH source code implements one narrow population: the pinned
Fahrenheit ``WRH_HOURLY_DATA`` table semantics.  ``WRH_ALL_TIMES`` and Celsius
contracts remain explicitly adapter-blocked rather than being silently coerced onto
that population.  Adapter capability is not itself settlement or delivery authority.
"""

import hashlib
import json
from dataclasses import asdict, dataclass, field

from .weather_only_contracts import CompiledWeatherEvent, SOURCE_NWS_WRH
from .weather_only_rules import TemperatureRuleAuthority


SAME_DAY_CONTRACT_VERSION = "weather_same_day_contract_semantics_v1_population_bound"
SUPPORTED_LAYER1_POPULATION = "WRH_HOURLY_DATA"
SUPPORTED_LAYER1_UNIT = "F"
SUPPORTED_LAYER1_PRECISION = "WHOLE_DEGREE_F"


class SameDayContractError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


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
        raise SameDayContractError("SAME_DAY_CONTRACT_JSON_INVALID") from None


def _sha(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _identity(value: object, code: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise SameDayContractError(code)
    return text


@dataclass(frozen=True, slots=True)
class SameDayContractSemantics:
    version: str
    event_id: str
    event_slug: str
    family: str
    target_date: str
    station: str
    unit: str
    source_family: str
    source_urls: tuple[str, ...]
    rule_authority_version: str
    rule_profile: str
    statistic: str
    observation_population: str
    precision: str
    fallback_policy: str
    finality_policy: str
    correction_policy: str
    no_data_outcome: str
    bucket_partition_sha256: str
    semantics_sha256: str
    layer1_adapter_capable: bool
    layer1_adapter_block_reason: str | None
    rule_semantics_proven: bool = field(init=False, default=True)
    exactly_one_outcome_proven: bool = field(init=False, default=True)
    settlement_value_adapter_ready: bool = field(init=False, default=False)
    same_day_delivery_authority: bool = field(init=False, default=False)
    settlement_authority: bool = field(init=False, default=False)
    financial_authority: bool = field(init=False, default=False)

    def as_dict(self) -> dict:
        return asdict(self)


def _bucket_payload(compiled: CompiledWeatherEvent) -> list[dict]:
    return [
        {
            "market_id": bucket.market_id,
            "condition_id": bucket.condition_id,
            "label": bucket.label,
            "lower": bucket.lower,
            "upper": bucket.upper,
            "unit": bucket.unit,
            "yes_token": bucket.yes_token,
            "no_token": bucket.no_token,
        }
        for bucket in compiled.buckets
    ]


def _semantics_payload(value: SameDayContractSemantics) -> dict:
    payload = value.as_dict()
    payload.pop("semantics_sha256", None)
    return payload


def build_same_day_contract_semantics(
    compiled: CompiledWeatherEvent,
    authority: TemperatureRuleAuthority,
) -> SameDayContractSemantics:
    if not isinstance(compiled, CompiledWeatherEvent):
        raise SameDayContractError("SAME_DAY_CONTRACT_COMPILED_TYPE_INVALID")
    if not isinstance(authority, TemperatureRuleAuthority):
        raise SameDayContractError("SAME_DAY_CONTRACT_AUTHORITY_TYPE_INVALID")
    if (
        not compiled.partition_shape_complete
        or not compiled.exactly_one_outcome_proven
        or compiled.target_date is None
        or not compiled.station_hint
        or not compiled.buckets
        or compiled.financial_authority
    ):
        raise SameDayContractError("SAME_DAY_CONTRACT_COMPILED_UNPROVEN")
    if (
        not authority.rule_semantics_proven
        or not authority.exactly_one_outcome_proven
        or authority.financial_authority
        or authority.family != compiled.family
        or authority.source_family != compiled.source_family
    ):
        raise SameDayContractError("SAME_DAY_CONTRACT_RULE_AUTHORITY_UNPROVEN")

    population = _identity(
        authority.observation_population,
        "SAME_DAY_CONTRACT_OBSERVATION_POPULATION_MISSING",
    )
    precision = _identity(authority.precision, "SAME_DAY_CONTRACT_PRECISION_MISSING")
    statistic = _identity(authority.statistic, "SAME_DAY_CONTRACT_STATISTIC_MISSING")
    fallback = _identity(authority.fallback_policy, "SAME_DAY_CONTRACT_FALLBACK_MISSING")
    finality = _identity(authority.finality_policy, "SAME_DAY_CONTRACT_FINALITY_MISSING")
    correction = _identity(authority.correction_policy, "SAME_DAY_CONTRACT_CORRECTION_MISSING")
    no_data = _identity(authority.no_data_outcome, "SAME_DAY_CONTRACT_NO_DATA_OUTCOME_MISSING")

    capable = (
        compiled.source_family == SOURCE_NWS_WRH
        and population == SUPPORTED_LAYER1_POPULATION
        and compiled.unit == SUPPORTED_LAYER1_UNIT
        and precision == SUPPORTED_LAYER1_PRECISION
    )
    if capable:
        block_reason = None
    elif compiled.source_family != SOURCE_NWS_WRH:
        block_reason = "LAYER1_SOURCE_FAMILY_ADAPTER_UNSUPPORTED"
    elif population != SUPPORTED_LAYER1_POPULATION:
        block_reason = f"LAYER1_OBSERVATION_POPULATION_UNSUPPORTED:{population}"
    elif compiled.unit != SUPPORTED_LAYER1_UNIT:
        block_reason = f"LAYER1_UNIT_ADAPTER_UNSUPPORTED:{compiled.unit}"
    else:
        block_reason = f"LAYER1_PRECISION_ADAPTER_UNSUPPORTED:{precision}"

    bucket_sha = _sha(_bucket_payload(compiled))
    shell = SameDayContractSemantics(
        version=SAME_DAY_CONTRACT_VERSION,
        event_id=compiled.event_id,
        event_slug=compiled.event_slug,
        family=compiled.family,
        target_date=compiled.target_date.isoformat(),
        station=str(compiled.station_hint).upper(),
        unit=str(compiled.unit),
        source_family=compiled.source_family,
        source_urls=tuple(compiled.source_urls),
        rule_authority_version=authority.version,
        rule_profile=authority.profile,
        statistic=statistic,
        observation_population=population,
        precision=precision,
        fallback_policy=fallback,
        finality_policy=finality,
        correction_policy=correction,
        no_data_outcome=no_data,
        bucket_partition_sha256=bucket_sha,
        semantics_sha256="0" * 64,
        layer1_adapter_capable=capable,
        layer1_adapter_block_reason=block_reason,
    )
    return SameDayContractSemantics(
        **{
            name: getattr(shell, name)
            for name, definition in shell.__dataclass_fields__.items()
            if definition.init and name != "semantics_sha256"
        },
        semantics_sha256=_sha(_semantics_payload(shell)),
    )


def verify_same_day_contract_semantics(
    value: object,
    compiled: CompiledWeatherEvent,
) -> SameDayContractSemantics:
    if not isinstance(value, SameDayContractSemantics):
        raise SameDayContractError("SAME_DAY_CONTRACT_SEMANTICS_TYPE_INVALID")
    if value.semantics_sha256 != _sha(_semantics_payload(value)):
        raise SameDayContractError("SAME_DAY_CONTRACT_SEMANTICS_DIGEST_MISMATCH")
    if value.event_id != compiled.event_id or value.bucket_partition_sha256 != _sha(_bucket_payload(compiled)):
        raise SameDayContractError("SAME_DAY_CONTRACT_PARTITION_IDENTITY_MISMATCH")
    if (
        value.station != str(compiled.station_hint or "").upper()
        or value.target_date != compiled.target_date.isoformat()
        or value.family != compiled.family
        or value.unit != compiled.unit
        or value.source_family != compiled.source_family
    ):
        raise SameDayContractError("SAME_DAY_CONTRACT_SEMANTIC_IDENTITY_MISMATCH")
    if any((
        not value.rule_semantics_proven,
        not value.exactly_one_outcome_proven,
        value.settlement_value_adapter_ready,
        value.same_day_delivery_authority,
        value.settlement_authority,
        value.financial_authority,
    )):
        raise SameDayContractError("SAME_DAY_CONTRACT_AUTHORITY_BOUNDARY_BROKEN")
    return value
