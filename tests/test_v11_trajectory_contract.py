"""Adversarial synthetic tests for the offline R09_NATIVE_2T_TRAJECTORY_V1 contract.

Every case here is constructed data; nothing is fetched, fit or admitted as real.
This module never imports/uses tools.v11_multimodel_panel's historical 541-day
plan or databases, and asserts no real-admission flag anywhere flips to True.

Timing layout for the default fixture (all offsets relative to the local target
day's start, S): run_initialized_at = S-11h, response_completed_at = S-8h
(3h after run), feature_ready_at ~= S-8h, decision_at = S-1h (still before
local-day start, per FUTURE_FORECAST), prediction_frozen_at ~= S-1h,
label_knowable_at = local-day-end + 1h. This leaves a 7h margin between
receipt and decision for the "late receipt" / "clock uncertainty" cases to
consume.
"""
from dataclasses import replace
from datetime import datetime, timezone as dt_timezone
import hashlib

import pytest

from tools.v11_multimodel_panel import PanelError, PROVIDERS
from tools import v11_trajectory_contract as tc

ZONE = 'America/New_York'
TARGET_DATE = '2027-01-15'
EXPECTED_HOURS = (12, 24)


def sha(label_text):
    return hashlib.sha256(label_text.encode()).hexdigest()


RULE_VERSION = sha('rule-v1')
STATION_VERSION = sha('station-v1')


def local_day(target_date=TARGET_DATE):
    start, end = tc.local_day_window(target_date, ZONE)
    return tc.LocalDay(target_date=target_date, timezone=ZONE, start_utc=start, end_utc_exclusive=end)


def run_context(target_date=TARGET_DATE):
    """A run 11h before local-day start, on a valid 6-hourly cycle boundary."""
    day = local_day(target_date)
    run_utc = day.start_utc - 11 * 3600
    dt = datetime.fromtimestamp(run_utc, dt_timezone.utc)
    return run_utc, dt.date().isoformat(), dt.hour


RUN_UTC, RUN_DATE, CYCLE = run_context(TARGET_DATE)


def declared_ts(utc):
    return tc.Timestamp(utc=utc, origin=tc.DECLARED_ORIGIN, uncertainty_seconds=0.0, clock_health='SYNCED')


def receipt_ts(utc, uncertainty=30.0, origin=None, health='SYNCED'):
    return tc.Timestamp(utc=utc, origin=origin or next(iter(tc.ALLOWED_ORIGINS['response_completed_at'])),
                         uncertainty_seconds=uncertainty, clock_health=health)


def protocol_ts(utc, role, uncertainty=0.0, health='SYNCED'):
    return tc.Timestamp(utc=utc, origin=next(iter(tc.ALLOWED_ORIGINS[role])),
                         uncertainty_seconds=uncertainty, clock_health=health)


def make_point(member, hour, *, target_date=TARGET_DATE, receipt_offset=3 * 3600, rule_version=RULE_VERSION,
               response_origin=None, response_health='SYNCED', feature_offset=60, response_utc=None):
    run_utc, run_date, cycle = run_context(target_date)
    run_initialized_at = declared_ts(run_utc)
    valid_at = declared_ts(run_utc + hour * 3600)
    if response_utc is None:
        response_utc = run_utc + receipt_offset
    response_completed_at = receipt_ts(response_utc, origin=response_origin, health=response_health)
    feature_ready_at = tc.Timestamp(utc=response_utc + feature_offset,
                                     origin=next(iter(tc.ALLOWED_ORIGINS['feature_ready_at'])),
                                     uncertainty_seconds=30.0, clock_health='SYNCED')
    return tc.TrajectoryPoint(
        contract_identity=tc.CONTRACT_ID, provider='GEFS', source_release=f'GEFS:{run_date}:{cycle:02d}Z',
        release_binding='OBSERVED_HEADER_ONLY', run_date=run_date, cycle=cycle, member=member,
        forecast_hour=hour, run_initialized_at=run_initialized_at, valid_at=valid_at,
        source_published_at=None, response_completed_at=response_completed_at,
        feature_ready_at=feature_ready_at, value_native_k=280.0 + member * 0.01,
        conversion=tc.UnitConversion(original_unit='K', stored_unit='C', formula='SUBTRACT_273.15'),
        grid=tc.GridExtraction(nearest_lat=33.64, nearest_lon=-84.43, distance_km=5.0,
                                grid_resolution_deg=0.25, grid_sha256=sha(f'grid-{member}-{hour}-{target_date}')),
        capture=tc.CaptureRef(byte_sha256=sha(f'bytes-{member}-{hour}-{target_date}'),
                               index_sha256=sha(f'idx-{member}-{hour}-{target_date}'),
                               store='private-evidence/r09-trajectory-cas', immutable=True),
        rule_version=rule_version, station_version=STATION_VERSION)


def full_points(target_date=TARGET_DATE, **kwargs):
    return [make_point(m, h, target_date=target_date, **kwargs)
            for m in range(PROVIDERS['GEFS']) for h in EXPECTED_HOURS]


def expected_coverage():
    return tc.ExpectedCoverage(provider='GEFS', expected_members=tuple(range(PROVIDERS['GEFS'])),
                                expected_hours=EXPECTED_HOURS)


def clocks(target_date=TARGET_DATE, decision_offset=3600, freeze_offset=60):
    day = local_day(target_date)
    decision_at = protocol_ts(day.start_utc - decision_offset, 'decision_at')
    prediction_frozen_at = tc.Timestamp(utc=decision_at.utc + freeze_offset,
                                         origin=next(iter(tc.ALLOWED_ORIGINS['prediction_frozen_at'])),
                                         uncertainty_seconds=0.0, clock_health='SYNCED')
    return tc.CausalClocks(decision_at=decision_at, prediction_frozen_at=prediction_frozen_at)


def label(target_date=TARGET_DATE, label_offset=3600, label_version=None, revision_of=None):
    day = local_day(target_date)
    knowable = tc.Timestamp(utc=day.end_utc_exclusive + label_offset,
                             origin=next(iter(tc.ALLOWED_ORIGINS['label_knowable_at'])),
                             uncertainty_seconds=0.0, clock_health='SYNCED')
    return tc.LabelFact(label_version=label_version or sha('label-v1'), label_knowable_at=knowable,
                         family='daily_high_temperature', winner_bucket=3, revision_of=revision_of)


def split_cutoffs(target_date=TARGET_DATE, fit_offset=3600, dev_gap=200000, conf_gap=200000):
    lab = label(target_date=target_date, label_offset=fit_offset)
    fit_cutoff = protocol_ts(lab.label_knowable_at.utc + 60, 'fit_cutoff')
    selection_freeze_at = protocol_ts(fit_cutoff.utc + dev_gap, 'selection_freeze_at')
    evaluation_asof = protocol_ts(selection_freeze_at.utc + conf_gap, 'evaluation_asof')
    return dict(TRAIN=fit_cutoff, DEVELOPMENT=selection_freeze_at, CONFIRMATION=evaluation_asof)


def baseline_example(city_day='KATL|2027-01-15', split='TRAIN', target_date=TARGET_DATE):
    return dict(city_day=city_day, split=split, points=full_points(target_date=target_date),
                expected=expected_coverage(), local_day=local_day(target_date), clocks=clocks(target_date),
                label=label(target_date), split_cutoffs=split_cutoffs(target_date))


# --- baseline sanity -------------------------------------------------------

def test_baseline_example_validates_and_admits_nothing_real():
    record = tc.validate_example(**baseline_example())
    assert record['learner_admitted'] is False
    assert record['evidence_class'] == 'SYNTHETIC'
    assert record['financial_authority'] is False
    assert record['coverage']['required'] == record['coverage']['present_all']
    assert record['out_of_day_points'] == 0


def test_never_admits_a_real_example():
    with pytest.raises(PanelError, match='REAL_ADAPTER_NOT_IMPLEMENTED'):
        tc.validate_example(**{**baseline_example(), 'evidence_class': 'REAL'})


# --- identity must never alias the legacy contracts -------------------------

def _point_kwargs(contract_identity):
    return dict(contract_identity=contract_identity, provider='GEFS',
                source_release=f'GEFS:{RUN_DATE}:{CYCLE:02d}Z', release_binding='OBSERVED_HEADER_ONLY',
                run_date=RUN_DATE, cycle=CYCLE, member=0, forecast_hour=12,
                run_initialized_at=declared_ts(RUN_UTC), valid_at=declared_ts(RUN_UTC + 12 * 3600),
                source_published_at=None,
                response_completed_at=receipt_ts(RUN_UTC + 3 * 3600), feature_ready_at=receipt_ts(RUN_UTC + 3 * 3600 + 60),
                value_native_k=280.0, conversion=tc.UnitConversion('K', 'C', 'SUBTRACT_273.15'),
                grid=tc.GridExtraction(33.64, -84.43, 5.0, 0.25, sha('g')),
                capture=tc.CaptureRef(sha('b'), sha('i'), 'private-evidence/x', True),
                rule_version=RULE_VERSION, station_version=STATION_VERSION)


def test_contract_identity_accepts_only_the_new_identity():
    tc.TrajectoryPoint(**_point_kwargs(tc.CONTRACT_ID))  # does not raise


@pytest.mark.parametrize('alias', sorted(tc.FORBIDDEN_IDENTITY_ALIASES))
def test_contract_identity_rejects_legacy_aliases(alias):
    with pytest.raises(PanelError, match='CONTRACT_IDENTITY'):
        tc.TrajectoryPoint(**_point_kwargs(alias))


# --- 1. missing expected members / late-partial receipt ---------------------

def test_missing_expected_member_fails_closed():
    points = [p for p in full_points() if not (p.member == 5 and p.forecast_hour == 24)]
    with pytest.raises(PanelError, match='MISSING_EXPECTED_MESSAGE'):
        tc.validate_coverage(points, expected_coverage())


def test_late_partial_receipt_still_covers_but_fails_decision_availability():
    # All expected points arrive (coverage complete), but member 5's hour-24
    # message lands after decision_at: a late-partial receipt must still gate.
    decision_at_utc = clocks().decision_at.utc
    late_offset = (decision_at_utc - RUN_UTC) + 3600  # 1h after the real cutoff
    points = [p for p in full_points() if not (p.member == 5 and p.forecast_hour == 24)]
    points.append(make_point(5, 24, receipt_offset=late_offset))
    coverage = tc.validate_coverage(points, expected_coverage())
    assert set(coverage['required']) == set(coverage['present_all'])  # coverage complete; only the clock gate fails
    with pytest.raises(PanelError, match='FEATURE_NOT_AVAILABLE_BY_DECISION'):
        tc.validate_example(**{**baseline_example(), 'points': points})


# --- 2. future-valid but already-received forecasts (legitimate) ------------

def test_future_valid_forecast_already_received_is_admitted_and_tagged_out_of_day():
    day = local_day()
    points = full_points()
    future_hour = int((day.end_utc_exclusive - RUN_UTC) / 3600) + 10
    points.append(make_point(0, future_hour, receipt_offset=3 * 3600))
    coverage = tc.validate_coverage(points, expected_coverage())
    assert (0, future_hour) in coverage['extra_native_points']
    record = tc.validate_example(**{**baseline_example(), 'points': points})
    assert record['out_of_day_points'] == 1
    assert record['points'] == PROVIDERS['GEFS'] * len(EXPECTED_HOURS) + 1


# --- 3. initialization-as-availability fraud ---------------------------------

def test_initialization_as_availability_fraud_rejected_on_equal_timestamp():
    with pytest.raises(PanelError, match='INITIALIZATION_AS_AVAILABILITY_FRAUD'):
        make_point(0, 12, response_utc=RUN_UTC)  # claims receipt exactly at model init


def test_initialization_as_availability_fraud_rejected_on_wrong_origin():
    with pytest.raises(PanelError, match='RESPONSE_COMPLETED_AT_ORIGIN_UNTRUSTED'):
        make_point(0, 12, response_origin='PROVIDER_METADATA')


def test_initialization_time_copy_origin_is_not_a_trusted_receipt_origin():
    with pytest.raises(PanelError, match='RESPONSE_COMPLETED_AT_ORIGIN_UNTRUSTED'):
        make_point(0, 12, response_origin='INITIALIZATION_TIME_COPY')


# --- 4. timezone boundaries ---------------------------------------------------

@pytest.mark.parametrize('zone,day,hours', [
    ('America/New_York', '2027-03-14', 23), ('America/New_York', '2027-11-07', 25),
    ('Asia/Kolkata', '2027-01-15', 24), ('America/Sao_Paulo', '2027-01-15', 24),
])
def test_local_day_geometry_matches_iana_dst_and_fractional_offsets(zone, day, hours):
    start, end = tc.local_day_window(day, zone)
    assert end - start == hours * 3600
    ld = tc.LocalDay(target_date=day, timezone=zone, start_utc=start, end_utc_exclusive=end)
    assert ld.end_utc_exclusive - ld.start_utc == hours * 3600


def test_local_day_geometry_tamper_rejected():
    start, end = tc.local_day_window(TARGET_DATE, ZONE)
    with pytest.raises(PanelError, match='LOCAL_DAY_GEOMETRY_MISMATCH'):
        tc.LocalDay(target_date=TARGET_DATE, timezone=ZONE, start_utc=start - 1, end_utc_exclusive=end)


# --- 5. delayed / corrected labels --------------------------------------------

def test_label_correction_chain_must_be_append_only_and_later():
    v1 = label(label_version=sha('label-v1'))
    v2 = label(label_offset=7200, label_version=sha('label-v2'), revision_of=v1.label_version)
    current = tc.validate_label_lineage([v1, v2])
    assert current.label_version == v2.label_version


def test_label_correction_cannot_rewrite_without_revision_link():
    v1 = label(label_version=sha('label-v1'))
    v2 = label(label_offset=7200, label_version=sha('label-v2'), revision_of=None)
    with pytest.raises(PanelError, match='LABEL_LINEAGE_BROKEN'):
        tc.validate_label_lineage([v1, v2])


def test_label_correction_must_be_strictly_later():
    v1 = label(label_offset=7200, label_version=sha('label-v1'))
    v2 = label(label_offset=3600, label_version=sha('label-v2'), revision_of=v1.label_version)
    with pytest.raises(PanelError, match='LABEL_CORRECTION_NOT_LATER'):
        tc.validate_label_lineage([v1, v2])


def test_label_knowable_at_rejects_catalog_created_at_basis():
    day = local_day()
    knowable = tc.Timestamp(utc=day.end_utc_exclusive + 60, origin='CATALOG_CREATED_AT',
                             uncertainty_seconds=0.0, clock_health='SYNCED')
    with pytest.raises(PanelError):
        tc.LabelFact(label_version=sha('l'), label_knowable_at=knowable, family='daily_high_temperature',
                     winner_bucket=0, revision_of=None)


# --- 6. rule drift -------------------------------------------------------------

def test_rule_drift_within_single_example_rejected():
    points = full_points()
    tampered = replace(points[-1], rule_version=sha('rule-v2'))
    points = points[:-1] + [tampered]
    with pytest.raises(PanelError, match='RULE_DRIFT_WITHIN_EXAMPLE'):
        tc.validate_rule_consistency(points)


def test_rule_drift_across_reappearance_of_same_city_day_caught_as_duplicate():
    r1 = tc.validate_example(**baseline_example(city_day='KATL|2027-01-15', split='TRAIN'))
    drifted_points = [replace(p, rule_version=sha('rule-v2')) for p in full_points()]
    r2 = tc.validate_example(**{**baseline_example(city_day='KATL|2027-01-15', split='TRAIN'),
                                 'points': drifted_points})
    with pytest.raises(PanelError, match='DUPLICATE_CITY_DAY'):
        tc.validate_no_duplicate_city_days([r1, r2])


# --- 7. duplicate city-days -----------------------------------------------------

def test_duplicate_city_day_ingestion_rejected():
    r1 = tc.validate_example(**baseline_example(city_day='KATL|2027-01-15'))
    r2 = tc.validate_example(**baseline_example(city_day='KATL|2027-01-15'))
    with pytest.raises(PanelError, match='DUPLICATE_CITY_DAY'):
        tc.validate_no_duplicate_city_days([r1, r2])


def test_distinct_city_days_pass():
    r1 = tc.validate_example(**baseline_example(city_day='KATL|2027-01-15'))
    r2 = tc.validate_example(**baseline_example(city_day='KORD|2027-01-15'))
    tc.validate_no_duplicate_city_days([r1, r2])  # no raise


# --- 8. clock uncertainty --------------------------------------------------------

def test_clock_uncertainty_crossing_decision_cutoff_fails_even_if_nominal_passes():
    decision_at = clocks().decision_at
    nominal_receipt = decision_at.utc - 5  # nominal value is safely before cutoff
    response_completed_at = receipt_ts(nominal_receipt, uncertainty=30.0)
    feature_ready_at = tc.Timestamp(utc=nominal_receipt + 1, origin='LOCAL_RECEIPT_WALL_CLOCK',
                                     uncertainty_seconds=30.0, clock_health='SYNCED')
    assert feature_ready_at.utc <= decision_at.utc  # nominal value passes
    assert feature_ready_at.conservative_upper_bound > decision_at.utc  # but uncertainty crosses it
    point = tc.TrajectoryPoint(
        contract_identity=tc.CONTRACT_ID, provider='GEFS', source_release=f'GEFS:{RUN_DATE}:{CYCLE:02d}Z',
        release_binding='OBSERVED_HEADER_ONLY', run_date=RUN_DATE, cycle=CYCLE, member=0, forecast_hour=12,
        run_initialized_at=declared_ts(RUN_UTC), valid_at=declared_ts(RUN_UTC + 12 * 3600),
        source_published_at=None, response_completed_at=response_completed_at, feature_ready_at=feature_ready_at,
        value_native_k=280.0, conversion=tc.UnitConversion('K', 'C', 'SUBTRACT_273.15'),
        grid=tc.GridExtraction(33.64, -84.43, 5.0, 0.25, sha('g')),
        capture=tc.CaptureRef(sha('b'), sha('i'), 'private-evidence/x', True),
        rule_version=RULE_VERSION, station_version=STATION_VERSION)
    points = [p for p in full_points() if not (p.member == 0 and p.forecast_hour == 12)] + [point]
    with pytest.raises(PanelError, match='FEATURE_NOT_AVAILABLE_BY_DECISION'):
        tc.validate_example(**{**baseline_example(), 'points': points})


def test_unknown_clock_health_fails_closed():
    with pytest.raises(PanelError, match='CLOCK_HEALTH_NOT_SYNCED'):
        make_point(0, 12, response_health='UNKNOWN')


def test_unknown_split_cutoff_origin_fails_closed():
    cutoffs = split_cutoffs()
    bad = tc.Timestamp(utc=cutoffs['TRAIN'].utc, origin='UNKNOWN', uncertainty_seconds=0.0, clock_health='UNKNOWN')
    with pytest.raises(PanelError):
        tc.validate_split_cutoffs({**cutoffs, 'TRAIN': bad})


# --- 9. immutable replay -----------------------------------------------------------

def test_immutable_replay_of_identical_bytes_is_a_noop():
    registry = tc.CaptureRegistry()
    key = ('GEFS', RUN_DATE, CYCLE, 0, 12)
    byte_digest = sha('bytes-0-12')
    assert registry.register(key, byte_digest) is True
    assert registry.register(key, byte_digest) is False  # idempotent replay


def test_immutable_replay_conflicting_overwrite_rejected():
    registry = tc.CaptureRegistry()
    key = ('GEFS', RUN_DATE, CYCLE, 0, 12)
    registry.register(key, sha('bytes-v1'))
    with pytest.raises(PanelError, match='IMMUTABLE_CAPTURE_CONFLICT'):
        registry.register(key, sha('bytes-v2'))


# --- 10. legitimate cadence gaps vs missing messages --------------------------------

def test_native_cadence_beyond_required_policy_is_not_a_missing_message():
    points = full_points()
    # A finer-cadence native point (hour 15) beyond the frozen required policy is
    # legitimate extra evidence, not a gap and not gating.
    points.append(make_point(0, 15))
    coverage = tc.validate_coverage(points, expected_coverage())
    assert (0, 15) in coverage['extra_native_points']
    tc.validate_example(**{**baseline_example(), 'points': points})  # does not raise


def test_true_missing_message_within_required_policy_gates():
    points = [p for p in full_points() if not (p.member == 30 and p.forecast_hour == 12)]
    with pytest.raises(PanelError, match='MISSING_EXPECTED_MESSAGE'):
        tc.validate_example(**{**baseline_example(), 'points': points})


# --- split cutoffs / global-cutoff prohibition ---------------------------------------

def test_split_cutoffs_must_be_distinct_and_ordered():
    cutoffs = split_cutoffs()
    shared = cutoffs['TRAIN']
    with pytest.raises(PanelError, match='SPLIT_CUTOFFS_MUST_BE_DISTINCT_AND_ORDERED'):
        tc.validate_split_cutoffs({**cutoffs, 'DEVELOPMENT': shared})


def test_split_cutoffs_valid_ordering_passes():
    tc.validate_split_cutoffs(split_cutoffs())  # no raise


# --- cross-split label embargo -----------------------------------------------------

def test_cross_split_label_embargo_leakage_rejected():
    before = dict(split='TRAIN', label_knowable_upper_bound=2000.0)
    after = dict(split='DEVELOPMENT', decision_at=1000.0)  # decides before the prior label was even knowable
    with pytest.raises(PanelError, match='CROSS_SPLIT_LABEL_EMBARGO_LEAKAGE'):
        tc.validate_cross_split_embargo([before, after])


def test_cross_split_label_embargo_respected_when_properly_sequenced():
    before = dict(split='TRAIN', label_knowable_upper_bound=1000.0)
    after = dict(split='DEVELOPMENT', decision_at=2000.0)
    tc.validate_cross_split_embargo([before, after])  # no raise


# --- prediction must freeze strictly after decision ---------------------------------

def test_prediction_frozen_before_decision_rejected():
    decision_at = protocol_ts(local_day().start_utc - 3600, 'decision_at')
    prediction_frozen_at = tc.Timestamp(utc=decision_at.utc - 10, origin='LOCAL_RECEIPT_WALL_CLOCK',
                                         uncertainty_seconds=0.0, clock_health='SYNCED')
    with pytest.raises(PanelError, match='PREDICTION_NOT_FROZEN_AFTER_DECISION'):
        tc.CausalClocks(decision_at=decision_at, prediction_frozen_at=prediction_frozen_at)


# --- full corpus integration (three genuinely distinct, ordered dates) --------------

def test_full_corpus_validates_with_no_real_admission():
    dates = dict(TRAIN='2027-01-10', DEVELOPMENT='2027-02-10', CONFIRMATION='2027-03-05')
    cutoffs = dict(
        TRAIN=protocol_ts(label(target_date=dates['TRAIN']).label_knowable_at.utc + 60, 'fit_cutoff'),
        DEVELOPMENT=protocol_ts(label(target_date=dates['DEVELOPMENT']).label_knowable_at.utc + 60, 'selection_freeze_at'),
        CONFIRMATION=protocol_ts(label(target_date=dates['CONFIRMATION']).label_knowable_at.utc + 60, 'evaluation_asof'),
    )
    records = []
    for split, city in (('TRAIN', 'KATL'), ('DEVELOPMENT', 'KORD'), ('CONFIRMATION', 'KDEN')):
        d = dates[split]
        records.append(tc.validate_example(
            city_day=f'{city}|{d}', split=split, points=full_points(target_date=d),
            expected=expected_coverage(), local_day=local_day(d), clocks=clocks(target_date=d),
            label=label(target_date=d), split_cutoffs=cutoffs))
    result = tc.validate_corpus(records, cutoffs)
    assert result['admitted_real_examples'] == 0
    assert result['historical_541_day_admissions'] == 0
    assert result['splits'] == dict(TRAIN=1, DEVELOPMENT=1, CONFIRMATION=1)
    assert all(not r['financial_authority'] and not r['learner_admitted'] for r in records)
