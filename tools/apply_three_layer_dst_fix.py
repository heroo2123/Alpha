from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"missing patch anchor in {path}: {old[:80]!r}")
    if text.count(old) != 1:
        raise SystemExit(f"non-unique patch anchor in {path}: {text.count(old)}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


GEFS = "polymarket_scanner/weather_only_gefs_hourly.py"
GUARDED = "polymarket_scanner/weather_only_three_layer_guarded.py"

replace_once(
    GEFS,
    "from datetime import date, datetime, timedelta\n",
    "from datetime import date, datetime, timedelta, timezone\n",
)

old_parser = '''def _parse_local_valid_times(raw_times: object, *, target: date, timezone_name: str) -> tuple[float, ...]:
    if not isinstance(raw_times, list) or not raw_times:
        raise GEFSHourlyError("GEFS_HOURLY_TIME_SERIES_INVALID")
    try:
        zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        raise GEFSHourlyError("GEFS_HOURLY_TIMEZONE_INVALID") from None

    parsed: list[float] = []
    seen_raw: set[str] = set()
    for raw in raw_times:
        if not isinstance(raw, str) or not raw.strip() or raw != raw.strip():
            raise GEFSHourlyError("GEFS_HOURLY_TIME_VALUE_INVALID")
        if raw in seen_raw:
            # Local timestamps without offsets cannot disambiguate a repeated DST
            # wall-clock hour. Refuse rather than silently selecting fold=0.
            raise GEFSHourlyError("GEFS_HOURLY_TIME_DUPLICATE_OR_DST_AMBIGUOUS")
        seen_raw.add(raw)
        try:
            value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            raise GEFSHourlyError("GEFS_HOURLY_TIME_VALUE_INVALID") from None
        if value.tzinfo is None or value.utcoffset() is None:
            value = value.replace(tzinfo=zone)
        local = value.astimezone(zone)
        if local.date() != target:
            raise GEFSHourlyError("GEFS_HOURLY_TIME_OUTSIDE_TARGET_LOCAL_DATE")
        parsed.append(local.timestamp())

    if len(set(parsed)) != len(parsed):
        raise GEFSHourlyError("GEFS_HOURLY_TIME_INSTANT_DUPLICATE")
    if parsed != sorted(parsed):
        raise GEFSHourlyError("GEFS_HOURLY_TIME_NOT_MONOTONIC")
    for before, after in zip(parsed, parsed[1:]):
        if abs((after - before) - GEFS_HOURLY_STEP_SECONDS) > 1e-6:
            raise GEFSHourlyError("GEFS_HOURLY_GRID_NOT_EXACT_HOURLY")

    target_start, target_end = _local_target_bounds(target, timezone_name)
    if abs(parsed[0] - target_start) > 1e-6:
        raise GEFSHourlyError("GEFS_HOURLY_TARGET_START_MISSING")
    if abs((parsed[-1] + GEFS_HOURLY_STEP_SECONDS) - target_end) > 1e-6:
        raise GEFSHourlyError("GEFS_HOURLY_TARGET_END_COVERAGE_MISSING")
    return tuple(parsed)
'''

new_parser = '''def _raw_time_format(raw_times: object) -> str:
    if not isinstance(raw_times, list) or not raw_times:
        raise GEFSHourlyError("GEFS_HOURLY_TIME_SERIES_INVALID")
    if all(isinstance(raw, str) and raw.strip() and raw == raw.strip() for raw in raw_times):
        return "iso8601"
    if all(
        not isinstance(raw, bool)
        and isinstance(raw, (int, float))
        and math.isfinite(float(raw))
        for raw in raw_times
    ):
        return "unixtime"
    raise GEFSHourlyError("GEFS_HOURLY_TIME_VALUE_INVALID")


def _parse_local_valid_times(raw_times: object, *, target: date, timezone_name: str) -> tuple[float, ...]:
    time_format = _raw_time_format(raw_times)
    try:
        zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        raise GEFSHourlyError("GEFS_HOURLY_TIMEZONE_INVALID") from None

    parsed: list[float] = []
    seen_raw: set[object] = set()
    for raw in raw_times:
        if raw in seen_raw:
            raise GEFSHourlyError("GEFS_HOURLY_TIME_DUPLICATE_OR_DST_AMBIGUOUS")
        seen_raw.add(raw)
        if time_format == "unixtime":
            epoch = _finite(raw, "GEFS_HOURLY_TIME_VALUE_INVALID")
            try:
                value = datetime.fromtimestamp(epoch, tz=timezone.utc)
            except (OSError, OverflowError, ValueError):
                raise GEFSHourlyError("GEFS_HOURLY_TIME_VALUE_INVALID") from None
        else:
            try:
                value = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            except ValueError:
                raise GEFSHourlyError("GEFS_HOURLY_TIME_VALUE_INVALID") from None
            if value.tzinfo is None or value.utcoffset() is None:
                value = value.replace(tzinfo=zone)
        local = value.astimezone(zone)
        if local.date() != target:
            raise GEFSHourlyError("GEFS_HOURLY_TIME_OUTSIDE_TARGET_LOCAL_DATE")
        parsed.append(local.timestamp())

    if len(set(parsed)) != len(parsed):
        raise GEFSHourlyError("GEFS_HOURLY_TIME_INSTANT_DUPLICATE")
    if parsed != sorted(parsed):
        raise GEFSHourlyError("GEFS_HOURLY_TIME_NOT_MONOTONIC")
    for before, after in zip(parsed, parsed[1:]):
        if abs((after - before) - GEFS_HOURLY_STEP_SECONDS) > 1e-6:
            raise GEFSHourlyError("GEFS_HOURLY_GRID_NOT_EXACT_HOURLY")

    target_start, target_end = _local_target_bounds(target, timezone_name)
    if abs(parsed[0] - target_start) > 1e-6:
        raise GEFSHourlyError("GEFS_HOURLY_TARGET_START_MISSING")
    if abs((parsed[-1] + GEFS_HOURLY_STEP_SECONDS) - target_end) > 1e-6:
        raise GEFSHourlyError("GEFS_HOURLY_TARGET_END_COVERAGE_MISSING")
    return tuple(parsed)
'''
replace_once(GEFS, old_parser, new_parser)

replace_once(
    GEFS,
    '''    valid_times = _parse_local_valid_times(
        hourly.get("time"), target=target_date, timezone_name=timezone
    )
''',
    '''    raw_times = hourly.get("time")
    expected_time_unit = _raw_time_format(raw_times)
    valid_times = _parse_local_valid_times(
        raw_times, target=target_date, timezone_name=timezone
    )
''',
)
replace_once(
    GEFS,
    '''    if str(units.get("time") or "") != "iso8601":
        raise GEFSHourlyError("GEFS_HOURLY_TIME_UNIT_INVALID")
''',
    '''    if str(units.get("time") or "") != expected_time_unit:
        raise GEFSHourlyError("GEFS_HOURLY_TIME_UNIT_INVALID")
''',
)

# The base client remains useful in tests and research utilities; pin it too.
replace_once(
    GEFS,
    '''            "temperature_unit": unit_name,
            "timezone": timezone,
''',
    '''            "temperature_unit": unit_name,
            "timeformat": "unixtime",
            "timezone": timezone,
''',
)

# The deployed guarded client must request unambiguous absolute instants on DST folds.
replace_once(
    GUARDED,
    '''            "temperature_unit": "fahrenheit" if unit == "F" else "celsius",
            "timezone": timezone,
''',
    '''            "temperature_unit": "fahrenheit" if unit == "F" else "celsius",
            "timeformat": "unixtime",
            "timezone": timezone,
''',
)
