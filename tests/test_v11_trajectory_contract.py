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

Gate-2 review (fed1cbe) findings F1-F8 each have a dedicated repair test below,
named test_gateN_review_*, in addition to the original adversarial coverage.
"""
from dataclasses import replace
from datetime import datetime, timezone as dt_timezone
from pathlib import Path
import hashlib
import inspect

import pytest

from tools.v11_multimodel_panel import PanelError, PROVIDERS, file_sha
from tools import v11_trajectory_contract as tc

ZONE = 'America/New_York'
TARGET_DATE = '2027-01-15'
EXPECTED_HOURS = (12, 24)
EVIDENCE = {}


def put(data):
    identity = hashlib.sha256(data).hexdigest()
    EVIDENCE[identity] = data
    return identity


def resolve(identity):
    return EVIDENCE.get(identity)


def bind_clock(ts, subject):
    return replace(ts, evidence_sha256=put(tc.clock_payload(ts, subject)))


def sha(label_text):
    return put(label_text.encode())


RULE_VERSION = sha('rule-v1')
STATION_VERSION = sha('station-v1')


def local_day(target_date=TARGET_DATE, zone=ZONE):
    start, end = tc.local_day_window(target_date, zone)
    return tc.LocalDay(target_date=target_date, timezone=zone, start_utc=start, end_utc_exclusive=end,
                        timezone_file_sha256=file_sha(Path('/usr/share/zoneinfo') / zone))


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
    origin = origin or next(iter(tc.ALLOWED_ORIGINS['response_completed_at']))
    ts = tc.Timestamp(utc=utc, origin=origin, uncertainty_seconds=uncertainty,
                      clock_health=health)
    return bind_clock(ts, 'LOCAL_CLOCK')


def protocol_ts(utc, role, uncertainty=0.0, health='SYNCED'):
    origin = next(iter(tc.ALLOWED_ORIGINS[role]))
    ts = tc.Timestamp(utc=utc, origin=origin,
                      uncertainty_seconds=uncertainty, clock_health=health)
    return bind_clock(ts, role)


def make_point(member, hour, *, target_date=TARGET_DATE, receipt_offset=3 * 3600, rule_version=RULE_VERSION,
               station_version=STATION_VERSION, response_origin=None, response_health='SYNCED', feature_offset=60,
               response_utc=None, run_date=None, cycle=None, byte_key=None, station_id='KATL', provider='GEFS'):
    run_utc, ctx_run_date, ctx_cycle = run_context(target_date)
    run_date = ctx_run_date if run_date is None else run_date
    cycle = ctx_cycle if cycle is None else cycle
    run_initialized_at = declared_ts(run_utc)
    valid_at = declared_ts(run_utc + hour * 3600)
    if response_utc is None:
        response_utc = run_utc + receipt_offset
    response_completed_at = receipt_ts(response_utc, origin=response_origin, health=response_health)
    byte_key = byte_key or f'{provider}-{member}-{hour}-{target_date}'
    grid = tc.GridExtraction(nearest_lat=33.64, nearest_lon=-84.43, distance_km=5.0,
                             grid_resolution_deg=0.25, grid_sha256=put(f'grid-{byte_key}'.encode()))
    policy_sha = put(b'extract-policy-v1')
    stations = {name: dict(station_version=station_version if name == station_id else
                           (STATION_VERSION if name == 'KATL' else sha('station-' + name.lower())),
                           grid=tc.asdict(grid), value_native_k=280.0 + member * 0.01 + offset,
                           policy_sha256=policy_sha)
                for name, offset in (('KATL', 0.0), ('KORD', 1.0), ('KDEN', 2.0))}
    message = dict(byte_key=byte_key, provider=provider,
                   source_release=f'{provider}:{run_date}:{cycle:02d}Z', run_date=run_date,
                   cycle=cycle, member=member, forecast_hour=hour, valid_utc=valid_at.utc,
                   parameter='2t', unit='K', stations=stations)
    raw = tc.canonical(message)
    index = tc.canonical([dict(offset=0, length=len(raw))])
    raw_sha = put(raw)
    idx_sha = put(index)
    response_completed_at = bind_clock(response_completed_at, raw_sha)
    ev = tc.CaptureEvidence(request_url=f'https://synthetic.invalid/{byte_key}',
                            range_start=0, range_end=len(raw)-1, resource_size=len(raw),
                            request_started_at=receipt_ts(response_utc - 100, uncertainty=0),
                            response_completed_at=response_completed_at, http_status=206,
                            content_range=f'bytes 0-{len(raw)-1}/{len(raw)}',
                            response_headers=(('content-range', f'bytes 0-{len(raw)-1}/{len(raw)}'),
                                              ('content-length', str(len(raw))),
                                              ('etag', sha(byte_key))), raw_bytes=raw, index_bytes=index,
                            clock_evidence_bytes=resolve(response_completed_at.evidence_sha256), sealed=True)
    extraction = tc.ExtractionEvidence(raw_sha256=raw_sha, index_sha256=idx_sha,
                                       provider=provider, source_release=f'{provider}:{run_date}:{cycle:02d}Z',
                                       run_date=run_date, cycle=cycle, member=member,
                                       forecast_hour=hour, valid_utc=valid_at.utc,
                                       parameter='2t', unit='K', response_range_start=0,
                                       response_range_end=len(raw)-1, message_offset=0,
                                       message_length=len(raw), station_id=station_id,
                                       station_version=station_version,
                                       grid=grid, value_native_k=stations[station_id]['value_native_k'],
                                       decoder_sha256=put(b'decoder-v1'), extraction_sha256=sha('placeholder'),
                                       code_sha256=put(b'code-v1'), policy_sha256=policy_sha,
                                       dependency_sha256=put(b'deps-v1'))
    extraction = replace(extraction, extraction_sha256=tc.extraction_digest(extraction))
    put(tc.extraction_manifest_bytes(extraction))
    feature_ready_at = bind_clock(receipt_ts(response_utc + feature_offset), extraction.extraction_sha256)
    put(ev.manifest_bytes)
    return tc.TrajectoryPoint(
        contract_identity=tc.CONTRACT_ID, provider=provider, source_release=f'{provider}:{run_date}:{cycle:02d}Z',
        release_binding='OBSERVED_HEADER_ONLY', run_date=run_date, cycle=cycle, member=member,
        forecast_hour=hour, run_initialized_at=run_initialized_at, valid_at=valid_at,
        source_published_at=None, response_completed_at=response_completed_at,
        feature_ready_at=feature_ready_at, value_native_k=stations[station_id]['value_native_k'],
        conversion=tc.UnitConversion(original_unit='K', stored_unit='C', formula='SUBTRACT_273.15'),
        grid=grid,
        capture=tc.CaptureRef(byte_sha256=raw_sha, index_sha256=idx_sha,
                               store=f'private-evidence/r09-trajectory-cas/{raw_sha}', immutable=True, evidence=ev),
        extraction=extraction,
        rule_version=rule_version, station_version=station_version)


def full_points(target_date=TARGET_DATE, provider='GEFS', **kwargs):
    return [make_point(m, h, target_date=target_date, provider=provider, **kwargs)
            for m in range(PROVIDERS[provider]) for h in EXPECTED_HOURS]


def cohort():
    return tuple((station, f'{station}-event', 'temperature-rule', date, 'daily_high_temperature')
                 for station in ('KATL', 'KORD', 'KDEN')
                 for date in ('2027-01-10', '2027-01-15', '2027-02-10', '2027-03-05'))


def coverage_policy(required_providers=('GEFS',), extra_expected=None, requested=None,
                    fallback_mode='NONE', expected_hours=EXPECTED_HOURS):
    requested = requested or cohort()
    trial_keys = sorted({(station, event, date) for station, event, _, date, _ in requested})
    schedule = tuple((key, protocol_ts(local_day(key[2]).start_utc - 3600,
                                        'decision_at')) for key in trial_keys)
    expected = {'GEFS': tc.ExpectedCoverage(provider='GEFS', expected_members=tuple(range(PROVIDERS['GEFS'])),
                                             expected_hours=expected_hours,
                                             native_window_hours=(expected_hours[0], expected_hours[-1]))}
    if extra_expected:
        expected.update(extra_expected)
    policy = tc.CoveragePolicy(required_providers=required_providers, expected=expected,
                              preregistered_at=protocol_ts(local_day().start_utc - 30 * 86400, 'decision_at'),
                              requested_cohort=requested, decision_schedule=schedule,
                              max_run_age_seconds=24 * 3600,
                              run_selection='LATEST_COMPLETE_READY',
                              allowed_cycles=(0, 6, 12, 18),
                              fallback_mode=fallback_mode)
    return replace(policy, preregistered_at=bind_clock(policy.preregistered_at, policy.protocol_subject))


def clocks(target_date=TARGET_DATE, decision_offset=3600, freeze_offset=60):
    day = local_day(target_date)
    decision_at = protocol_ts(day.start_utc - decision_offset, 'decision_at')
    prediction_frozen_at = receipt_ts(decision_at.utc + freeze_offset, uncertainty=0)
    return tc.CausalClocks(decision_at=decision_at, prediction_frozen_at=prediction_frozen_at)


def label(target_date=TARGET_DATE, label_offset=3600, label_version=None, revision_of=None, status='FINAL',
          bucket_count=10, winner_bucket=3, station_id='KATL', station_version=STATION_VERSION,
          rule_version=RULE_VERSION, family='daily_high_temperature', zone=ZONE):
    day = local_day(target_date)
    source_revision_id = label_version or f'label-v1:{station_id}:{target_date}:{family}'
    station_metadata_sha = put(tc.canonical(dict(station_id=station_id,
        event_id=f'{station_id}-event', station_version=station_version,
        settlement_timezone=zone)))
    station_subject = tc.station_metadata_subject(station_id, f'{station_id}-event',
                                                  station_version, station_metadata_sha)
    rule_subject = tc.rule_metadata_subject('temperature-rule', rule_version, family,
                                            'CELSIUS', 'NEAREST_INTEGER', tuple(range(bucket_count + 1)))
    target = tc.SettlementTarget(station_id=station_id, event_id=f'{station_id}-event',
                                 rule_id='temperature-rule', station_version=station_version,
                                 rule_version=rule_version, target_date=target_date, family=family,
                                 bucket_count=bucket_count, unit='CELSIUS', rounding='NEAREST_INTEGER',
                                 bucket_edges=tuple(range(bucket_count + 1)),
                                 settlement_timezone=zone,
                                 station_metadata_sha256=station_metadata_sha,
                                 station_metadata_available_at=bind_clock(receipt_ts(day.start_utc - 2 * 86400), station_subject),
                                 rule_metadata_available_at=bind_clock(receipt_ts(day.start_utc - 2 * 86400), rule_subject))
    version = tc.label_version_for(source_revision_id=source_revision_id, family=family,
        bucket_count=bucket_count, winner_bucket=winner_bucket, status=status,
        revision_of=revision_of, target=target)
    knowable = bind_clock(protocol_ts(day.end_utc_exclusive + label_offset, 'label_knowable_at'), version)
    fact = tc.LabelFact(label_version=version, label_knowable_at=knowable,
                         family=family, bucket_count=bucket_count, winner_bucket=winner_bucket,
                         status=status, revision_of=revision_of, target=target,
                         source_revision_id=source_revision_id)
    put(tc.label_payload_bytes(fact))
    return fact


def reseal_label(fact):
    version = tc.label_version_for(source_revision_id=fact.source_revision_id,
        family=fact.family, bucket_count=fact.bucket_count, winner_bucket=fact.winner_bucket,
        status=fact.status, revision_of=fact.revision_of, target=fact.target)
    fact = replace(fact, label_version=version,
                   label_knowable_at=bind_clock(fact.label_knowable_at, version))
    put(tc.label_payload_bytes(fact))
    return fact


def split_cutoffs(target_date=TARGET_DATE, fit_offset=3600, dev_gap=200000, conf_gap=200000):
    lab = label(target_date=target_date, label_offset=fit_offset)
    fit_cutoff = protocol_ts(lab.label_knowable_at.utc + 60, 'fit_cutoff')
    selection_freeze_at = protocol_ts(fit_cutoff.utc + dev_gap, 'selection_freeze_at')
    evaluation_asof = protocol_ts(selection_freeze_at.utc + conf_gap, 'evaluation_asof')
    return dict(TRAIN=fit_cutoff, DEVELOPMENT=selection_freeze_at, CONFIRMATION=evaluation_asof)


def artifact_for(split, cutoffs, policy, target_date):
    prior = cutoffs['TRAIN' if split == 'DEVELOPMENT' else 'DEVELOPMENT']
    payload = f'synthetic-parameters:{split}:{target_date}'.encode()
    return tc.TrainedArtifact(artifact_id=put(payload), produced_after_split='TRAIN' if split == 'DEVELOPMENT' else 'DEVELOPMENT',
                              split_cutoffs_sha256=tc.split_cutoffs_sha256(cutoffs),
                              available_at=bind_clock(receipt_ts(prior.utc + 60, uncertainty=0),
                                                      hashlib.sha256(payload).hexdigest()), artifact_bytes=payload,
                              family='daily_high_temperature', bucket_partition_sha256=tc.digest(list(range(11))),
                              coverage_policy_id=policy.policy_id)


def candidate_for(points):
    probe = tc.RunCandidate(provider=points[0].provider,
                            run_initialized_at=points[0].run_initialized_at,
                            ready_at=receipt_ts(max(p.feature_ready_at.conservative_upper_bound
                                                    for p in points) + 1, uncertainty=0), complete=True)
    return replace(probe, ready_at=bind_clock(probe.ready_at, probe.subject))


def inventory_for(points, policy, decision_at, selected=None):
    selected = tuple(selected or (candidate_for(points),))
    providers = set(policy.required_providers)
    slots = tc.run_candidate_slots(policy, decision_at.conservative_lower_bound)
    candidates = list(selected)
    for provider in providers:
        for stamp in slots:
            if any(c.provider == provider and c.run_initialized_at.utc == stamp for c in candidates):
                continue
            candidate = tc.RunCandidate(provider=provider, run_initialized_at=declared_ts(stamp),
                ready_at=receipt_ts(min(decision_at.utc - 120, stamp + 60), uncertainty=0),
                complete=False, status='UNAVAILABLE')
            candidates.append(replace(candidate, ready_at=bind_clock(candidate.ready_at, candidate.subject)))
    candidates = tuple(candidates)
    inventory_sha = put(tc.run_inventory_bytes(policy, decision_at, candidates, providers))
    return candidates, inventory_sha


def refresh_inventory(ex, selected=None):
    candidates, identity = inventory_for(ex['points'], ex['coverage_policy'],
                                         ex['clocks'].decision_at, selected=selected)
    ex['run_candidates'] = candidates
    ex['run_inventory_sha256'] = identity
    return ex


def baseline_example(city_day='KATL|2027-01-15', split='TRAIN', target_date=TARGET_DATE,
                     station_version=STATION_VERSION, rule_version=RULE_VERSION, cutoffs=None, policy=None):
    station_id = city_day.split('|')[0].replace('_ALT', '')
    points = full_points(target_date=target_date, station_version=station_version,
                         rule_version=rule_version, station_id=station_id)
    cutoffs = cutoffs or split_cutoffs(target_date)
    policy = policy or coverage_policy()
    decision_at = clocks(target_date).decision_at
    candidates, inventory_sha = inventory_for(points, policy, decision_at)
    return dict(city_day=city_day, split=split, points=points,
                coverage_policy=policy, local_day=local_day(target_date), clocks=clocks(target_date),
                label_history=(label(target_date, station_id=station_id, station_version=station_version,
                                     rule_version=rule_version),), split_cutoffs=cutoffs,
                capture_registry=tc.CaptureRegistry(), label_registry=tc.LabelVersionRegistry(),
                station_id=station_id, rule_id='temperature-rule', evidence_resolver=resolve,
                run_candidates=candidates, run_inventory_sha256=inventory_sha,
                artifact=artifact_for(split, cutoffs, policy, target_date) if split != 'TRAIN' else None)


# --- baseline sanity -------------------------------------------------------

def test_baseline_example_validates_and_admits_nothing_real():
    record = tc.validate_example(**baseline_example())
    assert record.learner_admitted is False
    assert record.evidence_class == 'SYNTHETIC'
    assert record.financial_authority is False
    assert record.coverage.required == record.coverage.present_all
    assert record.out_of_day_points == 0


def test_never_admits_a_real_example():
    with pytest.raises(PanelError, match='REAL_ADAPTER_NOT_IMPLEMENTED'):
        tc.validate_example(**{**baseline_example(), 'evidence_class': 'REAL'})


# --- identity must never alias the legacy contracts -------------------------

def _point_kwargs(contract_identity):
    return {**make_point(0, 12).__dict__, 'contract_identity': contract_identity}


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
        tc.validate_coverage(points, coverage_policy())


def test_late_partial_receipt_still_covers_but_fails_decision_availability():
    # All expected points arrive (coverage complete), but member 5's hour-24
    # message lands after decision_at: a late-partial receipt must still gate.
    decision_at_utc = clocks().decision_at.utc
    late_offset = (decision_at_utc - RUN_UTC) + 3600  # 1h after the real cutoff
    points = [p for p in full_points() if not (p.member == 5 and p.forecast_hour == 24)]
    points.append(make_point(5, 24, receipt_offset=late_offset))
    coverage = tc.validate_coverage(points, coverage_policy())
    assert set(coverage.required) == set(coverage.present_all)  # coverage complete; only the clock gate fails
    with pytest.raises(PanelError, match='FEATURE_NOT_AVAILABLE_BY_DECISION'):
        tc.validate_example(**{**baseline_example(), 'points': points})


# --- 2. future-valid but already-received forecasts (legitimate) ------------

def test_future_valid_forecast_already_received_is_admitted_and_tagged_out_of_day():
    day = local_day()
    points = full_points()
    future_hour = int((day.end_utc_exclusive - RUN_UTC) / 3600) + 10
    points.append(make_point(0, future_hour, receipt_offset=3 * 3600))
    coverage = tc.validate_coverage(points, coverage_policy())
    assert ('GEFS', 0, future_hour) in coverage.extra_native_points
    record = tc.validate_example(**{**baseline_example(), 'points': points})
    assert record.out_of_day_points == 1
    assert record.points == PROVIDERS['GEFS'] * len(EXPECTED_HOURS) + 1


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
    ld = local_day(day, zone)
    assert ld.end_utc_exclusive - ld.start_utc == hours * 3600


def test_local_day_geometry_tamper_rejected():
    start, end = tc.local_day_window(TARGET_DATE, ZONE)
    with pytest.raises(PanelError, match='LOCAL_DAY_GEOMETRY_MISMATCH'):
        tc.LocalDay(target_date=TARGET_DATE, timezone=ZONE, start_utc=start - 1, end_utc_exclusive=end,
                    timezone_file_sha256=file_sha(Path('/usr/share/zoneinfo') / ZONE))


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
                     bucket_count=10, winner_bucket=0, status='FINAL', revision_of=None,
                     target=label().target)


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
    r2 = tc.validate_example(**{**baseline_example(city_day='KATL|2027-01-15', split='TRAIN',
                                                   rule_version=sha('rule-v2')),
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
    r2 = tc.validate_example(**{**baseline_example(city_day='KORD|2027-01-15', station_version=sha('station-kord')),
                                 'points': full_points(station_version=sha('station-kord'), station_id='KORD')})
    tc.validate_no_duplicate_city_days([r1, r2])  # no raise


# --- 8. clock uncertainty --------------------------------------------------------

def test_clock_uncertainty_crossing_decision_cutoff_fails_even_if_nominal_passes():
    decision_at = clocks().decision_at
    nominal_receipt = decision_at.utc - 5  # nominal value is safely before cutoff
    response_completed_at = receipt_ts(nominal_receipt, uncertainty=30.0)
    feature_ready_at = receipt_ts(nominal_receipt + 1, uncertainty=30.0)
    assert feature_ready_at.utc <= decision_at.utc  # nominal value passes
    assert feature_ready_at.conservative_upper_bound > decision_at.utc  # but uncertainty crosses it
    point = replace(make_point(0, 12), response_completed_at=response_completed_at,
                    feature_ready_at=feature_ready_at)
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
    point = make_point(0, 12)
    args = dict(raw_key=('GEFS', RUN_DATE, CYCLE, 0, 12), station_id=point.extraction.station_id,
                station_version=point.station_version,
                capture=point.capture, value_native_k=point.value_native_k, grid=point.grid,
                receipt=point.response_completed_at, extraction=point.extraction,
                source_published_at=point.source_published_at, evidence_resolver=resolve)
    assert registry.register(**args) is True
    assert registry.register(**args) is False


def test_immutable_replay_conflicting_overwrite_rejected():
    registry = tc.CaptureRegistry()
    point = make_point(0, 12)
    other = make_point(0, 12, byte_key='different-raw')
    for p in (point, other):
        args = dict(raw_key=('GEFS', RUN_DATE, CYCLE, 0, 12), station_id=p.extraction.station_id,
                    station_version=p.station_version,
                    capture=p.capture, value_native_k=p.value_native_k, grid=p.grid,
                    receipt=p.response_completed_at, extraction=p.extraction,
                    source_published_at=p.source_published_at, evidence_resolver=resolve)
        if p is point:
            assert registry.register(**args)
        else:
            with pytest.raises(PanelError, match='IMMUTABLE_RAW_MESSAGE_CONFLICT'):
                registry.register(**args)


# --- 10. legitimate cadence gaps vs missing messages --------------------------------

def test_unregistered_hour_inside_native_window_is_rejected():
    points = full_points()
    # Hour 15 is not in the frozen twelve-hour native cadence/window.
    points.append(make_point(0, 15))
    with pytest.raises(PanelError, match='UNREGISTERED_NATIVE_HOUR'):
        tc.validate_coverage(points, coverage_policy())


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
    prediction_frozen_at = receipt_ts(decision_at.utc - 10, uncertainty=0)
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
    policy = coverage_policy()
    for split, city, station in (('TRAIN', 'KATL', STATION_VERSION), ('DEVELOPMENT', 'KORD', sha('station-kord')),
                                  ('CONFIRMATION', 'KDEN', sha('station-kden'))):
        d = dates[split]
        records.append(tc.validate_example(**baseline_example(
            city_day=f'{city}|{d}', split=split, target_date=d, station_version=station,
            cutoffs=cutoffs, policy=policy)))
    result = tc.validate_corpus(records, cutoffs, policy, resolve)
    assert result['admitted_real_examples'] == 0
    assert result['historical_541_day_admissions'] == 0
    assert result['splits'] == dict(TRAIN=1, DEVELOPMENT=1, CONFIRMATION=1)
    assert all(not r.financial_authority and not r.learner_admitted for r in records)


# === Gate-2 independent review (fed1cbe) repair tests ===============================

# --- F1: every dependency's conservative receipt bound gates, not only feature_ready ---

def test_gate2_review_f1_receipt_uncertainty_cannot_be_erased_by_a_precise_feature_timestamp():
    decision_at = clocks().decision_at
    # Nominal receipt is 2s before decision, but its 120s uncertainty conservatively
    # extends 118s past decision. A fresh, zero-uncertainty feature timestamp taken
    # 1s later must not erase that.
    nominal_receipt = decision_at.utc - 2
    response_completed_at = receipt_ts(nominal_receipt, uncertainty=120.0)
    feature_ready_at = receipt_ts(nominal_receipt + 1, uncertainty=0)
    assert feature_ready_at.conservative_upper_bound <= decision_at.utc  # feature side alone looks fine
    assert response_completed_at.conservative_upper_bound > decision_at.utc  # receipt side is not
    point = replace(make_point(0, 12), response_completed_at=response_completed_at,
                    feature_ready_at=feature_ready_at)
    points = [p for p in full_points() if not (p.member == 0 and p.forecast_hour == 12)] + [point]
    with pytest.raises(PanelError, match='FEATURE_NOT_AVAILABLE_BY_DECISION'):
        tc.validate_example(**{**baseline_example(), 'points': points})


# --- F2: fit/selection/prediction ordering, and FUTURE_FORECAST day boundary ---

def test_gate2_review_f2_development_prediction_before_fit_cutoff_rejected():
    target_date = '2027-02-10'
    day = local_day(target_date)
    cl = clocks(target_date)
    lab = label(target_date)
    fit_cutoff = protocol_ts(cl.prediction_frozen_at.utc + 10, 'fit_cutoff')  # fit "freezes" after this prediction
    selection_freeze_at = protocol_ts(lab.label_knowable_at.utc + 60, 'selection_freeze_at')
    evaluation_asof = protocol_ts(selection_freeze_at.utc + 200000, 'evaluation_asof')
    cutoffs = dict(TRAIN=fit_cutoff, DEVELOPMENT=selection_freeze_at, CONFIRMATION=evaluation_asof)
    with pytest.raises(PanelError, match='DEVELOPMENT_PREDICTION_BEFORE_FIT_CUTOFF'):
        tc.validate_example(**baseline_example(city_day=f'KATL|{target_date}', split='DEVELOPMENT',
                                               target_date=target_date, cutoffs=cutoffs))


def test_gate2_review_f2_confirmation_prediction_before_selection_freeze_rejected():
    target_date = '2027-03-05'
    day = local_day(target_date)
    cl = clocks(target_date)
    lab = label(target_date)
    fit_cutoff = protocol_ts(cl.prediction_frozen_at.utc - 400000, 'fit_cutoff')
    selection_freeze_at = protocol_ts(cl.prediction_frozen_at.utc + 10, 'selection_freeze_at')  # after this prediction
    evaluation_asof = protocol_ts(lab.label_knowable_at.utc + 60, 'evaluation_asof')
    cutoffs = dict(TRAIN=fit_cutoff, DEVELOPMENT=selection_freeze_at, CONFIRMATION=evaluation_asof)
    with pytest.raises(PanelError, match='CONFIRMATION_PREDICTION_BEFORE_SELECTION_FREEZE'):
        tc.validate_example(**baseline_example(city_day=f'KDEN|{target_date}', split='CONFIRMATION',
                                               target_date=target_date, cutoffs=cutoffs))


def test_gate2_review_f2_prediction_frozen_after_local_day_start_rejected():
    example = baseline_example()
    day = example['local_day']
    late_decision = protocol_ts(day.start_utc + 10, 'decision_at')
    late_freeze = receipt_ts(late_decision.utc + 60, uncertainty=0)
    example['clocks'] = tc.CausalClocks(decision_at=late_decision, prediction_frozen_at=late_freeze)
    with pytest.raises(PanelError, match='PREDICTION_NOT_FROZEN_BEFORE_LOCAL_DAY_START'):
        tc.validate_example(**example)


# --- F3: validate_corpus only accepts typed records bound to the exact cutoffs/policy used ---

def test_gate2_review_f3_corpus_rejects_records_that_are_not_validated_examples():
    record = tc.validate_example(**baseline_example())
    with pytest.raises(PanelError, match='CORPUS_RECORDS_MUST_BE_VALIDATED_EXAMPLES'):
        tc.validate_corpus([record.__dict__], split_cutoffs(), coverage_policy(), resolve)


def test_gate2_review_f3_corpus_rejects_detached_cutoffs():
    record = tc.validate_example(**baseline_example())
    other_cutoffs = split_cutoffs(fit_offset=7200)
    with pytest.raises(PanelError, match='DETACHED_CORPUS_CUTOFFS'):
        tc.validate_corpus([record], other_cutoffs, coverage_policy(), resolve)


def test_gate2_review_f3_corpus_rejects_mixed_coverage_policy():
    cutoffs = split_cutoffs()
    full_policy = coverage_policy()
    shortened_policy = coverage_policy(expected_hours=(12,))
    r1 = tc.validate_example(**{**baseline_example(city_day='KATL|2027-01-15'),
                                 'coverage_policy': full_policy,
                                 'split_cutoffs': cutoffs})
    second = baseline_example(city_day='KORD|2027-01-15', station_version=sha('station-kord'))
    second['points'] = [p for p in second['points'] if p.forecast_hour == 12]
    second['coverage_policy'] = shortened_policy
    second['split_cutoffs'] = cutoffs
    r2 = tc.validate_example(**refresh_inventory(second))
    with pytest.raises(PanelError, match='CORPUS_COVERAGE_POLICY_MUST_BE_FROZEN'):
        tc.validate_corpus([r1, r2], cutoffs, full_policy, resolve)


def test_gate2_review_f3_cross_split_embargo_checked_for_nonadjacent_splits_without_middle():
    # TRAIN and CONFIRMATION present, DEVELOPMENT absent: leakage between the two
    # remaining splits must still be caught, not skipped because they are not adjacent.
    before = dict(split='TRAIN', label_knowable_upper_bound=2000.0)
    after = dict(split='CONFIRMATION', decision_at=1000.0)
    with pytest.raises(PanelError, match='CROSS_SPLIT_LABEL_EMBARGO_LEAKAGE'):
        tc.validate_cross_split_embargo([before, after])


def test_gate2_review_f3_standalone_example_call_still_enforces_full_cutoff_set():
    with pytest.raises(PanelError, match='SPLIT_CUTOFF_SET'):
        tc.validate_example(**{**baseline_example(), 'split_cutoffs': {'TRAIN': split_cutoffs()['TRAIN']}})


# --- F4: run/release identity binding ---

def test_gate2_review_f4_run_date_cycle_must_match_initialization():
    with pytest.raises(PanelError, match='RUN_DATE_CYCLE_MUST_MATCH_INITIALIZATION'):
        make_point(0, 12, run_date='1900-01-01', cycle=18)


def test_gate2_review_f4_mixed_source_release_within_example_rejected():
    other_day_points = full_points(target_date='2027-01-14')
    points = full_points()[:-1] + [other_day_points[0]]
    with pytest.raises(PanelError, match='MULTIPLE_SOURCE_RELEASES_IN_EXAMPLE'):
        tc.validate_example(**{**baseline_example(), 'points': points})


# --- F5: canonical station/day identity binds duplicate detection and city_day ---

def test_gate2_review_f5_station_drift_within_example_rejected():
    points = full_points()
    tampered = make_point(points[-1].member, points[-1].forecast_hour,
                          station_version=sha('station-other'))
    points = points[:-1] + [tampered]
    with pytest.raises(PanelError, match='STATION_DRIFT_WITHIN_EXAMPLE'):
        tc.validate_station_consistency(points)


def test_gate2_review_f5_city_day_target_date_mismatch_rejected():
    with pytest.raises(PanelError, match='CITY_DAY_TARGET_DATE_MISMATCH'):
        tc.validate_example(**{**baseline_example(), 'city_day': 'KATL|1999-12-31'})


def test_gate2_review_f5_alias_duplicate_caught_via_station_identity_not_city_day_spelling():
    r1 = tc.validate_example(**baseline_example(city_day='KATL|2027-01-15'))
    r2 = tc.validate_example(**{**baseline_example(city_day='KATL_ALT|2027-01-15')})  # same station_version, alias label
    with pytest.raises(PanelError, match='DUPLICATE_CITY_DAY'):
        tc.validate_no_duplicate_city_days([r1, r2])


# --- F6: label lineage/finality/target-partition are integrated admission predicates ---

def test_gate2_review_f6_pending_label_not_admitted():
    with pytest.raises(PanelError, match='LABEL_NOT_FINAL_FOR_ADMISSION'):
        tc.validate_example(**{**baseline_example(), 'label_history': (label(status='PENDING'),)})


def test_gate2_review_f6_winner_bucket_out_of_declared_partition_rejected():
    with pytest.raises(PanelError, match='LABEL_WINNER_BUCKET_RANGE'):
        label(bucket_count=10, winner_bucket=999999)


def test_gate2_review_f6_label_version_content_rewrite_across_separate_calls_rejected():
    registry = tc.LabelVersionRegistry()
    v1 = label(label_version=sha('shared-version'), winner_bucket=3)
    v2 = replace(v1, winner_bucket=7)  # same version, different content
    registry.register(v1)
    with pytest.raises(PanelError, match='LABEL_VERSION_CONTENT_REWRITE'):
        registry.register(v2)


def test_gate2_review_f6_orphan_correction_without_lineage_rejected():
    orphan = label(label_version=sha('orphan-v2'), revision_of=sha('never-existed'))
    with pytest.raises(PanelError, match='FIRST_LABEL_NOT_REVISION'):
        tc.validate_label_lineage([orphan])


# --- F7: capture registry is actually invoked, binds semantic content, and tzdata is pinned ---

def test_gate2_review_f7_capture_registry_rejects_byte_conflict_across_examples():
    shared_registry = tc.CaptureRegistry()
    example = {**baseline_example(), 'capture_registry': shared_registry}
    tc.validate_example(**example)  # first pass registers every point's capture
    other = baseline_example(city_day='KORD|2027-01-15', station_version=sha('station-kord'))
    tampered_points = [make_point(0, 12, station_version=sha('station-kord'), station_id='KORD',
                                  byte_key='different-raw')] + other['points'][1:]
    with pytest.raises(PanelError, match='IMMUTABLE_RAW_MESSAGE_CONFLICT'):
        tc.validate_example(**{**other, 'points': tampered_points, 'capture_registry': shared_registry})


def test_gate2_review_f7_capture_registry_rejects_semantic_rewrite_under_same_byte_digest():
    shared_registry = tc.CaptureRegistry()
    example = {**baseline_example(), 'capture_registry': shared_registry}
    tc.validate_example(**example)
    original = full_points()[0]
    # The strengthened point constructor refuses a geometry rewrite even before
    # registry admission; no shared registry is needed to authenticate it.
    with pytest.raises(PanelError, match='EXTRACTION_POINT_MISMATCH'):
        replace(original, grid=replace(original.grid, nearest_lat=34.0))


def test_gate2_review_f7_local_day_timezone_file_hash_tamper_rejected():
    start, end = tc.local_day_window(TARGET_DATE, ZONE)
    with pytest.raises(PanelError, match='LOCAL_DAY_TIMEZONE_FILE_HASH'):
        tc.LocalDay(target_date=TARGET_DATE, timezone=ZONE, start_utc=start, end_utc_exclusive=end,
                    timezone_file_sha256=sha('wrong-tzdata'))


# --- F8: expected coverage is bound to a frozen, full-provider-set protocol ---

def test_gate2_review_f8_absent_required_provider_is_reported_not_dropped():
    policy = coverage_policy(
        required_providers=('AIFS', 'GEFS', 'IFS'),
        extra_expected={
            'IFS': tc.ExpectedCoverage(provider='IFS', expected_members=tuple(range(PROVIDERS['IFS'])),
                                        expected_hours=EXPECTED_HOURS),
            'AIFS': tc.ExpectedCoverage(provider='AIFS', expected_members=tuple(range(PROVIDERS['AIFS'])),
                                         expected_hours=EXPECTED_HOURS),
        })
    coverage = tc.validate_coverage(full_points(), policy)
    assert coverage.absent_providers == ('AIFS', 'IFS')


def test_gate2_review_f8_shrinking_expectation_to_match_received_data_rejected():
    # A caller cannot silently drop an expected hour to make an incomplete receipt
    # look complete: ExpectedCoverage itself only accepts the full member set, and
    # the resulting policy_id differs from the frozen one used elsewhere, so a
    # corpus mixing the two is caught by CORPUS_COVERAGE_POLICY_MUST_BE_FROZEN.
    shrunk = coverage_policy(expected_hours=(12,))
    cutoffs = split_cutoffs()
    r1 = tc.validate_example(**{**baseline_example(city_day='KATL|2027-01-15'), 'split_cutoffs': cutoffs})
    second = baseline_example(city_day='KORD|2027-01-15', station_version=sha('station-kord'))
    second['points'] = [p for p in second['points'] if p.forecast_hour == 12]
    second['coverage_policy'] = shrunk
    second['split_cutoffs'] = cutoffs
    r2 = tc.validate_example(**refresh_inventory(second))
    with pytest.raises(PanelError, match='CORPUS_COVERAGE_POLICY_MUST_BE_FROZEN'):
        tc.validate_corpus([r1, r2], cutoffs, coverage_policy(), resolve)


# R1-R7: adversarial controls from the exact 2d116af independent review.

def heldout_example():
    cutoffs = dict(
        TRAIN=protocol_ts(label('2027-01-10').label_knowable_at.utc + 60, 'fit_cutoff'),
        DEVELOPMENT=protocol_ts(label('2027-02-10').label_knowable_at.utc + 60, 'selection_freeze_at'),
        CONFIRMATION=protocol_ts(label('2027-03-05').label_knowable_at.utc + 60, 'evaluation_asof'))
    return baseline_example(city_day='KORD|2027-02-10', split='DEVELOPMENT',
                            target_date='2027-02-10', station_version=sha('station-kord'),
                            cutoffs=cutoffs)


@pytest.mark.parametrize('offset,uncertainty', [(0, 0), (-1, 120)])
def test_r1_freeze_boundary_equality_and_uncertainty_rejected(offset, uncertainty):
    ex = baseline_example()
    freeze = receipt_ts(ex['local_day'].start_utc + offset, uncertainty=uncertainty)
    ex['clocks'] = replace(ex['clocks'], prediction_frozen_at=freeze)
    with pytest.raises(PanelError, match='PREDICTION_NOT_FROZEN_BEFORE_LOCAL_DAY_START'):
        tc.validate_example(**ex)


def test_r1_artifact_must_be_available_by_decision_and_match_target_policy():
    ex = heldout_example()
    late = bind_clock(receipt_ts(ex['clocks'].decision_at.utc + 1, uncertainty=0),
                      ex['artifact'].artifact_id)
    with pytest.raises(PanelError, match='DEVELOPMENT_PREDICTION_BEFORE_FIT_CUTOFF'):
        tc.validate_example(**{**ex, 'artifact': replace(ex['artifact'], available_at=late)})
    with pytest.raises(PanelError, match='ARTIFACT_TARGET_POLICY_MISMATCH'):
        tc.validate_example(**{**ex, 'artifact': replace(ex['artifact'],
            bucket_partition_sha256=sha('wrong-partition'))})


def test_r1_artifact_available_at_decision_boundary_is_accepted():
    ex = heldout_example()
    at_decision = bind_clock(receipt_ts(ex['clocks'].decision_at.utc, uncertainty=0),
                             ex['artifact'].artifact_id)
    record = tc.validate_example(**{**ex, 'artifact': replace(ex['artifact'], available_at=at_decision)})
    assert record.evidence_class == 'SYNTHETIC'


def reseal(record, **changes):
    data = {**record.__dict__, **changes}
    names = inspect.signature(tc._validated_example_seal).parameters
    data['content_seal'] = tc._validated_example_seal(**{name: data[name] for name in names})
    return replace(record, **{**changes, 'content_seal': data['content_seal']})


@pytest.mark.parametrize('change', [{'decision_at': 1.0},
                                     {'label_knowable_upper_bound': 1.0}])
def test_r2_resealed_public_record_cannot_bypass_source_revalidation(change):
    ex = baseline_example()
    record = tc.validate_example(**ex)
    forged = reseal(record, **change)
    with pytest.raises(PanelError, match='CORPUS_RECORD_NOT_SOURCE_REVALIDATION'):
        tc.validate_corpus([forged], ex['split_cutoffs'], ex['coverage_policy'], resolve)


def test_r2_inconsistent_point_count_is_rejected_before_corpus():
    record = tc.validate_example(**baseline_example())
    with pytest.raises(PanelError, match='VALIDATED_EXAMPLE_POINT_COUNT_INCONSISTENT'):
        reseal(record, points=999)


def test_r2_revalidated_source_bytes_require_injected_evidence():
    ex = baseline_example()
    record = tc.validate_example(**ex)
    bad_resolver = lambda identity: b'forged' if identity == ex['points'][0].capture.byte_sha256 else resolve(identity)
    with pytest.raises(PanelError, match='INJECTED_EVIDENCE_VERIFICATION_FAILED'):
        tc.validate_corpus([record], ex['split_cutoffs'], ex['coverage_policy'], bad_resolver)


def test_r2_target_metadata_bytes_are_reverified_at_corpus_boundary():
    ex = baseline_example()
    record = tc.validate_example(**ex)
    station_digest = ex['label_history'][0].target.station_version
    missing = lambda identity: None if identity == station_digest else resolve(identity)
    with pytest.raises(PanelError, match='INJECTED_DEPENDENCY_BYTES_VERIFICATION_FAILED'):
        tc.validate_corpus([record], ex['split_cutoffs'], ex['coverage_policy'], missing)


def test_r2_revalidated_complete_source_rejects_missing_message_even_with_old_summary():
    ex = baseline_example()
    record = tc.validate_example(**ex)
    altered_inputs = replace(record.source_inputs, points=record.source_inputs.points[:-1])
    forged = replace(record, source_inputs=altered_inputs)
    with pytest.raises(PanelError, match='MISSING_EXPECTED_MESSAGE'):
        tc.validate_corpus([forged], ex['split_cutoffs'], ex['coverage_policy'], resolve)


def test_r3_label_transplant_across_station_rejected():
    ex = baseline_example(city_day='KORD|2027-01-15', station_version=sha('station-kord'))
    ex['label_history'] = (label(station_id='KATL'),)
    with pytest.raises(PanelError, match='SETTLEMENT_TARGET_STATION_MISMATCH'):
        tc.validate_example(**ex)


@pytest.mark.parametrize('role', ['station', 'rule'])
def test_r3_metadata_boundary_and_uncertainty_rejected(role):
    ex = baseline_example()
    target = ex['label_history'][0].target
    subject = (tc.station_metadata_subject(target.station_id, target.event_id,
               target.station_version, target.station_metadata_sha256)
               if role == 'station' else tc.rule_metadata_subject(target.rule_id, target.rule_version,
               target.family, target.unit, target.rounding, target.bucket_edges))
    late = bind_clock(receipt_ts(ex['clocks'].decision_at.utc - 1, uncertainty=120), subject)
    field = f'{role}_metadata_available_at'
    changed_label = reseal_label(replace(ex['label_history'][0], target=replace(target, **{field: late})))
    with pytest.raises(PanelError, match='TARGET_METADATA_NOT_AVAILABLE_BY_DECISION'):
        tc.validate_example(**{**ex, 'label_history': (changed_label,)})


def test_r3_metadata_available_exactly_at_decision_passes():
    ex = baseline_example()
    target = ex['label_history'][0].target
    subject = tc.rule_metadata_subject(target.rule_id, target.rule_version,
        target.family, target.unit, target.rounding, target.bucket_edges)
    at_cutoff = bind_clock(receipt_ts(ex['clocks'].decision_at.utc, uncertainty=0), subject)
    changed = reseal_label(replace(ex['label_history'][0], target=replace(target,
        rule_metadata_available_at=at_cutoff)))
    record = tc.validate_example(**{**ex, 'label_history': (changed,)})
    assert record.rule_version == RULE_VERSION


def test_r3_partition_identity_is_more_than_bucket_count():
    ex = heldout_example()
    target = ex['label_history'][0].target
    edges = tuple(x * 2 for x in target.bucket_edges)
    rule_subject = tc.rule_metadata_subject(target.rule_id, target.rule_version,
                                            target.family, target.unit, target.rounding, edges)
    changed_target = replace(target, bucket_edges=edges,
                             rule_metadata_available_at=bind_clock(target.rule_metadata_available_at, rule_subject))
    changed_label = reseal_label(replace(ex['label_history'][0], target=changed_target))
    with pytest.raises(PanelError, match='ARTIFACT_TARGET_POLICY_MISMATCH'):
        tc.validate_example(**{**ex, 'label_history': (changed_label,)})


def test_r4_correction_cannot_change_family_or_partition():
    v1 = label(label_version=sha('first'))
    for v2 in (label(label_offset=7200, label_version=sha('second'), revision_of=v1.label_version,
                     family='daily_low_temperature', bucket_count=2, winner_bucket=1),
               label(label_offset=7200, label_version=sha('third'), revision_of=v1.label_version,
                     bucket_count=2, winner_bucket=1)):
        with pytest.raises(PanelError, match='LABEL_LINEAGE_TARGET_DRIFT'):
            tc.validate_label_lineage((v1, v2))


def test_r4_corpus_catches_same_label_version_different_payload_across_registries():
    version = sha('reused-cross-station-label-version')
    a = baseline_example()
    b = baseline_example(city_day='KORD|2027-01-15', station_version=sha('station-kord'))
    a['label_history'] = (label(label_version=version),)
    b['label_history'] = (replace(label(label_version=version, station_id='KORD',
                                station_version=sha('station-kord'), winner_bucket=7),
                                label_version=a['label_history'][0].label_version),)
    tc.validate_example(**a)
    with pytest.raises(PanelError, match='INJECTED_EVIDENCE_VERIFICATION_FAILED'):
        tc.validate_example(**b)


def test_r4_label_revision_selected_as_of_split_cutoff():
    ex = baseline_example()
    v1 = ex['label_history'][0]
    v2 = label(label_offset=7200, label_version=sha('late-correction'),
               revision_of=v1.label_version, winner_bucket=7)
    record = tc.validate_example(**{**ex, 'label_history': (v1, v2)})
    assert record.label_version == v1.label_version
    assert record.winner_bucket == v1.winner_bucket


@pytest.mark.parametrize('mutation,reason', [
    ({'http_status': 200}, 'CAPTURE_HTTP_STATUS'),
    ({'content_range': 'bytes 0-1/*'}, 'CAPTURE_CONTENT_RANGE'),
    ({'sealed': False}, 'CAPTURE_STATE_SEAL_CONSISTENCY'),
    ({'range_end': 999999}, 'CAPTURE_RESOURCE_SIZE'),
])
def test_r5_request_http_range_and_seal_are_typed(mutation, reason):
    ev = make_point(0, 12).capture.evidence
    with pytest.raises(PanelError, match=reason):
        replace(ev, **mutation)


def test_r5_partial_and_failed_attempts_are_typed_but_not_admitted():
    ex = baseline_example()
    point = ex['points'][0]
    partial_raw = point.capture.evidence.raw_bytes[:-1]
    partial_clock = bind_clock(point.response_completed_at,
                               hashlib.sha256(partial_raw).hexdigest())
    partial = replace(point.capture.evidence, sealed=False, state='PARTIAL',
                      raw_bytes=partial_raw, http_status=206,
                      response_completed_at=partial_clock,
                      clock_evidence_bytes=resolve(partial_clock.evidence_sha256))
    assert partial.state == 'PARTIAL'
    with pytest.raises(PanelError, match='CAPTURE_NOT_SEALED'):
        tc.validate_example(**{**ex, 'points': [replace(point, capture=replace(
            point.capture, evidence=partial))] + ex['points'][1:]})
    failed_clock = bind_clock(point.response_completed_at, hashlib.sha256(b'').hexdigest())
    failed = replace(point.capture.evidence, sealed=False, state='FAILED',
                     raw_bytes=b'', index_bytes=b'', http_status=503,
                     content_range='', response_headers=(),
                     response_completed_at=failed_clock,
                     clock_evidence_bytes=resolve(failed_clock.evidence_sha256))
    assert failed.state == 'FAILED'


def test_r5_injected_header_manifest_and_clock_evidence_are_verified():
    ex = baseline_example()
    point = ex['points'][0]
    altered = replace(point.capture.evidence, response_headers=(
        ('content-range', point.capture.evidence.content_range),
        ('content-length', str(len(point.capture.evidence.raw_bytes))), ('etag', sha('changed'))))
    changed_point = replace(point, capture=replace(point.capture, evidence=altered))
    with pytest.raises(PanelError, match='INJECTED_EVIDENCE_VERIFICATION_FAILED'):
        tc.validate_example(**{**ex, 'points': [changed_point] + ex['points'][1:]})
    with pytest.raises(PanelError, match='CAPTURE_CLOCK_EVIDENCE_MISMATCH'):
        replace(point.capture.evidence, clock_evidence_bytes=b'bad-clock')
    forged_clock = replace(point.response_completed_at, evidence_sha256=put(b'bad-clock'))
    with pytest.raises(PanelError, match='CAPTURE_CLOCK_CONTENT_MISMATCH'):
        replace(point.capture.evidence, response_completed_at=forged_clock,
                clock_evidence_bytes=b'bad-clock')


@pytest.mark.parametrize('kind', ['decoder_sha256', 'code_sha256', 'policy_sha256',
                                  'dependency_sha256', 'grid_sha256'])
def test_r5_admission_requires_actual_injected_dependency_bytes(kind):
    ex = baseline_example()
    point = ex['points'][0]
    identity = (point.grid.grid_sha256 if kind == 'grid_sha256'
                else getattr(point.extraction, kind))
    missing = lambda digest: None if digest == identity else resolve(digest)
    with pytest.raises(PanelError, match='INJECTED_DEPENDENCY_BYTES_VERIFICATION_FAILED'):
        tc.validate_example(**{**ex, 'evidence_resolver': missing})


def test_r5_request_end_boundary_equality_and_uncertainty():
    ev = make_point(0, 12).capture.evidence
    request = receipt_ts(ev.response_completed_at.conservative_lower_bound, uncertainty=0)
    assert replace(ev, request_started_at=request).sealed
    crossing = receipt_ts(request.utc, uncertainty=1)
    with pytest.raises(PanelError, match='CAPTURE_REQUEST_AFTER_RESPONSE'):
        replace(ev, request_started_at=crossing)


def test_r5_attested_release_requires_bound_publication_evidence():
    point = make_point(0, 12)
    with pytest.raises(PanelError, match='ATTESTED_RELEASE_REQUIRES_PUBLICATION_EVIDENCE'):
        replace(point, release_binding='ATTESTED')
    publication = bind_clock(protocol_ts(point.response_completed_at.utc - 10,
                                          'source_published_at'), point.capture.byte_sha256)
    ex = baseline_example()
    changed = replace(point, release_binding='ATTESTED', source_published_at=publication)
    record = tc.validate_example(**{**ex, 'points': [changed] + ex['points'][1:]})
    assert record.evidence_class == 'SYNTHETIC'


def reextract(point, **changes):
    extraction = replace(point.extraction, **changes)
    extraction = replace(extraction, extraction_sha256=tc.extraction_digest(extraction))
    put(tc.extraction_manifest_bytes(extraction))
    ready = bind_clock(point.feature_ready_at, extraction.extraction_sha256)
    return replace(point, extraction=extraction, value_native_k=extraction.value_native_k,
                   feature_ready_at=ready)


@pytest.mark.parametrize('field', ['decoder_sha256', 'code_sha256', 'policy_sha256',
                                    'dependency_sha256'])
def test_r5_extraction_code_policy_dependency_rewrite_conflicts(field):
    shared = tc.CaptureRegistry()
    ex = baseline_example()
    tc.validate_example(**{**ex, 'capture_registry': shared})
    point = reextract(ex['points'][0], **{field: put(('changed-' + field).encode())})
    reason = 'SYNTHETIC_STATION_EXTRACTION_MISMATCH' if field == 'policy_sha256' else 'IMMUTABLE_CAPTURE_CONFLICT'
    with pytest.raises(PanelError, match=reason):
        tc.validate_example(**{**ex, 'points': [point] + ex['points'][1:],
                               'capture_registry': shared})


def test_r5_receipt_rewrite_under_same_raw_bytes_conflicts():
    shared = tc.CaptureRegistry()
    ex = baseline_example()
    tc.validate_example(**{**ex, 'capture_registry': shared})
    later = make_point(0, 12, response_utc=ex['points'][0].response_completed_at.utc + 10)
    with pytest.raises(PanelError, match='IMMUTABLE_RAW_MESSAGE_CONFLICT'):
        tc.validate_example(**{**ex, 'points': [later] + ex['points'][1:],
                               'capture_registry': shared})


def test_r5_feature_availability_equality_and_uncertainty():
    ex = baseline_example()
    decision = ex['clocks'].decision_at.utc
    point = make_point(0, 12, response_utc=decision - 90, feature_offset=60)
    points = [point] + ex['points'][1:]
    candidate = candidate_for(points)
    candidate = replace(candidate, ready_at=bind_clock(
        receipt_ts(decision, uncertainty=0), candidate.subject))
    valid = refresh_inventory({**ex, 'points': points}, selected=(candidate,))
    assert tc.validate_example(**valid).points == len(points)
    uncertain = replace(point, feature_ready_at=bind_clock(
        receipt_ts(decision - 30, uncertainty=31), point.extraction.extraction_sha256))
    with pytest.raises(PanelError, match='FEATURE_NOT_AVAILABLE_BY_DECISION'):
        tc.validate_example(**{**valid, 'points': [uncertain] + points[1:]})


def test_r6_two_stations_share_raw_message_with_distinct_extractions():
    shared = tc.CaptureRegistry()
    a = baseline_example()
    b = baseline_example(city_day='KORD|2027-01-15', station_version=sha('station-kord'))
    b['points'] = [reextract(b['points'][0], value_native_k=281.0)] + b['points'][1:]
    a['capture_registry'] = shared
    b['capture_registry'] = shared
    records = [tc.validate_example(**a), tc.validate_example(**b)]
    report = tc.validate_corpus(records, a['split_cutoffs'], a['coverage_policy'], resolve)
    assert report['city_days'] == 2
    assert report['provider_counts']['GEFS'] == 2


def test_r7_protocol_content_mutation_changes_identity_and_is_immutable():
    policy = coverage_policy()
    with pytest.raises(TypeError):
        policy.expected['GEFS'] = tc.ExpectedCoverage('GEFS', tuple(range(31)), (12,),
                                                        native_window_hours=(12, 12))
    changed = coverage_policy(expected_hours=(12,))
    assert changed.policy_id != policy.policy_id
    assert changed.protocol_subject != policy.protocol_subject


def test_r7_latest_complete_ready_run_selection_and_freshness():
    ex = baseline_example()
    with pytest.raises(PanelError, match='RUN_CANDIDATE_INVENTORY_INCOMPLETE_OR_DUPLICATE'):
        tc.validate_example(**{**ex, 'run_candidates': ()})
    selected = ex['run_candidates'][0]
    newer = tc.RunCandidate(provider='GEFS',
        run_initialized_at=declared_ts(selected.run_initialized_at.utc + 6 * 3600),
        ready_at=receipt_ts(selected.run_initialized_at.utc + 7 * 3600, uncertainty=0), complete=True)
    newer = replace(newer, ready_at=bind_clock(newer.ready_at, newer.subject))
    with pytest.raises(PanelError, match='RUN_SELECTION_NOT_LATEST_ELIGIBLE'):
        tc.validate_example(**refresh_inventory(dict(ex), selected=(selected, newer)))
    short = replace(ex['coverage_policy'], max_run_age_seconds=3600)
    short = replace(short, preregistered_at=bind_clock(short.preregistered_at, short.protocol_subject))
    with pytest.raises(PanelError, match='RUN_CANDIDATE_INVENTORY_INCOMPLETE_OR_DUPLICATE'):
        tc.validate_example(**refresh_inventory({**ex, 'coverage_policy': short}))


def test_r7_run_freshness_equality_and_ready_uncertainty():
    ex = baseline_example()
    age = int(ex['clocks'].decision_at.utc - ex['points'][0].run_initialized_at.utc)
    boundary = replace(ex['coverage_policy'], max_run_age_seconds=age)
    boundary = replace(boundary, preregistered_at=bind_clock(
        boundary.preregistered_at, boundary.protocol_subject))
    assert tc.validate_example(**refresh_inventory({**ex, 'coverage_policy': boundary})).evidence_class == 'SYNTHETIC'
    crossing_ready = replace(ex['run_candidates'][0], ready_at=receipt_ts(
        ex['clocks'].decision_at.utc - 1, uncertainty=120))
    crossing_ready = replace(crossing_ready, ready_at=bind_clock(
        crossing_ready.ready_at, crossing_ready.subject))
    with pytest.raises(PanelError, match='NO_ELIGIBLE_RUN_CANDIDATE'):
        tc.validate_example(**refresh_inventory(dict(ex), selected=(crossing_ready,)))


def test_r7_empty_corpus_still_verifies_frozen_protocol_and_reports_full_cohort():
    policy = coverage_policy()
    report = tc.validate_corpus([], split_cutoffs(), policy, resolve)
    assert report['requested_city_days'] == len(cohort())
    assert len(report['missing_from_requested_cohort']) == len(cohort())
    missing = lambda digest: None if digest == policy.preregistered_at.evidence_sha256 else resolve(digest)
    with pytest.raises(PanelError, match='INJECTED_EVIDENCE_VERIFICATION_FAILED'):
        tc.validate_corpus([], split_cutoffs(), policy, missing)


def test_r7_preregistration_boundary_and_uncertainty():
    ex = baseline_example()
    policy = ex['coverage_policy']
    earliest_decision = min(ts.utc for _, ts in policy.decision_schedule)
    at_decision = replace(policy, preregistered_at=protocol_ts(
        earliest_decision, 'decision_at'))
    at_decision = replace(at_decision, preregistered_at=bind_clock(
        at_decision.preregistered_at, at_decision.protocol_subject))
    assert tc.validate_example(**refresh_inventory({**ex, 'coverage_policy': at_decision})).evidence_class == 'SYNTHETIC'
    with pytest.raises(PanelError, match='PROTOCOL_NOT_PREREGISTERED_BY_DECISION'):
        replace(policy, preregistered_at=protocol_ts(
            earliest_decision - 1, 'decision_at', uncertainty=120))


def test_r7_decision_schedule_is_complete_and_enforced_per_city_day():
    ex = baseline_example()
    policy = ex['coverage_policy']
    with pytest.raises(PanelError, match='DECISION_SCHEDULE_COHORT_MISMATCH'):
        replace(policy, decision_schedule=policy.decision_schedule[:-1])
    early_decision = protocol_ts(ex['clocks'].decision_at.utc - 60, 'decision_at')
    with pytest.raises(PanelError, match='DECISION_NOT_ON_FROZEN_SCHEDULE'):
        tc.validate_example(**{**ex, 'clocks': replace(ex['clocks'], decision_at=early_decision)})


def test_r7_provider_completeness_fallback_grouping_and_full_cohort_report():
    expected_ifs = tc.ExpectedCoverage('IFS', tuple(range(PROVIDERS['IFS'])), EXPECTED_HOURS)
    strict = coverage_policy(required_providers=('GEFS', 'IFS'), extra_expected={'IFS': expected_ifs})
    ex = baseline_example(policy=strict)
    with pytest.raises(PanelError, match='REQUIRED_PROVIDER_ABSENT_NO_FALLBACK'):
        tc.validate_example(**ex)
    fallback = coverage_policy(required_providers=('GEFS', 'IFS'),
                               extra_expected={'IFS': expected_ifs}, fallback_mode='FROZEN_OUTAGE')
    ex = baseline_example(policy=fallback)
    outage_sha = put(tc.outage_manifest_bytes('IFS', fallback, ex['clocks'].decision_at,
                                              ex['run_inventory_sha256']))
    observed = bind_clock(receipt_ts(ex['clocks'].decision_at.utc - 1000, uncertainty=0), outage_sha)
    outage = tc.OutageEvidence('IFS', observed, outage_sha)
    ex['outage_evidence'] = (outage,)
    record = tc.validate_example(**ex)
    report = tc.validate_corpus([record], ex['split_cutoffs'], fallback, resolve)
    assert report['city_days'] == 1
    assert report['requested_city_days'] == len(cohort())
    assert len(report['missing_from_requested_cohort']) == len(cohort()) - 1
    assert report['provider_missing_by_target'][record.dedup_key] == ('IFS',)
    assert report['provider_counts'] == {'GEFS': 1, 'IFS': 0}


def test_r7_multi_provider_single_outcome_group():
    expected_ifs = tc.ExpectedCoverage('IFS', tuple(range(PROVIDERS['IFS'])), EXPECTED_HOURS)
    policy = coverage_policy(required_providers=('GEFS', 'IFS'), extra_expected={'IFS': expected_ifs})
    ex = baseline_example(policy=policy)
    ifs = full_points(provider='IFS')
    ex['points'] = ex['points'] + ifs
    refresh_inventory(ex, selected=(candidate_for(ex['points'][:62]), candidate_for(ifs)))
    record = tc.validate_example(**ex)
    report = tc.validate_corpus([record], ex['split_cutoffs'], policy, resolve)
    assert report['city_days'] == 1
    assert report['provider_counts'] == {'GEFS': 1, 'IFS': 1}
    assert not record.coverage.absent_providers


def test_r7_provider_specific_native_cadence_and_window():
    expected_ifs = tc.ExpectedCoverage('IFS', tuple(range(PROVIDERS['IFS'])),
                                       (12, 18, 24), native_cadence_hours=6,
                                       native_window_hours=(12, 24))
    policy = coverage_policy(required_providers=('GEFS', 'IFS'), extra_expected={'IFS': expected_ifs})
    ex = baseline_example(policy=policy)
    ifs = [make_point(member, hour, provider='IFS')
           for member in range(PROVIDERS['IFS']) for hour in expected_ifs.expected_hours]
    ex['points'] = ex['points'] + ifs
    refresh_inventory(ex, selected=(candidate_for(ex['points'][:62]), candidate_for(ifs)))
    record = tc.validate_example(**ex)
    assert len([x for x in record.coverage.required if x[0] == 'IFS']) == 153


def test_r3_r7_high_low_rows_share_one_independent_city_day():
    low_key = ('KATL', 'KATL-event', 'temperature-rule', TARGET_DATE,
               'daily_low_temperature')
    policy = coverage_policy(requested=cohort() + (low_key,))
    high = baseline_example(policy=policy)
    low = baseline_example(policy=policy)
    low['label_history'] = (label(family='daily_low_temperature'),)
    report = tc.validate_corpus([tc.validate_example(**high), tc.validate_example(**low)],
                                high['split_cutoffs'], policy, resolve)
    assert report['city_days'] == 1
    assert report['splits']['TRAIN'] == 1
    assert report['provider_counts']['GEFS'] == 1
    assert report['requested_city_days'] == len(cohort())


# N1-N4: independent 16c0756 acceptance counterexamples. Each bad example is
# replayed at the corpus boundary against the same frozen resolver bytes.

def reject_example_and_corpus(ex, changed, reason, evidence=None):
    record = tc.validate_example(**ex)
    evidence = dict(EVIDENCE) if evidence is None else evidence
    resolver = evidence.get
    bad = {**ex, **changed, 'evidence_resolver': resolver,
           'capture_registry': tc.CaptureRegistry(),
           'label_registry': tc.LabelVersionRegistry()}
    with pytest.raises(PanelError, match=reason):
        tc.validate_example(**bad)
    source = replace(record.source_inputs, **{
        'points': tuple(bad['points']), 'local_day': bad['local_day'],
        'label_history': tuple(bad['label_history']),
        'run_candidates': tuple(bad['run_candidates']),
        'outage_evidence': tuple(bad.get('outage_evidence', ())),
        'run_inventory_sha256': bad['run_inventory_sha256']})
    forged = replace(record, source_inputs=source)
    with pytest.raises(PanelError, match=reason):
        tc.validate_corpus([forged], ex['split_cutoffs'], ex['coverage_policy'], resolver)


def rebind_point(point, **changes):
    extraction = replace(point.extraction, **changes)
    extraction = replace(extraction, extraction_sha256=tc.extraction_digest(extraction))
    put(tc.extraction_manifest_bytes(extraction))
    ready = bind_clock(point.feature_ready_at, extraction.extraction_sha256)
    return replace(point, extraction=extraction, feature_ready_at=ready,
        member=extraction.member, forecast_hour=extraction.forecast_hour,
        valid_at=declared_ts(extraction.valid_utc), grid=extraction.grid)


def test_n1_member_hour_replication_rejected_by_both_admission_boundaries():
    ex = baseline_example()
    evidence = dict(EVIDENCE)
    point = ex['points'][0]
    # The changed manifests are deliberately absent from the frozen evidence.
    replicated = [rebind_point(point, member=m, forecast_hour=h,
                   valid_utc=point.run_initialized_at.utc + h * 3600)
                  for m in range(31) for h in EXPECTED_HOURS]
    reject_example_and_corpus(ex, {'points': replicated},
                              'INJECTED_EVIDENCE_VERIFICATION_FAILED', evidence)


def test_n1_grid_rewrite_rejected_by_both_admission_boundaries():
    ex = baseline_example()
    evidence = dict(EVIDENCE)
    point = ex['points'][0]
    grid = replace(point.grid, nearest_lat=-33.64, nearest_lon=84.43,
                   distance_km=0.0, grid_resolution_deg=2.5)
    changed = rebind_point(point, grid=grid)
    reject_example_and_corpus(ex, {'points': [changed] + ex['points'][1:]},
                              'INJECTED_EVIDENCE_VERIFICATION_FAILED', evidence)


def test_n1_even_new_synthetic_manifests_cannot_relabel_unchanged_raw_bytes():
    ex = baseline_example()
    point = ex['points'][0]
    relabeled = [rebind_point(point, member=m, forecast_hour=h,
                            valid_utc=point.run_initialized_at.utc + h * 3600)
                 for m in range(31) for h in EXPECTED_HOURS]
    reject_example_and_corpus(ex, {'points': relabeled},
                              'SYNTHETIC_MESSAGE_SEMANTICS_MISMATCH')
    grid = replace(point.grid, nearest_lat=-33.64)
    moved = rebind_point(point, grid=grid)
    reject_example_and_corpus(ex, {'points': [moved] + ex['points'][1:]},
                              'SYNTHETIC_STATION_EXTRACTION_MISMATCH')


@pytest.mark.parametrize('field,value', [
    ('winner_bucket', 7), ('status', 'PENDING'),
    ('revision_of', sha('changed-revision')),
])
def test_n2_label_payload_rewrite_rejected_with_unchanged_evidence(field, value):
    ex = baseline_example()
    changed = replace(ex['label_history'][0], **{field: value})
    reject_example_and_corpus(ex, {'label_history': (changed,)},
                              'INJECTED_EVIDENCE_VERIFICATION_FAILED')


def test_n2_target_rewrite_and_missing_label_bytes_rejected():
    ex = baseline_example()
    fact = ex['label_history'][0]
    changed = replace(fact, target=replace(fact.target, event_id='other-event'))
    reject_example_and_corpus(ex, {'label_history': (changed,)},
                              'INJECTED_EVIDENCE_VERIFICATION_FAILED')
    record = tc.validate_example(**ex)
    evidence = dict(EVIDENCE)
    evidence.pop(fact.label_version)
    resolver = evidence.get
    with pytest.raises(PanelError, match='INJECTED_EVIDENCE_VERIFICATION_FAILED'):
        tc.validate_example(**{**ex, 'evidence_resolver': resolver})
    with pytest.raises(PanelError, match='INJECTED_EVIDENCE_VERIFICATION_FAILED'):
        tc.validate_corpus([record], ex['split_cutoffs'], ex['coverage_policy'], resolver)


def test_n3_wrong_valid_timezone_rejected_at_both_boundaries():
    ex = baseline_example()
    reject_example_and_corpus(ex, {'local_day': local_day(zone='America/Los_Angeles')},
                              'SETTLEMENT_TIMEZONE_MISMATCH')


def test_n3_alternate_timezone_with_its_own_metadata_and_window_passes():
    ex = baseline_example()
    zone = 'America/Los_Angeles'
    day = local_day(zone=zone)
    trial = ('KATL', 'KATL-event', TARGET_DATE)
    schedule = tuple((key, protocol_ts(day.start_utc - 3600, 'decision_at')
                      if key == trial else ts)
                     for key, ts in ex['coverage_policy'].decision_schedule)
    policy = replace(ex['coverage_policy'], decision_schedule=schedule)
    policy = replace(policy, preregistered_at=bind_clock(policy.preregistered_at,
                                                         policy.protocol_subject))
    decision = dict(schedule)[trial]
    ex['coverage_policy'] = policy
    ex['local_day'] = day
    ex['clocks'] = tc.CausalClocks(decision_at=decision,
        prediction_frozen_at=receipt_ts(decision.utc + 60, uncertainty=0))
    fact = label(zone=zone)
    ex['label_history'] = (fact,)
    fit = protocol_ts(fact.label_knowable_at.utc + 60, 'fit_cutoff')
    select = protocol_ts(fit.utc + 200000, 'selection_freeze_at')
    evaluate = protocol_ts(select.utc + 200000, 'evaluation_asof')
    ex['split_cutoffs'] = dict(TRAIN=fit, DEVELOPMENT=select, CONFIRMATION=evaluate)
    record = tc.validate_example(**refresh_inventory(ex))
    assert record.in_day_observations == 31
    tc.validate_corpus([record], ex['split_cutoffs'], policy, resolve)


def test_n4_omitted_or_reclassified_newer_run_rejected_at_both_boundaries():
    ex = baseline_example()
    old = ex['run_candidates'][0]
    newer_slot = next(c for c in ex['run_candidates'] if
                      c.run_initialized_at.utc == old.run_initialized_at.utc + 6 * 3600)
    newer = replace(newer_slot, complete=True, status='READY')
    newer = replace(newer, ready_at=bind_clock(receipt_ts(newer.run_initialized_at.utc + 3600,
                                                           uncertainty=0), newer.subject))
    candidates = tuple(newer if c is newer_slot else c for c in ex['run_candidates'])
    inventory_sha = put(tc.run_inventory_bytes(ex['coverage_policy'], ex['clocks'].decision_at,
                                                candidates, {'GEFS'}))
    # With the complete inventory, the old selection is no longer latest.
    reject_example_and_corpus(ex, {'run_candidates': candidates,
                                   'run_inventory_sha256': inventory_sha},
                              'RUN_SELECTION_NOT_LATEST_ELIGIBLE')
    # Keeping that inventory identity while dropping or reclassifying the new
    # candidate cannot turn the old run back into the latest eligible run.
    altered = (tuple(c for c in candidates if c is not newer),
               tuple(replace(c, complete=False, status='INCOMPLETE') if c is newer else c
                     for c in candidates))
    for subset in altered:
        reason = ('RUN_CANDIDATE_INVENTORY_INCOMPLETE_OR_DUPLICATE' if len(subset) < len(candidates)
                  else 'INJECTED_EVIDENCE_VERIFICATION_FAILED')
        reject_example_and_corpus(ex, {'run_candidates': subset,
                                       'run_inventory_sha256': inventory_sha}, reason)
    reject_example_and_corpus(ex, {'run_inventory_sha256': sha('wrong-inventory-identity')},
                              'INJECTED_EVIDENCE_VERIFICATION_FAILED')


def test_n4_unavailable_status_must_be_observed_by_decision():
    ex = baseline_example()
    unavailable = next(c for c in ex['run_candidates'] if not c.complete)
    late = replace(unavailable, ready_at=receipt_ts(ex['clocks'].decision_at.utc + 1,
                                                     uncertainty=0))
    late = replace(late, ready_at=bind_clock(late.ready_at, late.subject))
    candidates = tuple(late if c is unavailable else c for c in ex['run_candidates'])
    identity = put(tc.run_inventory_bytes(ex['coverage_policy'], ex['clocks'].decision_at,
                                          candidates, {'GEFS'}))
    reject_example_and_corpus(ex, {'run_candidates': candidates,
                                   'run_inventory_sha256': identity},
                              'RUN_CANDIDATE_STATUS_NOT_KNOWN_BY_DECISION')


def fallback_example():
    expected_ifs = tc.ExpectedCoverage('IFS', tuple(range(PROVIDERS['IFS'])), EXPECTED_HOURS)
    policy = coverage_policy(required_providers=('GEFS', 'IFS'),
                             extra_expected={'IFS': expected_ifs}, fallback_mode='FROZEN_OUTAGE')
    ex = baseline_example(policy=policy)
    outage_sha = put(tc.outage_manifest_bytes('IFS', policy, ex['clocks'].decision_at,
                                              ex['run_inventory_sha256']))
    observed = bind_clock(receipt_ts(ex['clocks'].decision_at.utc - 1000, uncertainty=0), outage_sha)
    ex['outage_evidence'] = (tc.OutageEvidence('IFS', observed, outage_sha),)
    return ex


def test_n4_fallback_requires_all_absent_provider_slots_and_bound_outage_bytes():
    ex = fallback_example()
    slots = tc.run_candidate_slots(ex['coverage_policy'],
                                   ex['clocks'].decision_at.conservative_lower_bound)
    assert len([c for c in ex['run_candidates'] if c.provider == 'IFS']) == len(slots)
    record = tc.validate_example(**ex)
    assert record.coverage.absent_providers == ('IFS',)
    unrelated = replace(ex['outage_evidence'][0],
                        evidence_sha256=ex['points'][0].station_version)
    reject_example_and_corpus(ex, {'outage_evidence': (unrelated,)},
                              'INJECTED_EVIDENCE_VERIFICATION_FAILED')
    omitted = tuple(c for c in ex['run_candidates'] if c.provider != 'IFS')
    reject_example_and_corpus(ex, {'run_candidates': omitted},
                              'RUN_CANDIDATE_INVENTORY_INCOMPLETE_OR_DUPLICATE')


def test_n4_fallback_rejects_complete_ready_absent_provider_run():
    ex = fallback_example()
    candidate = next(c for c in ex['run_candidates'] if c.provider == 'IFS')
    ready = replace(candidate, complete=True, status='READY')
    ready = replace(ready, ready_at=bind_clock(ready.ready_at, ready.subject))
    candidates = tuple(ready if c is candidate else c for c in ex['run_candidates'])
    inventory_sha = put(tc.run_inventory_bytes(ex['coverage_policy'], ex['clocks'].decision_at,
                                                candidates, {'GEFS', 'IFS'}))
    outage_sha = put(tc.outage_manifest_bytes('IFS', ex['coverage_policy'],
                                              ex['clocks'].decision_at, inventory_sha))
    old = ex['outage_evidence'][0]
    outage = replace(old, evidence_sha256=outage_sha,
                     observed_at=bind_clock(old.observed_at, outage_sha))
    reject_example_and_corpus(ex, {'run_candidates': candidates,
                                   'run_inventory_sha256': inventory_sha,
                                   'outage_evidence': (outage,)},
                              'OUTAGE_PROVIDER_HAS_ELIGIBLE_RUN')


def test_n1_distinct_messages_in_one_response_remain_admissible():
    ex = baseline_example()
    first, second = ex['points'][0], ex['points'][2]
    one = first.capture.evidence.raw_bytes
    two = second.capture.evidence.raw_bytes
    raw = one + b'\n' + two
    index = tc.canonical([dict(offset=0, length=len(one)),
                          dict(offset=len(one) + 1, length=len(two))])
    raw_sha, index_sha = put(raw), put(index)
    receipt = bind_clock(first.response_completed_at, raw_sha)
    ev = replace(first.capture.evidence, range_end=len(raw)-1, resource_size=len(raw),
        response_completed_at=receipt, content_range=f'bytes 0-{len(raw)-1}/{len(raw)}',
        response_headers=(('content-range', f'bytes 0-{len(raw)-1}/{len(raw)}'),
                          ('content-length', str(len(raw))), ('etag', raw_sha)),
        raw_bytes=raw, index_bytes=index,
        clock_evidence_bytes=resolve(receipt.evidence_sha256))
    put(ev.manifest_bytes)
    capture = replace(first.capture, byte_sha256=raw_sha, index_sha256=index_sha,
                      store=f'private-evidence/r09-trajectory-cas/{raw_sha}', evidence=ev)
    def bind(point, offset, length):
        extraction = replace(point.extraction, raw_sha256=raw_sha,
            index_sha256=index_sha, response_range_end=len(raw)-1,
            message_offset=offset, message_length=length)
        extraction = replace(extraction, extraction_sha256=tc.extraction_digest(extraction))
        put(tc.extraction_manifest_bytes(extraction))
        return replace(point, capture=capture, extraction=extraction,
            response_completed_at=receipt,
            feature_ready_at=bind_clock(point.feature_ready_at, extraction.extraction_sha256))
    points = list(ex['points'])
    points[0] = bind(first, 0, len(one))
    points[2] = bind(second, len(one) + 1, len(two))
    ex['points'] = points
    record = tc.validate_example(**ex)
    assert record.points == 62
    tc.validate_corpus([record], ex['split_cutoffs'], ex['coverage_policy'], resolve)
