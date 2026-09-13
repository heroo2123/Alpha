from __future__ import annotations

"""Versioned rule-semantic certification for recurring weather temperature events.

This layer proves only contract semantics needed for paper structural/forecast
reasoning.  V4 treats parent prose as insufficient by itself: every child must remain
coherent with the same statistic/source/station and must not introduce an override,
negation or obsolete-rule clause.  Unknown or conflicting evidence fails closed.
"""

import re
from dataclasses import asdict, dataclass, replace
from urllib.parse import parse_qs, urlparse

from .weather_only_contracts import (
    DAILY_HIGH,
    DAILY_LOW,
    SOURCE_HKO,
    SOURCE_NWS_WRH,
    CompiledWeatherEvent,
)


RULE_AUTHORITY_VERSION = "weather_temperature_rule_authority_v4_per_child_common_function"


def _norm(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().lower()


def _event_rules(event: dict) -> str:
    parts = [event.get("description"), event.get("resolutionSource")]
    for row in event.get("markets") or []:
        if isinstance(row, dict):
            parts.extend((row.get("description"), row.get("resolutionSource")))
    return _norm(" ".join(str(x or "") for x in parts))


def _contains_all(text: str, phrases: tuple[str, ...]) -> bool:
    return all(phrase in text for phrase in phrases)


def _wrh_station_from_url(raw: object) -> str | None:
    try:
        parsed = urlparse(str(raw or ""))
    except Exception:
        return None
    host = str(parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme.lower() != "https" or host not in {"weather.gov", "www.weather.gov"}:
        return None
    if parsed.path.rstrip("/").lower() != "/wrh/timeseries":
        return None
    query = parse_qs(parsed.query)
    values = [value for key, rows in query.items() if key.lower() == "site" for value in rows]
    if len(values) != 1:
        return None
    station = str(values[0]).strip().upper()
    return station if re.fullmatch(r"[A-Z0-9]{4}", station) else None


def _urls(text: str) -> tuple[str, ...]:
    return tuple(re.findall(r"https?://[^\s<>\"')]+", str(text or ""), flags=re.I))


def _child_semantic_reasons(event: dict, compiled: CompiledWeatherEvent) -> list[str]:
    """Reject child-level evidence that breaks one common resolution function."""
    reasons: list[str] = []
    wanted = "highest" if compiled.family == DAILY_HIGH else "lowest"
    opposite = "lowest" if wanted == "highest" else "highest"
    parent_text = _norm(" ".join(str(event.get(key) or "") for key in ("title", "description", "resolutionSource")))
    if re.search(r"\b(?:obsolete|superseded)\s+rules?\b.*\bdo\s+not\s+apply\b", parent_text):
        reasons.append("RULE_TEXT_EXPLICITLY_NEGATED")

    for index, row in enumerate(event.get("markets") or []):
        if not isinstance(row, dict):
            continue
        question = _norm(row.get("question"))
        description = _norm(row.get("description"))
        resolution = str(row.get("resolutionSource") or "")
        combined = " ".join(value for value in (question, description, _norm(resolution)) if value)

        if re.search(rf"\b{opposite}\s+temperature\b|\b{opposite}\s+reading\b", combined):
            reasons.append(f"CHILD_{index}_STATISTIC_CONFLICT")
        if re.search(r"\b(?:obsolete|superseded)\s+rules?\b.*\bdo\s+not\s+apply\b", combined):
            reasons.append(f"CHILD_{index}_RULE_TEXT_NEGATED")
        if re.search(r"\bresolves?\s+(?:to\s+)?yes\b", combined) or "regardless of" in combined:
            reasons.append(f"CHILD_{index}_OUTCOME_OVERRIDE")

        if compiled.source_family == SOURCE_NWS_WRH:
            child_urls = _urls(" ".join((description, resolution)))
            child_wrh_mentions = [raw for raw in child_urls if "/wrh/timeseries" in raw.lower() or "weather.gov" in raw.lower()]
            for raw in child_wrh_mentions:
                station = _wrh_station_from_url(raw)
                if station is None:
                    reasons.append(f"CHILD_{index}_UNTRUSTED_WRH_SOURCE")
                elif compiled.station_hint and station != compiled.station_hint:
                    reasons.append(f"CHILD_{index}_STATION_CONFLICT")
    return reasons


@dataclass(frozen=True, slots=True)
class TemperatureRuleAuthority:
    version: str
    profile: str
    family: str
    source_family: str
    statistic: str | None
    observation_population: str | None
    precision: str | None
    fallback_policy: str | None
    finality_policy: str | None
    correction_policy: str | None
    no_data_outcome: str | None
    rule_semantics_proven: bool
    exactly_one_outcome_proven: bool
    settlement_value_adapter_ready: bool
    financial_authority: bool
    rejection_reasons: tuple[str, ...]

    def as_dict(self) -> dict:
        return asdict(self)


def _nws_profile(event: dict, compiled: CompiledWeatherEvent) -> TemperatureRuleAuthority:
    text = _event_rules(event)
    wanted_stat = "highest" if compiled.family == DAILY_HIGH else "lowest"
    reasons: list[str] = []
    reasons.extend(_child_semantic_reasons(event, compiled))

    if not compiled.station_hint:
        reasons.append("NWS_STATION_UNPROVEN_OR_CONFLICT")
    if f"{wanted_stat} reading" not in text:
        reasons.append("NWS_STATISTIC_RULE_MISSING")
    if '"temp" column' not in text or "all times on this day" not in text:
        # Some recurring contracts explicitly choose Hourly Data instead.
        if not ("hourly data" in text and "show hourly data" in text):
            reasons.append("NWS_OBSERVATION_POPULATION_UNPROVEN")

    hourly = "hourly data" in text and "show hourly data" in text
    population = "WRH_HOURLY_DATA" if hourly else "WRH_ALL_TIMES"

    if not _contains_all(text, (
        "weather underground daily observations table",
        "11:59 pm et",
        "day following the observation date",
    )):
        reasons.append("NWS_FALLBACK_POLICY_UNPROVEN")
    if "no data" not in text or "lowest bracket" not in text:
        reasons.append("NWS_NO_DATA_RULE_UNPROVEN")
    if not _contains_all(text, (
        "first data point for the following date",
        "whichever comes first",
    )):
        reasons.append("NWS_FINALITY_RULE_UNPROVEN")
    if (
        "revisions" not in text
        or "after which any alterations will not be considered" not in text
        or not (
            "first datapoint for the following date" in text
            or "first data point for the following date" in text
        )
    ):
        reasons.append("NWS_CORRECTION_RULE_UNPROVEN")

    unit_word = "fahrenheit" if compiled.unit == "F" else "celsius" if compiled.unit == "C" else ""
    if not unit_word or "whole degrees" not in text or unit_word not in text:
        reasons.append("NWS_PRECISION_RULE_UNPROVEN")
    if not compiled.partition_shape_complete:
        reasons.append("BUCKET_PARTITION_SHAPE_UNPROVEN")

    reasons = list(dict.fromkeys(reasons))
    proven = not reasons
    return TemperatureRuleAuthority(
        version=RULE_AUTHORITY_VERSION,
        profile="NWS_WRH_DAILY_EXTREME_CURRENT_TEMPLATE_V4",
        family=compiled.family,
        source_family=compiled.source_family,
        statistic=f"DAILY_{wanted_stat.upper()}_TEMP",
        observation_population=population if "NWS_OBSERVATION_POPULATION_UNPROVEN" not in reasons else None,
        precision=f"WHOLE_DEGREE_{compiled.unit}" if "NWS_PRECISION_RULE_UNPROVEN" not in reasons else None,
        fallback_policy="WEATHER_UNDERGROUND_IF_WRH_UNAVAILABLE_BY_NEXT_DAY_2359_ET" if "NWS_FALLBACK_POLICY_UNPROVEN" not in reasons else None,
        finality_policy="FIRST_FOLLOWING_DATE_DATAPOINT_OR_NEXT_DAY_2359_ET" if "NWS_FINALITY_RULE_UNPROVEN" not in reasons else None,
        correction_policy="ACCEPT_REVISIONS_UNTIL_FIRST_FOLLOWING_DATE_DATAPOINT" if "NWS_CORRECTION_RULE_UNPROVEN" not in reasons else None,
        no_data_outcome="LOWEST_BRACKET" if "NWS_NO_DATA_RULE_UNPROVEN" not in reasons else None,
        rule_semantics_proven=proven,
        exactly_one_outcome_proven=proven,
        settlement_value_adapter_ready=False,
        financial_authority=False,
        rejection_reasons=tuple(reasons),
    )


def _hko_profile(event: dict, compiled: CompiledWeatherEvent) -> TemperatureRuleAuthority:
    text = _event_rules(event)
    wanted = "max" if compiled.family == DAILY_HIGH else "min"
    semantic_reasons: list[str] = []
    semantic_reasons.extend(_child_semantic_reasons(event, compiled))

    if "hong kong observatory" not in text:
        semantic_reasons.append("HKO_AUTHORITY_RULE_MISSING")
    if f"absolute daily {wanted} (deg. c)" not in text or "daily extract" not in text:
        semantic_reasons.append("HKO_STATISTIC_RULE_UNPROVEN")
    if not _contains_all(text, ("11:59 pm et", "seventh day following the observation date")):
        semantic_reasons.append("HKO_DEADLINE_RULE_UNPROVEN")
    if "no data" not in text or "lowest bracket" not in text:
        semantic_reasons.append("HKO_NO_DATA_RULE_UNPROVEN")
    if not ("once data for this date has been published" in text and "whichever comes first" in text):
        semantic_reasons.append("HKO_FINALITY_RULE_UNPROVEN")
    if "one decimal place" not in text:
        semantic_reasons.append("HKO_PRECISION_RULE_UNPROVEN")
    if not ("revisions" in text and "after data is initially published" in text and "will not be considered" in text):
        semantic_reasons.append("HKO_CORRECTION_RULE_UNPROVEN")
    if compiled.unit != "C":
        semantic_reasons.append("HKO_UNIT_MUST_BE_C")
    if not compiled.partition_shape_complete:
        semantic_reasons.append("BUCKET_PARTITION_SHAPE_UNPROVEN")

    semantic_reasons = list(dict.fromkeys(semantic_reasons))
    structural_reasons = list(semantic_reasons)
    if "HKO_PRECISION_RULE_UNPROVEN" not in semantic_reasons:
        structural_reasons.append("HKO_DECIMAL_BUCKET_MAPPING_UNPROVEN")

    semantics_proven = not semantic_reasons
    exactly_one = not structural_reasons
    return TemperatureRuleAuthority(
        version=RULE_AUTHORITY_VERSION,
        profile="HKO_DAILY_EXTRACT_EXTREME_CURRENT_TEMPLATE_V4",
        family=compiled.family,
        source_family=compiled.source_family,
        statistic=f"ABSOLUTE_DAILY_{wanted.upper()}_C",
        observation_population="HKO_DAILY_EXTRACT",
        precision="ONE_DECIMAL_C" if "HKO_PRECISION_RULE_UNPROVEN" not in semantic_reasons else None,
        fallback_policy="LOWEST_BRACKET_IF_UNPUBLISHED_BY_SEVENTH_DAY_2359_ET" if "HKO_DEADLINE_RULE_UNPROVEN" not in semantic_reasons and "HKO_NO_DATA_RULE_UNPROVEN" not in semantic_reasons else None,
        finality_policy="INITIAL_DAILY_EXTRACT_PUBLICATION_OR_SEVENTH_DAY_2359_ET" if "HKO_FINALITY_RULE_UNPROVEN" not in semantic_reasons else None,
        correction_policy="IGNORE_REVISIONS_AFTER_INITIAL_PUBLICATION" if "HKO_CORRECTION_RULE_UNPROVEN" not in semantic_reasons else None,
        no_data_outcome="LOWEST_BRACKET" if "HKO_NO_DATA_RULE_UNPROVEN" not in semantic_reasons else None,
        rule_semantics_proven=semantics_proven,
        exactly_one_outcome_proven=exactly_one,
        settlement_value_adapter_ready=False,
        financial_authority=False,
        rejection_reasons=tuple(structural_reasons),
    )


def compile_temperature_rule_authority(event: dict, compiled: CompiledWeatherEvent) -> TemperatureRuleAuthority:
    base_reasons: list[str] = []
    if compiled.family not in {DAILY_HIGH, DAILY_LOW}:
        base_reasons.append("UNSUPPORTED_TEMPERATURE_FAMILY")
    if compiled.target_date is None:
        base_reasons.append("TARGET_DATE_UNRESOLVED")
    if compiled.unit is None:
        base_reasons.append("UNIT_UNRESOLVED")
    if compiled.source_family == SOURCE_NWS_WRH and not compiled.station_hint:
        base_reasons.append("NWS_STATION_UNRESOLVED_OR_CONFLICT")
    if not compiled.shadow_supported:
        base_reasons.append("COMPILED_CONTRACT_NOT_SHADOW_SUPPORTED")
    if base_reasons:
        return TemperatureRuleAuthority(
            version=RULE_AUTHORITY_VERSION,
            profile="UNSUPPORTED",
            family=compiled.family,
            source_family=compiled.source_family,
            statistic=None,
            observation_population=None,
            precision=None,
            fallback_policy=None,
            finality_policy=None,
            correction_policy=None,
            no_data_outcome=None,
            rule_semantics_proven=False,
            exactly_one_outcome_proven=False,
            settlement_value_adapter_ready=False,
            financial_authority=False,
            rejection_reasons=tuple(dict.fromkeys(base_reasons)),
        )
    if compiled.source_family == SOURCE_NWS_WRH:
        return _nws_profile(event, compiled)
    if compiled.source_family == SOURCE_HKO:
        return _hko_profile(event, compiled)
    return TemperatureRuleAuthority(
        version=RULE_AUTHORITY_VERSION,
        profile="UNSUPPORTED_SOURCE",
        family=compiled.family,
        source_family=compiled.source_family,
        statistic=None,
        observation_population=None,
        precision=None,
        fallback_policy=None,
        finality_policy=None,
        correction_policy=None,
        no_data_outcome=None,
        rule_semantics_proven=False,
        exactly_one_outcome_proven=False,
        settlement_value_adapter_ready=False,
        financial_authority=False,
        rejection_reasons=("UNSUPPORTED_SOURCE_RULE_PROFILE",),
    )


def apply_rule_authority(
    compiled: CompiledWeatherEvent,
    authority: TemperatureRuleAuthority,
) -> CompiledWeatherEvent:
    """Upgrade exactly-one only after the full common-function proof succeeds."""
    if (
        authority.family != compiled.family
        or authority.source_family != compiled.source_family
        or not authority.exactly_one_outcome_proven
        or not compiled.partition_shape_complete
        or not compiled.shadow_supported
    ):
        return compiled
    return replace(compiled, exactly_one_outcome_proven=True, financial_authority=False)
