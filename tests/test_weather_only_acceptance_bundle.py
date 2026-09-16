from __future__ import annotations

import sqlite3
from dataclasses import replace

import pytest

from polymarket_scanner.weather_only_acceptance import WeatherW7RunEvidence
from polymarket_scanner.weather_only_acceptance_bundle import (
    WeatherW7BundleError,
    build_weather_w7_acceptance_bundle,
    dump_weather_w7_acceptance_bundle_json,
    load_weather_w7_acceptance_bundle_json,
    validate_weather_w7_acceptance_bundle,
)
from polymarket_scanner.weather_only_acceptance_containment import (
    build_weather_w7_containment_manifest,
    parse_linux_process_identity,
    read_weather_w7_database_snapshot,
)
from polymarket_scanner.weather_only_acceptance_evidence import build_weather_w7_evidence_envelope
from weather_w7_measurement_fixtures import build_w7_measurement_fixture


SHA = "a" * 40
START = 1_800_000_000.0
BOOT_ID = "12345678-1234-1234-1234-123456789abc"


def _make_db(path, *, outbox_rows=1):
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
        for index in range(outbox_rows):
            c.execute(
                "INSERT INTO signals(fingerprint,detector,confidence,created_at) VALUES(?,?,?,?)",
                (f"f{index}", "legacy", "WATCH", f"2026-09-11T00:00:{index:02d}Z"),
            )
            c.execute(
                "INSERT INTO telegram_outbox(signal_id,priority,status,attempts,next_attempt_at,created_at) VALUES(?,10,'PENDING',0,0,?)",
                (index + 1, float(index + 1)),
            )


def _process(*, pid=321, start_ticks=987654):
    suffix = ["S"] + ["0"] * 18 + [str(start_ticks)] + ["0"] * 8
    stat = f"{pid} (weather scanner) " + " ".join(suffix)
    return parse_linux_process_identity(
        process_id=pid,
        stat_text=stat,
        boot_id_text=BOOT_ID,
        cmdline_bytes=b"python\0-m\0polymarket_scanner.weather_only_runtime\0",
    )


def _base_envelope(*, observed_shift=0.0, detector_promotions=0):
    evidence, measurements = build_w7_measurement_fixture(
        release_sha=SHA,
        start=START,
        telegram_outbox_before=1,
        telegram_outbox_after=1,
    )
    if observed_shift:
        shifted = tuple(replace(row, observed_at=row.observed_at + observed_shift) for row in evidence.samples)
        evidence = replace(evidence, samples=shifted)
    if detector_promotions:
        evidence = replace(evidence, detector_promotions=detector_promotions)
    latest = max(row.observed_at for row in evidence.samples)
    return build_weather_w7_evidence_envelope(
        evidence,
        measurement_manifest=measurements,
        created_at=latest + 1.0,
    )


def _unchanged_containment(tmp_path):
    path = tmp_path / "signals.db"
    _make_db(path)
    before = read_weather_w7_database_snapshot(path, captured_at=START - 1.0)
    after = read_weather_w7_database_snapshot(path, captured_at=START + 2701.0)
    process = _process()
    manifest = build_weather_w7_containment_manifest(
        before_database=before,
        after_database=after,
        before_process=process,
        after_process=process,
    )
    return path, manifest


def test_final_bundle_round_trip_is_independently_verifiable_and_research_only(tmp_path):
    _, containment = _unchanged_containment(tmp_path)
    bundle = build_weather_w7_acceptance_bundle(
        w7_evidence=_base_envelope(),
        containment_manifest=containment,
    )
    assert len(bundle.bundle_sha256) == 64
    assert bundle.financial_authority is False
    assert bundle.financial_delivery is False
    assert bundle.detector_promotion_authority is False
    assert bundle.automatic_order_placement is False

    loaded, report = load_weather_w7_acceptance_bundle_json(
        dump_weather_w7_acceptance_bundle_json(bundle),
        expected_release_sha=SHA,
    )
    assert loaded == bundle
    assert report.passed is True
    assert report.reasons == ()


def test_same_count_outbox_state_mutation_fails_bundle_acceptance(tmp_path):
    path = tmp_path / "signals.db"
    _make_db(path)
    before = read_weather_w7_database_snapshot(path, captured_at=START - 1.0)
    with sqlite3.connect(path) as c:
        c.execute("UPDATE telegram_outbox SET status='SENT', sent_at=99.0 WHERE id=1")
    after = read_weather_w7_database_snapshot(path, captured_at=START + 2701.0)
    containment = build_weather_w7_containment_manifest(
        before_database=before,
        after_database=after,
        before_process=_process(),
        after_process=_process(),
    )
    bundle = build_weather_w7_acceptance_bundle(
        w7_evidence=_base_envelope(),
        containment_manifest=containment,
    )
    report = validate_weather_w7_acceptance_bundle(bundle)
    assert report.passed is False
    assert "TELEGRAM_OUTBOX_STATE_CHANGED" in report.reasons
    # Counts are still one on each side; state hashing is what caught the mutation.
    assert bundle.w7_evidence.run_evidence.telegram_outbox_before == 1
    assert bundle.w7_evidence.run_evidence.telegram_outbox_after == 1


def test_signal_registry_change_must_match_derived_counter_not_hand_authored_zero(tmp_path):
    path = tmp_path / "signals.db"
    _make_db(path)
    before = read_weather_w7_database_snapshot(path, captured_at=START - 1.0)
    with sqlite3.connect(path) as c:
        c.execute(
            "INSERT INTO signals(fingerprint,detector,confidence,created_at) VALUES('new','unexpected','ACTIONABLE','2026-09-11T01:00:00Z')"
        )
    after = read_weather_w7_database_snapshot(path, captured_at=START + 2701.0)
    containment = build_weather_w7_containment_manifest(
        before_database=before,
        after_database=after,
        before_process=_process(),
        after_process=_process(),
    )
    with pytest.raises(WeatherW7BundleError) as raised:
        build_weather_w7_acceptance_bundle(
            w7_evidence=_base_envelope(detector_promotions=0),
            containment_manifest=containment,
        )
    assert raised.value.code == "W7_BUNDLE_CONTAINMENT_COUNTER_MISMATCH:detector_promotions"


def test_old_valid_latency_measurements_cannot_be_shifted_into_later_sample_intervals(tmp_path):
    _, containment = _unchanged_containment(tmp_path)
    # The v3 inner envelope accepts these because each genuine measurement still
    # finishes before its now-shifted sample. The final bundle additionally requires
    # it to finish inside that sample's own interval, so replaying old evidence fails.
    shifted = _base_envelope(observed_shift=60.0)
    with pytest.raises(WeatherW7BundleError) as raised:
        build_weather_w7_acceptance_bundle(
            w7_evidence=shifted,
            containment_manifest=containment,
        )
    assert raised.value.code.startswith("W7_BUNDLE_INCREMENTAL_MEASUREMENT_OUTSIDE_SAMPLE_INTERVAL")


def test_containment_snapshots_must_bracket_entire_run(tmp_path):
    path = tmp_path / "signals.db"
    _make_db(path)
    before = read_weather_w7_database_snapshot(path, captured_at=START + 1.0)
    after = read_weather_w7_database_snapshot(path, captured_at=START + 2701.0)
    containment = build_weather_w7_containment_manifest(
        before_database=before,
        after_database=after,
        before_process=_process(),
        after_process=_process(),
    )
    with pytest.raises(WeatherW7BundleError) as raised:
        build_weather_w7_acceptance_bundle(
            w7_evidence=_base_envelope(),
            containment_manifest=containment,
        )
    assert raised.value.code == "W7_BUNDLE_CONTAINMENT_STARTED_AFTER_SAMPLES"


def test_bundle_digest_tamper_and_containment_process_restart_are_detected(tmp_path):
    _, containment = _unchanged_containment(tmp_path)
    bundle = build_weather_w7_acceptance_bundle(
        w7_evidence=_base_envelope(),
        containment_manifest=containment,
    )
    with pytest.raises(WeatherW7BundleError) as digest:
        validate_weather_w7_acceptance_bundle(replace(bundle, bundle_sha256="b" * 64))
    assert digest.value.code == "W7_BUNDLE_DIGEST_MISMATCH"

    restarted = build_weather_w7_containment_manifest(
        before_database=containment.before_database,
        after_database=containment.after_database,
        before_process=_process(pid=321, start_ticks=1000),
        after_process=_process(pid=322, start_ticks=2000),
    )
    # The run claims zero restarts, so provenance mismatch is rejected rather than
    # letting a hand-authored zero survive the containment manifest.
    with pytest.raises(WeatherW7BundleError) as process:
        build_weather_w7_acceptance_bundle(
            w7_evidence=_base_envelope(),
            containment_manifest=restarted,
        )
    assert process.value.code == "W7_BUNDLE_CONTAINMENT_COUNTER_MISMATCH:service_restart_count"
