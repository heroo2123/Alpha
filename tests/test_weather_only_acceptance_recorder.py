from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from copy import deepcopy
from datetime import date

import pytest

from polymarket_scanner.models import Book
from polymarket_scanner.weather_only_acceptance_bundle import load_weather_w7_acceptance_bundle_json
from polymarket_scanner.weather_only_acceptance_recorder import (
    WeatherW7RecorderError,
    WeatherW7RecorderSession,
    atomic_write_weather_w7_bundle,
    parse_weather_w7_runtime_report,
    select_weather_w7_probe_event,
)
from polymarket_scanner.weather_only_clob import WeatherExecutionSnapshot, WeatherMarketParameters
from polymarket_scanner.weather_only_runtime import WEATHER_SHADOW_RUNTIME_VERSION


SHA = "a" * 40
BOOT_ID = "12345678-1234-1234-1234-123456789abc"
PID = 321


def _market(mid, question):
    return {
        "id": mid,
        "active": True,
        "closed": False,
        "acceptingOrders": True,
        "enableOrderBook": True,
        "question": question,
        "slug": f"m-{mid}",
        "conditionId": f"condition-{mid}",
        "outcomes": '["Yes","No"]',
        "clobTokenIds": f'["{mid}-yes","{mid}-no"]',
    }


def _event(*, hourly=True):
    rules = (
        "This market will resolve to the temperature range that contains the highest temperature recorded by NOAA "
        "at the LaGuardia Airport Station in degrees Fahrenheit on 11 Sep '26. "
        'The resolution source for this market will be information from NOAA, specifically the highest reading under the "Temp" '
        "column for all times on this day, available here: https://www.weather.gov/wrh/timeseries?site=klga "
    )
    if hourly:
        rules += 'This market will resolve off of the Hourly Data provided using the "Show Hourly Data" button. '
    rules += (
        "If NOAA data for the observation date is unavailable by 11:59 PM ET on the day following the observation date, "
        "the Weather Underground Daily Observations table will be used as the resolution source. "
        "In the event that there is no data for the observation date by 11:59 PM ET on the day following the observation date, "
        "this market will resolve to the lowest bracket. "
        "This market will resolve once the first data point for the following date has been published on the resolution source, "
        "or by 11:59 PM ET on the day following the observation date, whichever comes first. "
        "The resolution source for this market measures temperatures to whole degrees Fahrenheit. "
        "Revisions to temperatures recorded within this market's timeframe will be considered until the first datapoint for the "
        "following date has been published, after which any alterations will not be considered."
    )
    return {
        "id": "nws-high",
        "slug": "nws-high",
        "title": "Highest temperature in NYC on September 11?",
        "description": rules,
        "resolutionSource": "https://www.weather.gov/wrh/timeseries?site=klga",
        "markets": [
            _market("a", "Will the highest temperature be 69°F or lower?"),
            _market("b", "Will the highest temperature be 70-71°F?"),
            _market("c", "Will the highest temperature be 72°F or higher?"),
        ],
    }


def _db(path):
    with sqlite3.connect(path) as c:
        c.executescript(
            """
            CREATE TABLE signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fingerprint TEXT UNIQUE,
                detector TEXT NOT NULL,
                confidence TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'OPEN',
                created_at TEXT NOT NULL
            );
            CREATE TABLE manual_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'OPEN',
                created_at TEXT NOT NULL
            );
            CREATE TABLE telegram_outbox (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_id INTEGER NOT NULL UNIQUE,
                priority INTEGER NOT NULL DEFAULT 10,
                status TEXT NOT NULL DEFAULT 'PENDING',
                attempts INTEGER NOT NULL DEFAULT 0,
                next_attempt_at REAL NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                sent_at REAL,
                claimed_at REAL
            );
            """
        )
        c.execute(
            "INSERT INTO signals(fingerprint,detector,confidence,created_at) VALUES('legacy','legacy','WATCH','2026-09-11T00:00:00Z')"
        )
        c.execute(
            "INSERT INTO telegram_outbox(signal_id,priority,status,attempts,next_attempt_at,created_at) VALUES(1,10,'PENDING',0,0,1.0)"
        )


def _proc(root):
    (root / str(PID)).mkdir(parents=True)
    (root / "sys" / "kernel" / "random").mkdir(parents=True)
    fields = ["S"] + ["0"] * 18 + ["987654"] + ["0"] * 8
    (root / str(PID) / "stat").write_text(
        f"{PID} (weather scanner) " + " ".join(fields), encoding="utf-8"
    )
    (root / str(PID) / "cmdline").write_bytes(
        b"python\0-m\0polymarket_scanner.weather_only_runtime\0"
    )
    (root / str(PID) / "status").write_text(
        "Name:\tpython\nVmRSS:\t 100000 kB\nVmHWM:\t 120000 kB\n",
        encoding="utf-8",
    )
    (root / "meminfo").write_text(
        "MemAvailable: 300000 kB\nSwapTotal: 0 kB\nSwapFree: 0 kB\n",
        encoding="utf-8",
    )
    (root / "sys" / "kernel" / "random" / "boot_id").write_text(
        BOOT_ID + "\n", encoding="ascii"
    )


def _runtime_payload(now, **overrides):
    payload = {
        "version": WEATHER_SHADOW_RUNTIME_VERSION,
        "mode": "SILENT_SHADOW",
        "read_only": True,
        "financial_authority": False,
        "financial_delivery": False,
        "automatic_order_placement": False,
        "gamma_execution_authority": False,
        "exact_clob_required_for_recorded_opportunities": True,
        "market_specific_fee_schedule_required": True,
        "v2_fd_fee_authority_required": True,
        "final_live_recheck_required": True,
        "forecast_probability_authority": False,
        "source_settlement_trade_authority": False,
        "started_at": now - 1.0,
        "finished_at": now - 0.1,
        "cycle_ok": True,
        "discovery": {"unique_event_count": 12, "unique_market_count": 36},
        "compiler": {"financial_authority_events": 0},
        "opportunities": [],
    }
    payload.update(overrides)
    return payload


class _FakeCLOB:
    async def exact_event_snapshot(self, compiled):
        now = time.time()
        books = {}
        params = {}
        for bucket in compiled.buckets:
            params[bucket.condition_id] = WeatherMarketParameters(
                condition_id=bucket.condition_id,
                token_outcomes=((bucket.yes_token, "Yes"), (bucket.no_token, "No")),
                minimum_order_size=1.0,
                minimum_tick_size=0.01,
                fee_rate=0.0,
                fee_exponent=0,
                taker_only=None,
                maker_base_fee_bps=0,
                taker_base_fee_bps=0,
                rfq_enabled=False,
                taker_delay_enabled=False,
                received_at=now,
            )
            for token in (bucket.yes_token, bucket.no_token):
                books[token] = Book(
                    token_id=token,
                    bids=[(0.30, 10.0)],
                    asks=[(0.60, 10.0)],
                    received_at=now,
                    source="exact_clob_test",
                )
        return WeatherExecutionSnapshot(
            version="test-exact",
            event_id=compiled.event_id,
            books=books,
            parameters=params,
            started_at=now,
            finished_at=now + 0.001,
            exact_clob=True,
            financial_authority=False,
        )


async def _noop_close():
    return None


def test_runtime_report_parser_rejects_stale_authority_and_bad_opportunity():
    now = 1_800_000_000.0
    parsed = parse_weather_w7_runtime_report(_runtime_payload(now), observed_at=now)
    assert parsed.cycle_ok is True
    assert parsed.weather_event_count == 12
    assert parsed.exact_clob_required is True
    assert parsed.financial_authority is False

    with pytest.raises(WeatherW7RecorderError) as stale:
        parse_weather_w7_runtime_report(
            _runtime_payload(now - 100.0),
            observed_at=now,
        )
    assert stale.value.code == "W7_RECORDER_RUNTIME_REPORT_STALE"

    with pytest.raises(WeatherW7RecorderError) as authority:
        parse_weather_w7_runtime_report(
            _runtime_payload(now, financial_delivery=True),
            observed_at=now,
        )
    assert "financial_delivery" in authority.value.code

    bad = _runtime_payload(now)
    bad["opportunities"] = [{
        "rechecked": True,
        "financial_authority": False,
        "financial_delivery": False,
        "automatic_order_placement": True,
    }]
    with pytest.raises(WeatherW7RecorderError) as opportunity:
        parse_weather_w7_runtime_report(bad, observed_at=now)
    assert "automatic_order_placement" in opportunity.value.code


def test_probe_selection_requires_exact_hourly_wrh_profile():
    good, compiled = select_weather_w7_probe_event(
        [_event(hourly=False), _event(hourly=True)],
        event_id="nws-high",
        today_utc=date(2026, 9, 11),
    )
    assert good["id"] == "nws-high"
    assert compiled.station_hint == "KLGA"
    assert compiled.exactly_one_outcome_proven is True

    with pytest.raises(WeatherW7RecorderError) as all_times:
        select_weather_w7_probe_event(
            [_event(hourly=False)],
            today_utc=date(2026, 9, 11),
        )
    assert all_times.value.code == "W7_RECORDER_NO_CERTIFIED_WRH_PROBE_EVENT"


def test_recorder_attaches_read_only_records_real_sample_and_finalizes_bundle(tmp_path):
    db = tmp_path / "signals.db"
    _db(db)
    db_before = db.read_bytes()
    proc = tmp_path / "proc"
    _proc(proc)
    report = tmp_path / "runtime.json"
    report.write_text(json.dumps(_runtime_payload(time.time())), encoding="utf-8")

    session = WeatherW7RecorderSession(
        release_sha=SHA,
        scanner_process_id=PID,
        database_path=db,
        runtime_report_path=report,
        probe_event=_event(),
        clob=_FakeCLOB(),
        proc_root=proc,
    )
    sample = asyncio.run(session.record_sample(poll_source=False))
    assert sample.weather_event_count == 12
    assert sample.process_rss_bytes == 120000 * 1024
    assert sample.swap_used_bytes == 0
    assert sample.exact_clob_required_for_candidates is True
    assert sample.financial_delivery is False
    assert len(sample.incremental_evaluation_evidence_sha256) == 64

    bundle = session.finalize()
    result = bundle.w7_evidence.run_evidence
    assert result.telegram_outbox_before == result.telegram_outbox_after == 1
    assert result.detector_promotions == 0
    assert result.order_attempts == 0
    assert result.actual_orders_placed == 0
    assert result.actual_fills_recorded == 0
    assert result.service_restart_count == 0
    assert db.read_bytes() == db_before

    # One sample intentionally cannot pass the frozen 45-minute gate, but the bundle
    # remains fully verifiable and preserves the failed acceptance honestly.
    from polymarket_scanner.weather_only_acceptance_bundle import validate_weather_w7_acceptance_bundle
    acceptance = validate_weather_w7_acceptance_bundle(bundle, expected_release_sha=SHA)
    assert acceptance.passed is False
    assert any(reason.startswith("SAMPLE_COUNT_BELOW_MIN") for reason in acceptance.reasons)


def test_recorder_detects_process_identity_change_in_cycle_and_final_counters(tmp_path):
    db = tmp_path / "signals.db"
    _db(db)
    proc = tmp_path / "proc"
    _proc(proc)
    report = tmp_path / "runtime.json"
    report.write_text(json.dumps(_runtime_payload(time.time())), encoding="utf-8")
    session = WeatherW7RecorderSession(
        release_sha=SHA,
        scanner_process_id=PID,
        database_path=db,
        runtime_report_path=report,
        probe_event=_event(),
        clob=_FakeCLOB(),
        proc_root=proc,
    )

    # Same PID, different kernel starttime -> PID recycling/restart.
    fields = ["S"] + ["0"] * 18 + ["999999"] + ["0"] * 8
    (proc / str(PID) / "stat").write_text(
        f"{PID} (weather scanner) " + " ".join(fields), encoding="utf-8"
    )
    sample = asyncio.run(session.record_sample(poll_source=False))
    assert sample.cycle_ok is False
    bundle = session.finalize()
    assert bundle.w7_evidence.run_evidence.service_restart_count == 1


def test_atomic_bundle_writer_rejects_symlink_and_round_trips(tmp_path):
    db = tmp_path / "signals.db"
    _db(db)
    proc = tmp_path / "proc"
    _proc(proc)
    report = tmp_path / "runtime.json"
    report.write_text(json.dumps(_runtime_payload(time.time())), encoding="utf-8")
    session = WeatherW7RecorderSession(
        release_sha=SHA,
        scanner_process_id=PID,
        database_path=db,
        runtime_report_path=report,
        probe_event=_event(),
        clob=_FakeCLOB(),
        proc_root=proc,
    )
    asyncio.run(session.record_sample(poll_source=False))
    bundle = session.finalize()

    output = tmp_path / "w7-bundle.json"
    atomic_write_weather_w7_bundle(output, bundle)
    assert output.stat().st_mode & 0o777 == 0o600
    loaded, report_result = load_weather_w7_acceptance_bundle_json(
        output.read_text(encoding="utf-8"),
        expected_release_sha=SHA,
    )
    assert loaded == bundle
    assert report_result.passed is False

    victim = tmp_path / "victim.txt"
    victim.write_text("keep", encoding="utf-8")
    link = tmp_path / "linked.json"
    link.symlink_to(victim)
    with pytest.raises(WeatherW7RecorderError) as symlink:
        atomic_write_weather_w7_bundle(link, bundle)
    assert symlink.value.code == "W7_RECORDER_OUTPUT_PATH_INVALID"
    assert victim.read_text(encoding="utf-8") == "keep"
