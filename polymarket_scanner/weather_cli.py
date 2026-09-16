from __future__ import annotations

"""NWS Daily Climate Report (CLI) parser for deterministic rain-result evidence.

A parsed report is still only one publication. Polymarket's current daily-rain rule
family says the **last** CLI version published before 2:00 PM ET on the following
day controls. ``resolve_daily_rain_after_cutoff`` therefore refuses to declare a
final outcome until that contract cutoff has passed and then selects only matching,
pre-cutoff versions.
"""

import hashlib
import html
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from .weather_rule_tree import DailyRainContract, EASTERN_TZ

CLI_REPORT_VERSION = "nws_cli_daily_precip_parser_v1"

_MONTHS = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
    "JANUARY": 1, "FEBRUARY": 2, "MARCH": 3, "APRIL": 4, "JUNE": 6,
    "JULY": 7, "AUGUST": 8, "SEPTEMBER": 9, "OCTOBER": 10,
    "NOVEMBER": 11, "DECEMBER": 12,
}

_PRODUCT_RE = re.compile(r"(?m)^\s*CLI([A-Z0-9]{3,4})\s*$")
_SUMMARY_RE = re.compile(
    r"CLIMATE\s+SUMMARY\s+FOR\s+(?:THE\s+)?(?:DAY\s+OF\s+)?"
    r"(JANUARY|FEBRUARY|MARCH|APRIL|MAY|JUNE|JULY|AUGUST|SEPTEMBER|OCTOBER|NOVEMBER|DECEMBER|"
    r"JAN|FEB|MAR|APR|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\s+(\d{1,2})\s+(20\d{2})",
    re.I,
)
_ISSUED_RE = re.compile(
    r"(?m)^\s*(\d{1,4})\s+(AM|PM)\s+(EST|EDT)\s+(?:MON|TUE|WED|THU|FRI|SAT|SUN)\s+"
    r"(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\s+(\d{1,2})\s+(20\d{2})\s*$",
    re.I,
)
_PRECIP_SECTION_RE = re.compile(
    r"PRECIPITATION\s*\(IN\)(.*?)(?:\n\s*[A-Z][A-Z /().-]{3,}\n|\Z)",
    re.I | re.S,
)
_YESTERDAY_RE = re.compile(r"(?m)^\s*YESTERDAY\s+(T|MM|M|[-+]?\d+(?:\.\d+)?)\b", re.I)


@dataclass(frozen=True, slots=True)
class CliDailyReport:
    parser_version: str
    issuer: str
    climate_date: date
    issued_at: datetime
    issued_timezone_abbrev: str
    precipitation_inches: float | None
    trace: bool
    missing: bool
    content_sha256: str


@dataclass(frozen=True, slots=True)
class DailyRainFinalEvidence:
    adapter_version: str
    station: str
    issuer: str
    target_date: date
    cutoff_at: datetime
    controlling_report_issued_at: datetime | None
    controlling_report_sha256: str | None
    precipitation_inches: float | None
    trace: bool
    no_figure_by_cutoff: bool
    outcome_yes: bool
    deterministic_after_cutoff: bool


def _plain_text(value: str) -> str:
    text = str(value or "")
    # forecast.weather.gov product pages wrap the report in HTML. Keep line breaks
    # around common block tags before stripping the rest; entity-decode afterwards.
    text = re.sub(r"(?i)<br\s*/?>|</(?:pre|div|p|tr|li|h\d)>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text).replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(line.rstrip() for line in text.splitlines())


def _month(raw: str) -> int | None:
    return _MONTHS.get(str(raw or "").upper())


def _parse_hhmm(raw: str, meridiem: str) -> tuple[int, int] | None:
    digits = str(raw).strip()
    if not digits.isdigit() or not 1 <= len(digits) <= 4:
        return None
    numeric = int(digits)
    hour = numeric // 100
    minute = numeric % 100
    if not 1 <= hour <= 12 or not 0 <= minute <= 59:
        return None
    if meridiem.upper() == "AM":
        hour = 0 if hour == 12 else hour
    elif meridiem.upper() == "PM":
        hour = 12 if hour == 12 else hour + 12
    else:
        return None
    return hour, minute


def parse_cli_daily_report(payload: str, *, expected_issuer: str | None = None) -> CliDailyReport | None:
    text = _plain_text(payload)

    product = _PRODUCT_RE.search(text)
    if not product:
        return None
    issuer = product.group(1).upper()
    if expected_issuer and issuer != str(expected_issuer).strip().upper():
        return None

    summary = _SUMMARY_RE.search(text)
    if not summary:
        return None
    month = _month(summary.group(1))
    if month is None:
        return None
    try:
        climate_date = date(int(summary.group(3)), month, int(summary.group(2)))
    except ValueError:
        return None

    issued = _ISSUED_RE.search(text)
    if not issued:
        return None
    hhmm = _parse_hhmm(issued.group(1), issued.group(2))
    issued_month = _month(issued.group(4))
    if hhmm is None or issued_month is None:
        return None
    try:
        naive = datetime(
            int(issued.group(6)), issued_month, int(issued.group(5)), hhmm[0], hhmm[1]
        )
    except ValueError:
        return None
    eastern = ZoneInfo(EASTERN_TZ)
    aware = naive.replace(tzinfo=eastern)
    abbrev = issued.group(3).upper()
    # Reject impossible EST/EDT labels rather than silently shifting a publication
    # across the contractual 2 PM cutoff.
    if aware.tzname() != abbrev:
        return None

    section = _PRECIP_SECTION_RE.search(text)
    if not section:
        return None
    yesterday = _YESTERDAY_RE.search(section.group(1))
    if not yesterday:
        return None
    raw = yesterday.group(1).upper()
    trace = raw == "T"
    missing = raw in {"MM", "M"}
    amount: float | None
    if trace or missing:
        amount = None
    else:
        try:
            amount = float(raw)
        except ValueError:
            return None
        if not 0 <= amount < 100:
            return None

    digest = hashlib.sha256(text.encode()).hexdigest()
    return CliDailyReport(
        parser_version=CLI_REPORT_VERSION,
        issuer=issuer,
        climate_date=climate_date,
        issued_at=aware,
        issued_timezone_abbrev=abbrev,
        precipitation_inches=amount,
        trace=trace,
        missing=missing,
        content_sha256=digest,
    )


def resolve_daily_rain_after_cutoff(
    contract: DailyRainContract,
    reports: list[CliDailyReport],
    *,
    now: datetime | None = None,
) -> DailyRainFinalEvidence | None:
    """Resolve the supported CLI contract only after its version cutoff has passed."""
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("now must be timezone-aware")

    cutoff_zone = ZoneInfo(contract.report_cutoff_timezone)
    cutoff = contract.report_cutoff_local.replace(tzinfo=cutoff_zone)
    if current.astimezone(timezone.utc) < cutoff.astimezone(timezone.utc):
        return None

    candidates = [
        report for report in reports
        if report.issuer == str(contract.primary.issuer or "").upper()
        and report.climate_date == contract.target_date
        and report.issued_at.astimezone(timezone.utc) <= cutoff.astimezone(timezone.utc)
    ]
    candidates.sort(key=lambda report: report.issued_at.astimezone(timezone.utc))
    controlling = candidates[-1] if candidates else None

    if controlling is None or controlling.missing:
        amount = None
        trace = False if controlling is None else controlling.trace
        yes = False
        no_figure = True
    elif controlling.trace:
        amount = None
        trace = True
        yes = False
        no_figure = False
    else:
        amount = controlling.precipitation_inches
        trace = False
        if amount is None:
            return None
        yes = amount + 1e-12 >= contract.threshold_inches
        no_figure = False

    return DailyRainFinalEvidence(
        adapter_version=contract.adapter_version,
        station=contract.station,
        issuer=str(contract.primary.issuer or "").upper(),
        target_date=contract.target_date,
        cutoff_at=cutoff,
        controlling_report_issued_at=controlling.issued_at if controlling else None,
        controlling_report_sha256=controlling.content_sha256 if controlling else None,
        precipitation_inches=amount,
        trace=trace,
        no_figure_by_cutoff=no_figure,
        outcome_yes=yes,
        deterministic_after_cutoff=True,
    )
