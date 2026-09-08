from __future__ import annotations

"""Typed rule profiles for a narrow Polymarket WRH daily-high contract family.

A source-family match is not enough to certify settlement mechanics. This compiler
recognizes only the currently observed WRH daily-high template and records the parts
that materially change the payout: observation population, unit/precision, fallback
trigger, no-data outcome, finality and correction cutoff. Unknown variants fail
closed and remain research-unsupported.
"""

import re
from dataclasses import asdict, dataclass


WRH_DAILY_HIGH_RULE_PROFILE_VERSION = "WRH_DAILY_HIGH_RULE_PROFILE_V1"


def _norm(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().lower()


@dataclass(frozen=True, slots=True)
class WRHDailyHighRuleProfile:
    version: str
    observation_population: str
    observation_column: str
    unit: str
    precision: str
    primary_source_family: str
    fallback_source_family: str
    fallback_trigger: str
    fallback_deadline_timezone: str
    fallback_deadline_local_time: str
    fallback_deadline_day_offset: int
    no_data_outcome: str
    finality: str
    correction_policy: str

    def as_metadata(self) -> dict:
        return asdict(self)


def compile_wrh_daily_high_profile(description: object, unit: str) -> WRHDailyHighRuleProfile | None:
    """Compile the currently documented WRH/Wunderground daily-high rule template.

    The caller separately proves the primary WRH URL/station and exact contract day.
    Here we prove the rule mechanics. We intentionally do not infer missing clauses.
    """
    text = _norm(description)
    wanted_unit = str(unit or "").upper()
    if wanted_unit not in {"F", "C"} or not text:
        return None

    # Primary statistic: highest value from WRH's Temp column for the observation day.
    if "highest reading" not in text or '"temp" column' not in text or "all times on this day" not in text:
        return None

    # Observation population. Current Chicago-style rules explicitly override the
    # default all-times display with WRH's Hourly Data view; London-style rules do not.
    has_hourly = "hourly data" in text and "show hourly data" in text
    observation_population = "WRH_HOURLY_DATA" if has_hourly else "WRH_ALL_TIMES"

    # Exact fallback/no-data semantics. A vague mention of Wunderground is not enough.
    if "weather underground daily observations" not in text:
        return None
    if "11:59 pm et" not in text or "day following the observation date" not in text:
        return None
    if "no data" not in text or "lowest bracket" not in text:
        return None

    # Exact finality and revision window. These prevent later corrected history from
    # being substituted for what the contract would actually have considered final.
    if "first data point for the following date" not in text or "whichever comes first" not in text:
        return None
    if "revisions" not in text or "first datapoint for the following date" not in text:
        return None
    if "after which any alterations will not be considered" not in text:
        return None

    if "whole degrees" not in text:
        return None
    if wanted_unit == "F" and "whole degrees fahrenheit" not in text:
        return None
    if wanted_unit == "C" and "whole degrees celsius" not in text:
        return None

    return WRHDailyHighRuleProfile(
        version=WRH_DAILY_HIGH_RULE_PROFILE_VERSION,
        observation_population=observation_population,
        observation_column="Temp",
        unit=wanted_unit,
        precision="whole_degree",
        primary_source_family="NWS_WRH_TIMESERIES",
        fallback_source_family="WEATHER_UNDERGROUND_DAILY_OBSERVATIONS",
        fallback_trigger="PRIMARY_UNAVAILABLE_BY_DEADLINE",
        fallback_deadline_timezone="America/New_York",
        fallback_deadline_local_time="23:59",
        fallback_deadline_day_offset=1,
        no_data_outcome="LOWEST_BRACKET",
        finality="FIRST_FOLLOWING_DATE_DATAPOINT_OR_FALLBACK_DEADLINE",
        correction_policy="ACCEPT_UNTIL_FIRST_FOLLOWING_DATE_DATAPOINT",
    )
