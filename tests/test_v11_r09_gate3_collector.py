"""Offline adversarial tests for the R09 Gate 3 (G3-I) bounded collector.

Every case here is synthetic and offline; no network access is performed and no
real forecast bytes are acquired or referenced. These tests exist to satisfy
protocol Section 7's "Build and test the bounded collector offline" requirement
and to independently reproduce the G3-P review's arithmetic and boundary checks
in executable form, plus regression-prove that the new native IFS/AIFS
three-hour request path never alters the production `ECMWFRequest`.
"""
from datetime import datetime, timezone as dt_timezone
import hashlib
import math
import time

import pytest

from tools.v11_multimodel_panel import PanelError, PROVIDERS
from tools import v11_r09_gate3_collector as g3i
from tools.v11_trajectory_contract import Timestamp


def sha(label):
    return hashlib.sha256(label.encode()).hexdigest()


def decision_timestamp(utc):
    return Timestamp(utc=utc, origin='ALPHA_PREREGISTERED_PROTOCOL', uncertainty_seconds=1.0,
                      clock_health='SYNCED', evidence_sha256=sha('decision'))


def feature_ready_timestamp(utc, *, evidence_label='feature_ready'):
    return Timestamp(utc=utc, origin='LOCAL_RECEIPT_WALL_CLOCK', uncertainty_seconds=1.0,
                      clock_health='SYNCED', evidence_sha256=sha(evidence_label))


def make_dossier(provider, *, verified=True):
    hi = PROVIDERS[provider] - 1
    if provider == 'GEFS':
        native_hours = tuple(range(0, 73, 3))
    elif provider == 'IFS':
        native_hours = tuple(range(0, 73, 3))
    else:
        native_hours = tuple(range(0, 73, 6))
    return g3i.SourceDossier(
        provider=provider, dataset=f'{provider.lower()}-pilot-dataset',
        operational_release_id=f'{provider.lower()}-release-2026-v1',
        source_dossier_sha256=sha(provider + ':dossier'),
        origin_base_url='https://example-pilot-origin.test',
        path_template='/forecasts/{run}/{provider}',
        licence_terms_sha256=sha(provider + ':licence'),
        expected_centre='TEST_CENTRE', expected_subcentre=0, expected_table_version=1,
        expected_generating_process='ENSEMBLE_FORECAST', expected_product_template='PILOT',
        expected_parameter='2t', expected_level='SURFACE', expected_grid_signature=sha(provider + ':grid'),
        member_range=(0, hi), native_hours=native_hours, native_cadence_hours=3 if provider != 'AIFS' else 6,
        original_unit='K', decoder_sha256=sha(provider + ':decoder'), dependency_sha256=sha(provider + ':deps'),
        index_sidecar_available=verified, supports_byte_range_206=verified,
    )


def make_manifest(*, verified=True, requested_keys=None, financial_authority=False):
    decision_at = decision_timestamp(2_000_000_000.0)
    requested_keys = requested_keys or (
        ('TEST_STATION', 'EVENT_HIGH', 'RULE_V1', '2027-03-01', 'daily_high_temperature'),
        ('TEST_STATION', 'EVENT_LOW', 'RULE_V1', '2027-03-01', 'daily_low_temperature'),
    )
    return g3i.CaptureManifest(
        manifest_id='', collector_commit_sha=sha('collector-commit'),
        dossiers=tuple(make_dossier(p, verified=verified) for p in g3i.VALID_PROVIDERS),
        requested_keys=requested_keys,
        decision_at=decision_at,
        acquisition_window_start_utc=decision_at.utc - 4 * 3600,
        acquisition_window_end_utc=decision_at.utc - 3 * 3600,
        feature_sealing_deadline_utc=decision_at.utc - 3600,
        allowed_cycles=(0,), max_run_age_seconds=86400, run_selection='LATEST_COMPLETE_READY',
        fallback_mode='NONE', max_requests=3600, max_total_received_bytes=1024 ** 3,
        min_interval_between_starts_seconds=2.0, max_elapsed_seconds=3 * 3600.,
        single_request_in_flight=True, max_request_deadline_seconds=30.,
        min_free_disk_bytes=2 * 1024 ** 3, min_available_memory_bytes=512 * 1024 ** 2,
        raw_directory_index_verified=verified, ifs_three_hour_path_verified=verified,
        financial_authority=financial_authority,
    )


# --------------------------------------------------------------------------- #
# SourceDossier
# --------------------------------------------------------------------------- #

def test_dossier_rejects_latest_alias():
    with pytest.raises(PanelError, match='DOSSIER_NO_LATEST_ALIAS'):
        g3i.SourceDossier(**{**make_dossier('GEFS').__dict__, 'operational_release_id': 'latest'})


def test_dossier_rejects_non_https_origin():
    fields = make_dossier('GEFS').__dict__.copy()
    fields['origin_base_url'] = 'http://insecure.test'
    with pytest.raises(PanelError, match='DOSSIER_ORIGIN_HTTPS_ONLY_NO_CREDENTIALS'):
        g3i.SourceDossier(**fields)


def test_dossier_rejects_credential_in_origin():
    fields = make_dossier('GEFS').__dict__.copy()
    fields['origin_base_url'] = 'https://user:pass@example.test'
    with pytest.raises(PanelError, match='DOSSIER_ORIGIN_HTTPS_ONLY_NO_CREDENTIALS'):
        g3i.SourceDossier(**fields)


def test_dossier_launch_ready_requires_both_flags_explicit_true():
    verified = make_dossier('GEFS', verified=True)
    unverified = make_dossier('GEFS', verified=False)
    assert verified.launch_ready is True
    assert unverified.launch_ready is False


def test_dossier_native_cadence_must_match_hour_set():
    fields = make_dossier('GEFS').__dict__.copy()
    fields['native_hours'] = (0, 3, 9)  # gap, inconsistent with a fixed 3h cadence
    with pytest.raises(PanelError, match='DOSSIER_NATIVE_CADENCE_CONSISTENCY'):
        g3i.SourceDossier(**fields)


# --------------------------------------------------------------------------- #
# Native IFS/AIFS three-hour request path vs. production ECMWFRequest
# --------------------------------------------------------------------------- #

def test_native_ifs_three_hour_step_accepted():
    req = g3i.NativeECMWFThreeHourRequest(provider='IFS', initialized_at=_aligned_cycle(),
                                           step=3, member=0, grib_signature_sha256=sha('sig'))
    assert req.hour_set == tuple(range(0, 73, 3))


def test_native_ifs_rejects_non_three_hour_step():
    run = _aligned_cycle()
    with pytest.raises(PanelError, match='NATIVE_ECMWF_STEP_BOUND'):
        g3i.NativeECMWFThreeHourRequest(provider='IFS', initialized_at=run, step=4, member=0,
                                         grib_signature_sha256=sha('sig'))


def test_native_aifs_requires_six_hour_step():
    run = _aligned_cycle()
    g3i.NativeECMWFThreeHourRequest(provider='AIFS', initialized_at=run, step=6, member=0,
                                     grib_signature_sha256=sha('sig'))
    with pytest.raises(PanelError, match='NATIVE_ECMWF_STEP_BOUND'):
        g3i.NativeECMWFThreeHourRequest(provider='AIFS', initialized_at=run, step=3, member=0,
                                         grib_signature_sha256=sha('sig'))


def _aligned_cycle():
    now = datetime.now(dt_timezone.utc).replace(minute=0, second=0, microsecond=0, hour=0)
    return now.timestamp()


def test_production_ecmwf_request_still_rejects_three_hour_step_unmodified():
    """Regression: the new G3-I-only native path must not have altered the
    production ECMWFRequest's own six-hour-only enforcement (protocol Section 7:
    "do not downsample IFS or modify a live caller to hide that incompatibility")."""
    from polymarket_scanner.v11.evidence import EvidenceError
    from polymarket_scanner.v11.model_panel import SourceIdentity
    from polymarket_scanner.v11.ecmwf_sources import ECMWFRequest

    run = _aligned_cycle()
    source = SourceIdentity(provider='ECMWF_IFS_ENS', model_version='ifs-cy49r1',
                            dataset='ecmwf-open-data:0p25', release_evidence_sha256=sha('release'))
    with pytest.raises(EvidenceError, match='ECMWF_STEP_BOUND'):
        ECMWFRequest(source=source, initialized_at=run, step=3, member=0,
                     grib_signature_sha256=sha('sig'))


# --------------------------------------------------------------------------- #
# CaptureManifest
# --------------------------------------------------------------------------- #

def test_manifest_nominal_denominator_matches_reviewed_arithmetic():
    manifest = make_manifest()
    assert manifest.nominal_denominator == 2713
    assert manifest.nominal_denominator == 31 * 25 + 51 * 25 + 51 * 13


def test_manifest_feature_eligibility_keys_do_not_multiply_raw_denominator():
    manifest = make_manifest()
    assert len(manifest.feature_eligibility_keys) == len(manifest.requested_keys) * len(g3i.VALID_PROVIDERS)
    assert len(manifest.expected_raw_message_keys) == manifest.nominal_denominator


def test_manifest_rejects_more_than_two_requested_keys():
    keys = (
        ('S', 'E1', 'R', '2027-03-01', 'daily_high_temperature'),
        ('S', 'E2', 'R', '2027-03-01', 'daily_low_temperature'),
        ('S', 'E3', 'R', '2027-03-01', 'daily_high_temperature'),
    )
    with pytest.raises(PanelError, match='MANIFEST_REQUESTED_KEYS_BOUND|MANIFEST_ONE_EVENT_PER_FAMILY'):
        make_manifest(requested_keys=keys)


def test_manifest_rejects_multi_station_pilot():
    keys = (
        ('STATION_A', 'E1', 'R', '2027-03-01', 'daily_high_temperature'),
        ('STATION_B', 'E2', 'R', '2027-03-01', 'daily_low_temperature'),
    )
    with pytest.raises(PanelError, match='MANIFEST_SINGLE_STATION_PILOT'):
        make_manifest(requested_keys=keys)


def test_manifest_rejects_window_after_decision():
    decision_at = decision_timestamp(2_000_000_000.0)
    fields = dict(
        manifest_id='', collector_commit_sha=sha('c'),
        dossiers=tuple(make_dossier(p) for p in g3i.VALID_PROVIDERS),
        requested_keys=(('S', 'E', 'R', '2027-03-01', 'daily_high_temperature'),),
        decision_at=decision_at,
        acquisition_window_start_utc=decision_at.utc + 10,  # after decision: invalid
        acquisition_window_end_utc=decision_at.utc + 20,
        feature_sealing_deadline_utc=decision_at.utc + 30,
        allowed_cycles=(0,), max_run_age_seconds=86400, run_selection='LATEST_COMPLETE_READY',
        fallback_mode='NONE', max_requests=3600, max_total_received_bytes=1024 ** 3,
        min_interval_between_starts_seconds=2.0, max_elapsed_seconds=3 * 3600.,
        single_request_in_flight=True, max_request_deadline_seconds=30.,
        min_free_disk_bytes=2 * 1024 ** 3, min_available_memory_bytes=512 * 1024 ** 2,
        raw_directory_index_verified=True, ifs_three_hour_path_verified=True,
    )
    with pytest.raises(PanelError, match='MANIFEST_WINDOW_MUST_PRECEDE_DECISION'):
        g3i.CaptureManifest(**fields)


def test_manifest_rejects_nonfrozen_policy_fields():
    fields = make_manifest().__dict__.copy()
    fields['fallback_mode'] = 'FROZEN_OUTAGE'
    with pytest.raises(PanelError, match='MANIFEST_FALLBACK_MODE_FROZEN'):
        g3i.CaptureManifest(**fields)


def test_manifest_rejects_financial_authority_true():
    with pytest.raises(PanelError, match='MANIFEST_AUTHORITY_FLAGS_MUST_STAY_FALSE'):
        make_manifest(financial_authority=True)


def test_manifest_not_launchable_until_all_dry_run_flags_true():
    unverified = make_manifest(verified=False)
    verified = make_manifest(verified=True)
    assert unverified.launchable is False
    assert verified.launchable is True
    with pytest.raises(PanelError, match='MANIFEST_NOT_LAUNCHABLE'):
        g3i.require_launch_prerequisites(unverified)
    assert g3i.require_launch_prerequisites(verified) is True


def test_manifest_launchable_false_if_any_single_dossier_unverified():
    dossiers = tuple(make_dossier(p, verified=(p != 'IFS')) for p in g3i.VALID_PROVIDERS)
    manifest = g3i.CaptureManifest(**{**make_manifest().__dict__, 'dossiers': dossiers})
    assert manifest.launchable is False


# --------------------------------------------------------------------------- #
# Manifest sealing
# --------------------------------------------------------------------------- #

def test_seal_manifest_is_deterministic_and_content_bound():
    a = g3i.seal_manifest(make_manifest())
    b = g3i.seal_manifest(make_manifest())
    assert a.manifest_id == b.manifest_id
    changed = g3i.seal_manifest(make_manifest(financial_authority=False, requested_keys=(
        ('TEST_STATION', 'EVENT_HIGH', 'RULE_V1', '2027-03-01', 'daily_high_temperature'),
    )))
    assert changed.manifest_id != a.manifest_id


def test_seal_manifest_rejects_already_sealed():
    sealed = g3i.seal_manifest(make_manifest())
    with pytest.raises(PanelError, match='MANIFEST_ALREADY_SEALED'):
        g3i.seal_manifest(sealed)


# --------------------------------------------------------------------------- #
# AttemptLedger
# --------------------------------------------------------------------------- #

def test_ledger_full_partition_succeeds():
    keys = (('GEFS', 0, 0), ('GEFS', 0, 3))
    ledger = g3i.AttemptLedger(keys)
    ledger.record(keys[0], 'HTTP_404_AT_ATTEMPT')
    ledger.record(keys[1], 'CLOCK_UNTRUSTED', 'HTTP_404_AT_ATTEMPT')
    finalized = ledger.finalize()
    assert set(finalized) == set(keys)
    assert ledger.primary_reason(keys[1]) == 'CLOCK_UNTRUSTED'


def test_ledger_incomplete_partition_refuses_finalize():
    keys = (('GEFS', 0, 0), ('GEFS', 0, 3))
    ledger = g3i.AttemptLedger(keys)
    ledger.record(keys[0], 'HTTP_404_AT_ATTEMPT')
    with pytest.raises(PanelError, match='LEDGER_INCOMPLETE_PARTITION'):
        ledger.finalize()


def test_ledger_refuses_retry_on_already_terminal_key():
    keys = (('GEFS', 0, 0),)
    ledger = g3i.AttemptLedger(keys)
    ledger.record(keys[0], 'HTTP_THROTTLED')
    with pytest.raises(PanelError, match='LEDGER_KEY_ALREADY_TERMINAL_NO_RETRY'):
        ledger.record(keys[0], 'HTTP_404_AT_ATTEMPT')


def test_ledger_refuses_key_outside_manifest():
    ledger = g3i.AttemptLedger((('GEFS', 0, 0),))
    with pytest.raises(PanelError, match='LEDGER_KEY_NOT_IN_MANIFEST'):
        ledger.record(('GEFS', 0, 3), 'HTTP_404_AT_ATTEMPT')


def test_ledger_refuses_unknown_reason():
    ledger = g3i.AttemptLedger((('GEFS', 0, 0),))
    with pytest.raises(PanelError, match='LEDGER_REASON_UNKNOWN'):
        ledger.record(('GEFS', 0, 0), 'MADE_UP_REASON')


# --------------------------------------------------------------------------- #
# RestrictionLedger
# --------------------------------------------------------------------------- #

def test_restriction_ledger_origin_scope_persists_beyond_window():
    ledger = g3i.RestrictionLedger()
    ledger.record(g3i.RestrictionRecord(origin='https://a.test', reason='HTTP_403',
                                        observed_at_utc=100.0, scope='ORIGIN'))
    assert ledger.is_restricted('https://a.test', now_utc=10_000.0, window_end_utc=200.0)


def test_restriction_ledger_window_scope_expires():
    ledger = g3i.RestrictionLedger()
    ledger.record(g3i.RestrictionRecord(origin='https://b.test', reason='HTTP_429',
                                        observed_at_utc=100.0, scope='WINDOW'))
    assert ledger.is_restricted('https://b.test', now_utc=150.0, window_end_utc=200.0)
    assert not ledger.is_restricted('https://b.test', now_utc=250.0, window_end_utc=200.0)


def test_restriction_ledger_resume_requires_matching_hash():
    ledger = g3i.RestrictionLedger()
    ledger.record(g3i.RestrictionRecord(origin='https://c.test', reason='HTTP_503',
                                        observed_at_utc=100.0, scope='WINDOW'))
    good_hash = ledger.content_sha256()
    resumed = g3i.RestrictionLedger.resume(ledger.records, expected_sha256=good_hash)
    assert resumed.records == ledger.records
    with pytest.raises(PanelError, match='RESTRICTION_LEDGER_HASH_MISMATCH_ON_RESUME'):
        g3i.RestrictionLedger.resume(ledger.records, expected_sha256=sha('tampered'))


def test_restriction_record_scope_reason_consistency():
    with pytest.raises(PanelError, match='RESTRICTION_SCOPE_REASON_CONSISTENCY'):
        g3i.RestrictionRecord(origin='https://d.test', reason='HTTP_403',
                              observed_at_utc=1.0, scope='WINDOW')


# --------------------------------------------------------------------------- #
# BudgetTracker
# --------------------------------------------------------------------------- #

def test_budget_cannot_loosen_protocol_index_or_field_ceilings():
    with pytest.raises(PanelError, match='BUDGET_MAX_INDEX_BYTES_CANNOT_LOOSEN_PROTOCOL'):
        g3i.BudgetTracker(max_index_bytes=4 * 1024 * 1024)
    with pytest.raises(PanelError, match='BUDGET_MAX_FIELD_BYTES_CANNOT_LOOSEN_PROTOCOL'):
        g3i.BudgetTracker(max_field_bytes=17 * 1024 * 1024)


@pytest.mark.parametrize('limit_name',
                         ['max_total_received_bytes', 'max_index_bytes', 'max_field_bytes'])
@pytest.mark.parametrize('bad', [True, 0.5, float('nan'), -1])
def test_budget_constructor_requires_positive_exact_integer_byte_limits(limit_name, bad):
    with pytest.raises(PanelError, match='BUDGET_MAX_'):
        g3i.BudgetTracker(**{limit_name: bad})


def test_budget_field_bytes_cannot_loosen_past_the_real_stricter_bound():
    """Regression for the independent G3-I review's P2-1 finding: the protocol's own
    nominal 16 MiB/field ceiling is looser than the already-reviewed real bound
    (`model_panel.MAX_RAW_BYTES` = 4 MiB); this module must enforce the real, tighter
    bound, not just the protocol's own nominal figure (protocol Section 4: "apply a
    stricter existing parser/field bound where present; this document cannot loosen
    it")."""
    g3i.BudgetTracker(max_field_bytes=4 * 1024 * 1024)  # exactly at the real bound: fine
    with pytest.raises(PanelError, match='BUDGET_MAX_FIELD_BYTES_CANNOT_LOOSEN_PROTOCOL'):
        g3i.BudgetTracker(max_field_bytes=4 * 1024 * 1024 + 1)
    with pytest.raises(PanelError, match='BUDGET_MAX_FIELD_BYTES_CANNOT_LOOSEN_PROTOCOL'):
        g3i.BudgetTracker(max_field_bytes=10 * 1024 * 1024)  # strictly between 4 MiB and 16 MiB


def test_budget_enforces_provider_specific_field_ceiling_before_begin():
    tracker = g3i.BudgetTracker()
    tracker.start_window(0.0)
    with pytest.raises(PanelError, match='BUDGET_FIELD_PROVIDER_REQUIRED'):
        tracker.begin_request(0.0, field_bytes=1)
    with pytest.raises(g3i.BudgetCeilingExceeded, match='NOT_ATTEMPTED_BUDGET'):
        tracker.begin_request(0.0, provider='GEFS', field_bytes=g3i._GEFS_S3_FULL_FIELD_MAX_BYTES + 1)
    assert tracker.request_count == 0
    tracker.begin_request(0.0, provider='GEFS', field_bytes=g3i._GEFS_S3_FULL_FIELD_MAX_BYTES)
    tracker.complete_request(g3i._GEFS_S3_FULL_FIELD_MAX_BYTES)
    tracker.begin_request(2.0, provider='IFS', field_bytes=4 * 1024 * 1024)


def test_budget_gefs_full_field_ceiling_is_per_acquisition_path_not_product_family():
    """Regression for the coordinator's 2026-09-30 finding: Gate 3's GEFS ceiling
    must bound the S3 `.idx`-sidecar + `Range` full-field path it actually intends
    to use, not `grib_fields.MAX_BYTES` (64 KiB), which bounds a different consumer
    -- the production NOMADS CGI subregion decoder. Real observed single-field
    byte-range sizes (`config/v11/r09_gate3_observed_message_sizes_20260930.json`,
    33,759 DONE GEFS captures, max 245,209 B; IFS/AIFS maxima from the same
    evidence) must all fit under `begin_request`'s per-provider ceiling, and
    `estimate_feasibility` must find the full manifest launchable at those real
    sizes -- reproducing, with real evidence, the exact G3-L prerequisite this
    module previously refused for every real GEFS capture."""
    observed_max_bytes = {'GEFS': 245_209, 'IFS': 672_912, 'AIFS': 635_346}
    assert g3i._GEFS_S3_FULL_FIELD_MAX_BYTES >= observed_max_bytes['GEFS']
    assert g3i._GEFS_S3_FULL_FIELD_MAX_BYTES <= g3i._EXISTING_MAX_FIELD_BYTES
    tracker = g3i.BudgetTracker()
    tracker.start_window(0.0)
    now = 0.0
    for provider, size in observed_max_bytes.items():
        tracker.begin_request(now, provider=provider, field_bytes=size)
        tracker.complete_request(size)
        now += tracker.min_interval_seconds
    manifest = make_manifest()
    # At the real observed sizes the full pilot denominator exceeds the protocol's
    # 1 GiB total-bytes ceiling (a manifest-scale fact, not a per-message ceiling
    # bug), so P3-3's prespecified bounded fallback applies; a per-provider ceiling
    # wide enough for every real message (not the old 64 KiB one, which rejected
    # all of them) still yields a positive, deterministic fallback.
    plan = g3i.estimate_feasibility(manifest, provider_message_size_estimate_bytes=observed_max_bytes)
    assert plan.launchable_at_full_denominator is False
    assert 0 < plan.fallback_denominator < plan.nominal_denominator
    for provider in g3i.VALID_PROVIDERS:
        assert (provider, 0, 0) in plan.fallback_keys
    # A hypothetical larger ceiling cannot loosen the frozen manifest or protocol.
    plan_capped = g3i.estimate_feasibility(
        manifest, provider_message_size_estimate_bytes=observed_max_bytes,
        ceiling_bytes=plan.estimated_total_bytes)
    assert plan_capped.launchable_at_full_denominator is False
    assert plan_capped.ceiling_bytes == manifest.max_total_received_bytes
    assert plan_capped.fallback_keys == plan.fallback_keys


def test_budget_caller_tightening_applies_to_every_provider():
    tracker = g3i.BudgetTracker(max_field_bytes=32 * 1024)
    tracker.start_window(0.0)
    with pytest.raises(g3i.BudgetCeilingExceeded, match='NOT_ATTEMPTED_BUDGET'):
        tracker.begin_request(0.0, provider='IFS', field_bytes=32 * 1024 + 1)
    with pytest.raises(g3i.BudgetCeilingExceeded, match='NOT_ATTEMPTED_BUDGET'):
        tracker.begin_request(0.0, provider='GEFS', field_bytes=32 * 1024 + 1)


def test_budget_enforces_request_count_ceiling():
    tracker = g3i.BudgetTracker(max_requests=1)
    tracker.start_window(0.0)
    tracker.begin_request(0.0)
    tracker.complete_request(10)
    with pytest.raises(g3i.BudgetCeilingExceeded, match='NOT_ATTEMPTED_BUDGET'):
        tracker.begin_request(3.0)


def test_budget_enforces_minimum_interval():
    tracker = g3i.BudgetTracker()
    tracker.start_window(0.0)
    tracker.begin_request(0.0)
    tracker.complete_request(10)
    with pytest.raises(g3i.BudgetCeilingExceeded, match='NOT_ATTEMPTED_BUDGET'):
        tracker.begin_request(1.0)  # under the 2-second minimum
    tracker.begin_request(2.0)  # exactly at the minimum is fine


def test_budget_enforces_total_received_bytes_ceiling():
    tracker = g3i.BudgetTracker(max_total_received_bytes=100)
    tracker.start_window(0.0)
    tracker.begin_request(0.0)
    with pytest.raises(g3i.BudgetCeilingExceeded, match='NOT_ATTEMPTED_BUDGET'):
        tracker.complete_request(101)
    assert tracker.total_received_bytes == 101
    assert tracker.in_flight is False and tracker.budget_exhausted is True
    with pytest.raises(g3i.BudgetCeilingExceeded, match='NOT_ATTEMPTED_BUDGET'):
        tracker.begin_request(2.0, provider='GEFS', field_bytes=1)
    assert tracker.request_count == 1


def test_budget_exact_exhaustion_stops_next_request():
    tracker = g3i.BudgetTracker(max_total_received_bytes=100)
    tracker.start_window(0.0)
    tracker.begin_request(0.0, provider='GEFS', field_bytes=100)
    tracker.complete_request(100)
    assert tracker.total_received_bytes == 100 and tracker.budget_exhausted is True
    before = vars(tracker).copy()
    with pytest.raises(g3i.BudgetCeilingExceeded, match='NOT_ATTEMPTED_BUDGET'):
        tracker.begin_request(2.0, provider='GEFS', field_bytes=1)
    assert vars(tracker) == before


def test_budget_known_size_must_fit_remaining_capacity_without_mutation():
    tracker = g3i.BudgetTracker(max_total_received_bytes=100)
    tracker.start_window(0.0)
    tracker.begin_request(0.0, provider='GEFS', field_bytes=60)
    tracker.complete_request(60)
    before = vars(tracker).copy()
    for size_arg in ({'field_bytes': 41, 'provider': 'GEFS'}, {'index_bytes': 41}):
        with pytest.raises(g3i.BudgetCeilingExceeded, match='NOT_ATTEMPTED_BUDGET'):
            tracker.begin_request(2.0, **size_arg)
        assert vars(tracker) == before
    tracker.begin_request(2.0, index_bytes=40)
    tracker.complete_request(40)
    assert tracker.total_received_bytes == 100


@pytest.mark.parametrize('bad', [float('nan'), -1, True, 0.5, 0, float('inf')])
@pytest.mark.parametrize('size_name', ['index_bytes', 'field_bytes'])
def test_budget_rejects_malformed_known_sizes_without_mutation(bad, size_name):
    tracker = g3i.BudgetTracker()
    tracker.start_window(0.0)
    before = vars(tracker).copy()
    kwargs = {size_name: bad}
    if size_name == 'field_bytes':
        kwargs['provider'] = 'GEFS'
    with pytest.raises(PanelError, match='BUDGET_(INDEX|FIELD)_BYTES_TYPE'):
        tracker.begin_request(0.0, **kwargs)
    assert vars(tracker) == before


@pytest.mark.parametrize('bad', [float('nan'), float('inf'), True])
def test_budget_rejects_invalid_window_time_without_mutation(bad):
    tracker = g3i.BudgetTracker()
    before = vars(tracker).copy()
    with pytest.raises(PanelError, match='BUDGET_MONOTONIC_TIME_(FINITE|ORDER)'):
        tracker.start_window(bad)
    assert vars(tracker) == before


@pytest.mark.parametrize('bad', [float('nan'), float('inf'), True, -1.0])
def test_budget_rejects_invalid_request_time_without_mutation(bad):
    tracker = g3i.BudgetTracker()
    tracker.start_window(0.0)
    before = vars(tracker).copy()
    with pytest.raises(PanelError, match='BUDGET_MONOTONIC_TIME_(FINITE|ORDER)'):
        tracker.begin_request(bad)
    assert vars(tracker) == before


def test_budget_refuses_clock_reversal_and_invalid_completion_without_mutation():
    tracker = g3i.BudgetTracker()
    tracker.start_window(0.0)
    tracker.begin_request(3.0)
    before = vars(tracker).copy()
    with pytest.raises(PanelError, match='BUDGET_MONOTONIC_TIME_ORDER'):
        tracker.check_before_request(now_monotonic=2.0)
    assert vars(tracker) == before
    for bad in (float('nan'), -1, True, 0.5):
        with pytest.raises(PanelError, match='BUDGET_RECEIVED_BYTES_TYPE'):
            tracker.complete_request(bad)
        assert vars(tracker) == before


def test_budget_refuses_second_request_while_in_flight():
    tracker = g3i.BudgetTracker()
    tracker.start_window(0.0)
    tracker.begin_request(0.0)
    with pytest.raises(g3i.BudgetCeilingExceeded, match='BUDGET_SINGLE_REQUEST_IN_FLIGHT_VIOLATION'):
        tracker.begin_request(5.0)


def test_budget_enforces_elapsed_window_ceiling():
    tracker = g3i.BudgetTracker(max_elapsed_seconds=10.0)
    tracker.start_window(0.0)
    with pytest.raises(g3i.BudgetCeilingExceeded, match='NOT_ATTEMPTED_BUDGET'):
        tracker.begin_request(11.0)


# --------------------------------------------------------------------------- #
# Feasibility / dry-run estimator (P3-3)
# --------------------------------------------------------------------------- #

def test_feasibility_full_denominator_when_small_messages():
    manifest = make_manifest()
    plan = g3i.estimate_feasibility(
        manifest, provider_message_size_estimate_bytes=dict(GEFS=1000, IFS=1000, AIFS=1000))
    assert plan.launchable_at_full_denominator is True
    assert plan.fallback_denominator == plan.nominal_denominator == 2713


def test_feasibility_bounded_fallback_when_messages_exceed_ceiling():
    manifest = make_manifest()
    # Real-world-scale per-message sizes that plausibly exceed the 1 GiB ceiling,
    # per the review's own P3-3 finding.
    sizes = dict(GEFS=600_000, IFS=900_000, AIFS=900_000)
    plan = g3i.estimate_feasibility(manifest, provider_message_size_estimate_bytes=sizes)
    assert plan.launchable_at_full_denominator is False
    assert 0 < plan.fallback_denominator < plan.nominal_denominator
    assert plan.estimated_total_bytes > plan.ceiling_bytes
    # Deterministic: recomputation from the same inputs reproduces the same fallback.
    plan2 = g3i.estimate_feasibility(manifest, provider_message_size_estimate_bytes=sizes)
    assert plan2.fallback_keys == plan.fallback_keys
    # Control member (0) at hour 0 for every provider is the highest-priority key
    # and must survive into the fallback whenever any fallback capacity exists.
    for provider in g3i.VALID_PROVIDERS:
        assert (provider, 0, 0) in plan.fallback_keys


def test_feasibility_requires_all_providers_covered():
    manifest = make_manifest()
    with pytest.raises(PanelError, match='FEASIBILITY_SIZE_ESTIMATE_MUST_COVER_ALL_PROVIDERS'):
        g3i.estimate_feasibility(manifest, provider_message_size_estimate_bytes=dict(GEFS=1000))


def test_feasibility_uses_tighter_frozen_manifest_byte_limit():
    manifest = g3i.CaptureManifest(**{**make_manifest().__dict__, 'max_total_received_bytes': 1000})
    sizes = dict.fromkeys(g3i.VALID_PROVIDERS, 1000)
    plan = g3i.estimate_feasibility(manifest, provider_message_size_estimate_bytes=sizes)
    assert plan.estimated_total_bytes == 2_713_000
    assert plan.ceiling_bytes == 1000
    assert plan.launchable_at_full_denominator is False
    assert plan.fallback_denominator == 1
    larger = g3i.estimate_feasibility(
        manifest, provider_message_size_estimate_bytes=sizes, ceiling_bytes=1024 ** 3)
    assert larger.ceiling_bytes == 1000 and larger.launchable_at_full_denominator is False


# --------------------------------------------------------------------------- #
# Index availability dry-run (P3-2), synthetic transport only
# --------------------------------------------------------------------------- #

class FakeTransport(g3i.Transport):
    def __init__(self, response):
        self._response = response

    def head_or_range_probe(self, url):
        return self._response


def test_check_index_availability_reports_synthetic_probe_result():
    transport = FakeTransport({'index_sidecar_available': True, 'supports_byte_range_206': True})
    result = g3i.check_index_availability(transport, 'https://example-origin.test/dir/', now_utc=123.0)
    assert result.index_sidecar_available is True
    assert result.supports_byte_range_206 is True


def test_check_index_availability_reports_missing_sidecar():
    transport = FakeTransport({'index_sidecar_available': False, 'supports_byte_range_206': True})
    result = g3i.check_index_availability(transport, 'https://example-origin.test/dir/', now_utc=123.0)
    assert result.index_sidecar_available is False


@pytest.mark.parametrize('bad', ['false', 'true', 0, 1, None, [], {}])
def test_check_index_availability_rejects_non_boolean_probe_flags(bad):
    for flag in ('index_sidecar_available', 'supports_byte_range_206'):
        response = {'index_sidecar_available': True, 'supports_byte_range_206': True}
        response[flag] = bad
        with pytest.raises(PanelError, match='INDEX_AVAILABILITY_PROBE_FLAGS_MUST_BE_EXPLICIT_BOOL'):
            g3i.check_index_availability(
                FakeTransport(response), 'https://example-origin.test/dir/', now_utc=123.0)


def test_check_index_availability_requires_transport_instance():
    with pytest.raises(PanelError, match='INDEX_AVAILABILITY_TRANSPORT_REQUIRED'):
        g3i.check_index_availability(object(), 'https://example-origin.test/', now_utc=1.0)


def test_transport_base_class_has_no_default_real_implementation():
    with pytest.raises(NotImplementedError):
        g3i.Transport().head_or_range_probe('https://example-origin.test/')


# --------------------------------------------------------------------------- #
# Causal clock reuse and P3-1 zero-retry documentation
# --------------------------------------------------------------------------- #

def test_causal_feature_eligible_true_when_receipt_precedes_decision():
    decision_at = decision_timestamp(2_000_000_000.0)
    feature_ready_at = feature_ready_timestamp(decision_at.utc - 3600)
    assert g3i.causal_feature_eligible(feature_ready_at, decision_at) is True


def test_causal_feature_eligible_false_when_receipt_after_decision():
    decision_at = decision_timestamp(2_000_000_000.0)
    feature_ready_at = feature_ready_timestamp(decision_at.utc + 3600)
    assert g3i.causal_feature_eligible(feature_ready_at, decision_at) is False


def test_zero_retry_is_documented_as_deliberate_pilot_exception():
    assert g3i.ZERO_RETRY_IS_DELIBERATE_PILOT_EXCEPTION is True
