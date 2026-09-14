from __future__ import annotations

from pathlib import Path


def rep(path: str, old: str, new: str, *, expected: int = 1) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    found = text.count(old)
    if found != expected:
        raise SystemExit(f"{path}: expected {expected}, found {found}: {old[:180]!r}")
    p.write_text(text.replace(old, new, expected), encoding="utf-8")


def section(path: str, start: str, end: str, replacement: str) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    a = text.find(start)
    if a < 0:
        raise SystemExit(f"{path}: start marker not found: {start!r}")
    b = text.find(end, a)
    if b < 0:
        raise SystemExit(f"{path}: end marker not found: {end!r}")
    p.write_text(text[:a] + replacement + text[b:], encoding="utf-8")


GEFS = "polymarket_scanner/weather_only_gefs_hourly.py"
GUARDED = "polymarket_scanner/weather_only_three_layer_guarded.py"
GEFS_TEST = "tests/test_weather_only_gefs_hourly.py"
GUARDED_TEST = "tests/test_weather_only_three_layer_validation_guarded.py"

# Open-Meteo explicitly supports UNIX epoch timestamps. Use them for the same-day
# ensemble so the repeated local 01:00 hour on a fall-back DST day has two distinct
# instants instead of relying on ambiguous local ISO strings.
rep(GEFS, "from datetime import date, datetime, timedelta\n", "from datetime import date, datetime, timedelta, timezone\n")
rep(
    GEFS,
    'GEFS_HOURLY_ADAPTER_VERSION = "open_meteo_ncep_gefs025_hourly_paths_v2_31_members"\n',
    'GEFS_HOURLY_ADAPTER_VERSION = "open_meteo_ncep_gefs025_hourly_paths_v3_unixtime_31_members"\n',
)
rep(
    GEFS,
    'GEFS_HOURLY_TEMPORAL_RESOLUTION = "hourly"\nGEFS_HOURLY_CELL_SELECTION = CELL_SELECTION_POLICY\n',
    'GEFS_HOURLY_TEMPORAL_RESOLUTION = "hourly"\nGEFS_HOURLY_TIMEFORMAT = "unixtime"\nGEFS_HOURLY_CELL_SELECTION = CELL_SELECTION_POLICY\n',
)
section(
    GEFS,
    "def _parse_local_valid_times(",
    "@dataclass(frozen=True, slots=True)\nclass GEFSHourlyMemberSeries:",
    '''def _parse_unix_valid_times(\n    raw_times: object, *, target: date, timezone_name: str\n) -> tuple[float, ...]:\n    if not isinstance(raw_times, list) or not raw_times:\n        raise GEFSHourlyError("GEFS_HOURLY_TIME_SERIES_INVALID")\n    try:\n        zone = ZoneInfo(timezone_name)\n    except ZoneInfoNotFoundError:\n        raise GEFSHourlyError("GEFS_HOURLY_TIMEZONE_INVALID") from None\n\n    parsed: list[float] = []\n    seen: set[float] = set()\n    for raw in raw_times:\n        if raw is None or isinstance(raw, bool) or not isinstance(raw, (int, float)):\n            raise GEFSHourlyError("GEFS_HOURLY_TIME_VALUE_INVALID")\n        value = float(raw)\n        if (\n            not math.isfinite(value)\n            or value < 0.0\n            or abs(value - round(value)) > 1e-6\n        ):\n            raise GEFSHourlyError("GEFS_HOURLY_TIME_VALUE_INVALID")\n        instant = float(round(value))\n        if instant in seen:\n            raise GEFSHourlyError("GEFS_HOURLY_TIME_INSTANT_DUPLICATE")\n        seen.add(instant)\n        local = datetime.fromtimestamp(instant, tz=timezone.utc).astimezone(zone)\n        if local.date() != target:\n            raise GEFSHourlyError("GEFS_HOURLY_TIME_OUTSIDE_TARGET_LOCAL_DATE")\n        parsed.append(instant)\n\n    if parsed != sorted(parsed):\n        raise GEFSHourlyError("GEFS_HOURLY_TIME_NOT_MONOTONIC")\n    for before, after in zip(parsed, parsed[1:]):\n        if abs((after - before) - GEFS_HOURLY_STEP_SECONDS) > 1e-6:\n            raise GEFSHourlyError("GEFS_HOURLY_GRID_NOT_EXACT_HOURLY")\n\n    target_start, target_end = _local_target_bounds(target, timezone_name)\n    if abs(parsed[0] - target_start) > 1e-6:\n        raise GEFSHourlyError("GEFS_HOURLY_TARGET_START_MISSING")\n    if abs((parsed[-1] + GEFS_HOURLY_STEP_SECONDS) - target_end) > 1e-6:\n        raise GEFSHourlyError("GEFS_HOURLY_TARGET_END_COVERAGE_MISSING")\n    return tuple(parsed)\n\n\n''',
)
rep(
    GEFS,
    "    query_temporal_resolution: str\n    source_role: str\n",
    "    query_temporal_resolution: str\n    query_timeformat: str\n    source_role: str\n",
)
rep(
    GEFS,
    '        "query_temporal_resolution": distribution.query_temporal_resolution,\n        "source_role": distribution.source_role,\n',
    '        "query_temporal_resolution": distribution.query_temporal_resolution,\n        "query_timeformat": distribution.query_timeformat,\n        "source_role": distribution.source_role,\n',
)
rep(
    GEFS,
    "    query_temporal_resolution: str = GEFS_HOURLY_TEMPORAL_RESOLUTION,\n) -> GEFSHourlyTargetDay:\n",
    "    query_temporal_resolution: str = GEFS_HOURLY_TEMPORAL_RESOLUTION,\n    query_timeformat: str = GEFS_HOURLY_TIMEFORMAT,\n) -> GEFSHourlyTargetDay:\n",
)
rep(
    GEFS,
    '    if query_temporal_resolution != GEFS_HOURLY_TEMPORAL_RESOLUTION:\n        raise GEFSHourlyError("GEFS_HOURLY_TEMPORAL_RESOLUTION_MISMATCH")\n',
    '    if query_temporal_resolution != GEFS_HOURLY_TEMPORAL_RESOLUTION:\n        raise GEFSHourlyError("GEFS_HOURLY_TEMPORAL_RESOLUTION_MISMATCH")\n    if query_timeformat != GEFS_HOURLY_TIMEFORMAT:\n        raise GEFSHourlyError("GEFS_HOURLY_TIMEFORMAT_MISMATCH")\n',
)
rep(
    GEFS,
    "    valid_times = _parse_local_valid_times(\n        hourly.get(\"time\"), target=target_date, timezone_name=timezone\n    )\n",
    "    valid_times = _parse_unix_valid_times(\n        hourly.get(\"time\"), target=target_date, timezone_name=timezone\n    )\n",
)
rep(
    GEFS,
    '    if str(units.get("time") or "") != "iso8601":\n        raise GEFSHourlyError("GEFS_HOURLY_TIME_UNIT_INVALID")\n',
    '    if str(units.get("time") or "") != GEFS_HOURLY_TIMEFORMAT:\n        raise GEFSHourlyError("GEFS_HOURLY_TIME_UNIT_INVALID")\n',
)
rep(
    GEFS,
    "        query_temporal_resolution=query_temporal_resolution,\n        source_role=GEFS_HOURLY_ROLE,\n",
    "        query_temporal_resolution=query_temporal_resolution,\n        query_timeformat=query_timeformat,\n        source_role=GEFS_HOURLY_ROLE,\n",
)
rep(
    GEFS,
    "        or distribution.query_temporal_resolution != GEFS_HOURLY_TEMPORAL_RESOLUTION\n        or distribution.source_role != GEFS_HOURLY_ROLE\n",
    "        or distribution.query_temporal_resolution != GEFS_HOURLY_TEMPORAL_RESOLUTION\n        or distribution.query_timeformat != GEFS_HOURLY_TIMEFORMAT\n        or distribution.source_role != GEFS_HOURLY_ROLE\n",
)
rep(
    GEFS,
    '            "temporal_resolution": GEFS_HOURLY_TEMPORAL_RESOLUTION,\n            "temperature_unit": unit_name,\n',
    '            "temporal_resolution": GEFS_HOURLY_TEMPORAL_RESOLUTION,\n            "timeformat": GEFS_HOURLY_TIMEFORMAT,\n            "temperature_unit": unit_name,\n',
)
rep(
    GEFS,
    "            query_temporal_resolution=GEFS_HOURLY_TEMPORAL_RESOLUTION,\n        )\n",
    "            query_temporal_resolution=GEFS_HOURLY_TEMPORAL_RESOLUTION,\n            query_timeformat=GEFS_HOURLY_TIMEFORMAT,\n        )\n",
)

# The guarded network client must issue and attest the same frozen timeformat policy.
rep(
    GUARDED,
    "    GEFS_HOURLY_TEMPORAL_RESOLUTION,\n    GEFS_HOURLY_VARIABLE,\n",
    "    GEFS_HOURLY_TEMPORAL_RESOLUTION,\n    GEFS_HOURLY_TIMEFORMAT,\n    GEFS_HOURLY_VARIABLE,\n",
)
rep(
    GUARDED,
    '            "temporal_resolution": GEFS_HOURLY_TEMPORAL_RESOLUTION,\n            "temperature_unit": "fahrenheit" if unit == "F" else "celsius",\n',
    '            "temporal_resolution": GEFS_HOURLY_TEMPORAL_RESOLUTION,\n            "timeformat": GEFS_HOURLY_TIMEFORMAT,\n            "temperature_unit": "fahrenheit" if unit == "F" else "celsius",\n',
)
rep(
    GUARDED,
    "            query_temporal_resolution=GEFS_HOURLY_TEMPORAL_RESOLUTION,\n        )\n",
    "            query_temporal_resolution=GEFS_HOURLY_TEMPORAL_RESOLUTION,\n            query_timeformat=GEFS_HOURLY_TIMEFORMAT,\n        )\n",
)

# Core GEFS tests now use absolute epochs, including real 23/25-hour DST target days.
rep(GEFS_TEST, "from datetime import date, datetime, timezone\n", "from datetime import date, datetime, timezone\nfrom zoneinfo import ZoneInfo\n")
rep(
    GEFS_TEST,
    "    GEFS_HOURLY_TEMPORAL_RESOLUTION,\n",
    "    GEFS_HOURLY_TEMPORAL_RESOLUTION,\n    GEFS_HOURLY_TIMEFORMAT,\n",
)
rep(
    GEFS_TEST,
    '    times = [f"2026-09-11T{hour:02d}:00" for hour in range(24)]\n    hourly = {"time": times}\n    units = {"time": "iso8601"}\n',
    '    times = [int(DAY_START + hour * GEFS_HOURLY_STEP_SECONDS) for hour in range(24)]\n    hourly = {"time": times}\n    units = {"time": GEFS_HOURLY_TIMEFORMAT}\n',
)
rep(
    GEFS_TEST,
    "    assert distribution.query_temporal_resolution == GEFS_HOURLY_TEMPORAL_RESOLUTION\n",
    "    assert distribution.query_temporal_resolution == GEFS_HOURLY_TEMPORAL_RESOLUTION\n    assert distribution.query_timeformat == GEFS_HOURLY_TIMEFORMAT\n",
)
rep(
    GEFS_TEST,
    '    with pytest.raises(GEFSHourlyError, match="GEFS_HOURLY_TEMPORAL_RESOLUTION_MISMATCH"):\n        _parse(query_temporal_resolution="native")\n',
    '    with pytest.raises(GEFSHourlyError, match="GEFS_HOURLY_TEMPORAL_RESOLUTION_MISMATCH"):\n        _parse(query_temporal_resolution="native")\n    with pytest.raises(GEFSHourlyError, match="GEFS_HOURLY_TIMEFORMAT_MISMATCH"):\n        _parse(query_timeformat="iso8601")\n',
)
section(
    GEFS_TEST,
    "def _dst_payload(",
    "def test_fall_back_25_hour_day_accepts_explicit_offsets_but_rejects_ambiguous_naive_duplicate():",
    '''def _dst_payload(times, timezone_name):\n    hourly = {"time": list(times)}\n    units = {"time": GEFS_HOURLY_TIMEFORMAT}\n    for member, key in enumerate(_keys()):\n        hourly[key] = [60.0 + member * 0.1 + index * 0.01 for index in range(len(times))]\n        units[key] = "°F"\n    return {\n        "latitude": 40.78,\n        "longitude": -73.87,\n        "timezone": timezone_name,\n        "hourly": hourly,\n        "hourly_units": units,\n    }\n\n\ndef _local_day_epochs(target: date, timezone_name: str) -> list[int]:\n    zone = ZoneInfo(timezone_name)\n    start = datetime(target.year, target.month, target.day, tzinfo=zone).timestamp()\n    following = date.fromordinal(target.toordinal() + 1)\n    end = datetime(following.year, following.month, following.day, tzinfo=zone).timestamp()\n    return [\n        int(value)\n        for value in range(int(start), int(end), GEFS_HOURLY_STEP_SECONDS)\n    ]\n\n\ndef test_spring_forward_23_hour_local_day_is_accepted_with_unambiguous_unix_instants():\n    target = date(2026, 3, 8)\n    times = _local_day_epochs(target, "America/New_York")\n    assert len(times) == 23\n    result = parse_open_meteo_gefs_hourly_target_day(\n        _dst_payload(times, "America/New_York"),\n        station="KLGA", target_date=target, unit="F", timezone="America/New_York",\n        requested_latitude=40.7769, requested_longitude=-73.8740, received_at=RECEIVED,\n    )\n    assert len(result.valid_times) == 23\n    assert all(\n        after - before == GEFS_HOURLY_STEP_SECONDS\n        for before, after in zip(result.valid_times, result.valid_times[1:])\n    )\n\n\n''',
)
section(
    GEFS_TEST,
    "def test_fall_back_25_hour_day_accepts_explicit_offsets_but_rejects_ambiguous_naive_duplicate():",
    "\n\n",
    '''def test_fall_back_25_hour_day_accepts_both_repeated_wall_hours_as_distinct_epochs():\n    target = date(2026, 11, 1)\n    times = _local_day_epochs(target, "America/New_York")\n    assert len(times) == 25\n    result = parse_open_meteo_gefs_hourly_target_day(\n        _dst_payload(times, "America/New_York"),\n        station="KLGA", target_date=target, unit="F", timezone="America/New_York",\n        requested_latitude=40.7769, requested_longitude=-73.8740, received_at=RECEIVED,\n    )\n    assert len(result.valid_times) == 25\n    local_labels = [\n        datetime.fromtimestamp(value, tz=timezone.utc)\n        .astimezone(ZoneInfo("America/New_York"))\n        .strftime("%Y-%m-%d %H:%M %z")\n        for value in result.valid_times\n    ]\n    assert any("01:00 -0400" in value for value in local_labels)\n    assert any("01:00 -0500" in value for value in local_labels)\n\n    duplicate = list(times)\n    duplicate[2] = duplicate[1]\n    with pytest.raises(GEFSHourlyError, match="GEFS_HOURLY_TIME_INSTANT_DUPLICATE"):\n        parse_open_meteo_gefs_hourly_target_day(\n            _dst_payload(duplicate, "America/New_York"),\n            station="KLGA", target_date=target, unit="F", timezone="America/New_York",\n            requested_latitude=40.7769, requested_longitude=-73.8740, received_at=RECEIVED,\n        )\n''',
)

# Guarded end-to-end request fixture and query assertions must prove the absolute-time
# policy is really sent over the wire, not merely accepted by the parser.
rep(
    GUARDED_TEST,
    "    GEFS_HOURLY_TEMPORAL_RESOLUTION,\n",
    "    GEFS_HOURLY_TEMPORAL_RESOLUTION,\n    GEFS_HOURLY_TIMEFORMAT,\n",
)
rep(
    GUARDED_TEST,
    '    hourly = {"time": [f"2026-09-14T{hour:02d}:00" for hour in range(24)]}\n    units = {"time": "iso8601"}\n',
    '    start = datetime(2026, 9, 14, 0, 0, tzinfo=timezone.utc).timestamp()\n    hourly = {"time": [int(start + hour * 3600) for hour in range(24)]}\n    units = {"time": GEFS_HOURLY_TIMEFORMAT}\n',
)
rep(
    GUARDED_TEST,
    "    assert result.query_temporal_resolution == GEFS_HOURLY_TEMPORAL_RESOLUTION\n",
    "    assert result.query_temporal_resolution == GEFS_HOURLY_TEMPORAL_RESOLUTION\n    assert result.query_timeformat == GEFS_HOURLY_TIMEFORMAT\n",
)
rep(
    GUARDED_TEST,
    '    assert query["temporal_resolution"] == [GEFS_HOURLY_TEMPORAL_RESOLUTION]\n    assert query["cell_selection"] == [GEFS_HOURLY_CELL_SELECTION]\n',
    '    assert query["temporal_resolution"] == [GEFS_HOURLY_TEMPORAL_RESOLUTION]\n    assert query["timeformat"] == [GEFS_HOURLY_TIMEFORMAT]\n    assert query["cell_selection"] == [GEFS_HOURLY_CELL_SELECTION]\n',
)
