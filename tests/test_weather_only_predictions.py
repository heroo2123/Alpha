from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime

import pytest

from polymarket_scanner.weather_only_calibration import (
    SETTLEMENT_LABEL_EVIDENCE_VERSION,
    CalibrationPolicy,
    assess_probability_calibration,
)
from polymarket_scanner.weather_only_contracts import DAILY_HIGH
from polymarket_scanner.weather_only_forecast import (
    FORECAST_ADAPTER_VERSION,
    OPEN_METEO_GEFS_MODEL,
    SUPPORTED_QUANTIZATION,
    BucketEnsembleFrequency,
    EnsembleBucketForecast,
)
from polymarket_scanner.weather_only_predictions import (
    EXACT_SETTLEMENT_SOURCE_ROLE,
    MODEL_FAMILY_VERSION,
    NWS_WRH_EXACT_LABEL_ADAPTER,
    ExactBucketSettlementLabel,
    ProspectiveSelectionPolicy,
    WeatherPredictionError,
    build_exact_bucket_settlement_label,
    calibration_sample_from_exact_label,
    select_prospective_bucket_prediction,
)


SOURCE_SHA = "1" * 64
LABEL_SOURCE_SHA = "2" * 64
TARGET_DATE = date(2026, 9, 11)


def _row(market_id: str, hits: int, *, n: int = 31, policy_id: str = "map-v1") -> BucketEnsembleFrequency:
    probability = hits / n
    return BucketEnsembleFrequency(
        market_id=market_id,
        condition_id=f"condition-{market_id}",
        yes_token=f"yes-{market_id}",
        no_token=f"no-{market_id}",
        lower=None,
        upper=None,
        member_hits=hits,
        member_count=n,
        raw_member_frequency=probability,
        raw_no_frequency=1.0 - probability,
        mapping_policy_id=policy_id,
        calibrated=False,
        financial_authority=False,
    )


def _forecast(
    *,
    event_id: str = "event-1",
    station: str = "KLGA",
    rows: tuple[BucketEnsembleFrequency, ...] | None = None,
    mapping_policy_id: str = "map-v1",
    included_control: bool = True,
) -> EnsembleBucketForecast:
    rows = rows or (
        _row("market-a", 10, policy_id=mapping_policy_id),
        _row("market-b", 15, policy_id=mapping_policy_id),
        _row("market-c", 6, policy_id=mapping_policy_id),
    )
    return EnsembleBucketForecast(
        adapter=FORECAST_ADAPTER_VERSION,
        event_id=event_id,
        station=station,
        target_date=TARGET_DATE,
        family=DAILY_HIGH,
        unit="F",
        provider_model=OPEN_METEO_GEFS_MODEL,
        source_evidence_sha256=SOURCE_SHA,
        mapping_policy_id=mapping_policy_id,
        quantization=SUPPORTED_QUANTIZATION,
        included_control=included_control,
        member_count=31,
        bucket_frequencies=rows,
        probability_sum=1.0,
        calibrated=False,
        settlement_authority=False,
        financial_authority=False,
    )


def _policy(policy_id: str = "top-bucket-prospective-v1") -> ProspectiveSelectionPolicy:
    return ProspectiveSelectionPolicy(policy_id)


def _prediction(*, forecast: EnsembleBucketForecast | None = None, captured_at: float = 100.0):
    return select_prospective_bucket_prediction(
        forecast or _forecast(),
        policy=_policy(),
        captured_at=captured_at,
    )


def _label(prediction=None, **overrides) -> ExactBucketSettlementLabel:
    prediction = prediction or _prediction()
    label = build_exact_bucket_settlement_label(
        event_id=prediction.event_id,
        market_id=prediction.market_id,
        station=prediction.station,
        target_date=prediction.target_date,
        final_payout=1.0,
        finalized_at=prediction.captured_at + 100.0,
        source_evidence_sha256=LABEL_SOURCE_SHA,
    )
    return replace(label, **overrides) if overrides else label


def test_selects_exactly_one_top_bucket_before_resolution_and_keeps_zero_authority():
    prediction = _prediction()
    assert prediction.market_id == "market-b"
    assert prediction.raw_predicted_probability == pytest.approx(15 / 31)
    assert prediction.model_version.startswith(MODEL_FAMILY_VERSION + ":")
    assert len(prediction.forecast_snapshot_sha256) == 64
    assert len(prediction.prediction_evidence_sha256) == 64
    assert prediction.calibrated_probability is False
    assert prediction.financial_authority is False


def test_exact_tie_break_is_market_id_order_and_snapshot_is_independent_of_input_row_order():
    rows_forward = (
        _row("market-z", 14),
        _row("market-a", 14),
        _row("market-m", 3),
    )
    rows_reverse = tuple(reversed(rows_forward))
    first = _prediction(forecast=_forecast(rows=rows_forward))
    second = _prediction(forecast=_forecast(rows=rows_reverse))
    assert first.market_id == second.market_id == "market-a"
    assert first.model_version == second.model_version
    assert first.forecast_snapshot_sha256 == second.forecast_snapshot_sha256


def test_full_forecast_snapshot_changes_when_nonselected_distribution_changes():
    first = _prediction()
    changed = _prediction(
        forecast=_forecast(rows=(
            _row("market-a", 9),
            _row("market-b", 15),
            _row("market-c", 7),
        ))
    )
    assert first.market_id == changed.market_id == "market-b"
    assert first.raw_predicted_probability == changed.raw_predicted_probability
    assert first.model_version == changed.model_version
    assert first.source_evidence_sha256 == changed.source_evidence_sha256
    assert first.forecast_snapshot_sha256 != changed.forecast_snapshot_sha256
    assert first.prediction_evidence_sha256 != changed.prediction_evidence_sha256


def test_model_version_is_configuration_identity_not_event_identity():
    first = _prediction(forecast=_forecast(event_id="event-a"))
    second = _prediction(forecast=_forecast(event_id="event-b"))
    assert first.model_version == second.model_version

    changed_rows = (
        _row("market-a", 10, policy_id="map-v2"),
        _row("market-b", 15, policy_id="map-v2"),
        _row("market-c", 6, policy_id="map-v2"),
    )
    changed_map = _prediction(
        forecast=_forecast(event_id="event-c", rows=changed_rows, mapping_policy_id="map-v2")
    )
    changed_selector = select_prospective_bucket_prediction(
        _forecast(event_id="event-d"),
        policy=_policy("top-bucket-prospective-v2"),
        captured_at=100.0,
    )
    assert changed_map.model_version != first.model_version
    assert changed_selector.model_version != first.model_version


def test_selection_policy_rejects_hidden_research_freedom():
    with pytest.raises(ValueError):
        ProspectiveSelectionPolicy(" x ")
    with pytest.raises(ValueError):
        ProspectiveSelectionPolicy("x", strategy="CHOOSE_AFTER_SETTLEMENT")
    with pytest.raises(ValueError):
        ProspectiveSelectionPolicy("x", tie_break="ROW_ORDER")


@pytest.mark.parametrize(
    ("forecast", "code"),
    [
        (replace(_forecast(), probability_sum=float("nan")), "PREDICTION_FORECAST_PROBABILITY_SUM_INVALID"),
        (replace(_forecast(), probability_sum=True), "PREDICTION_FORECAST_PROBABILITY_SUM_INVALID"),
        (
            replace(
                _forecast(),
                bucket_frequencies=(
                    _row("market-a", 10),
                    _row("market-a", 15),
                    _row("market-c", 6),
                ),
            ),
            "PREDICTION_BUCKET_MARKET_DUPLICATE",
        ),
        (
            replace(
                _forecast(),
                bucket_frequencies=(
                    _row("market-a", 10),
                    replace(
                        _row("market-b", 15),
                        raw_member_frequency=14 / 31,
                        raw_no_frequency=17 / 31,
                    ),
                    _row("market-c", 6),
                ),
            ),
            "PREDICTION_BUCKET_FREQUENCY_HIT_MISMATCH",
        ),
    ],
)
def test_forecast_distribution_corruption_fails_closed_before_selection(forecast, code):
    with pytest.raises(WeatherPredictionError) as raised:
        _prediction(forecast=forecast)
    assert raised.value.code == code


def test_exact_rule_state_label_builds_calibration_sample_without_upgrading_authority():
    prediction = _prediction()
    label = _label(prediction)
    sample = calibration_sample_from_exact_label(prediction, label)
    assert sample.event_id == prediction.event_id
    assert sample.station == prediction.station
    assert sample.model_version == prediction.model_version
    assert sample.predicted_probability == pytest.approx(prediction.raw_predicted_probability)
    assert sample.final_payout == 1.0
    assert sample.label_adapter == NWS_WRH_EXACT_LABEL_ADAPTER
    assert sample.source_role == EXACT_SETTLEMENT_SOURCE_ROLE
    assert sample.evidence_version == SETTLEMENT_LABEL_EVIDENCE_VERSION
    assert sample.label_authority is True
    assert sample.settlement_state_reconstructable is True
    assert len(label.label_evidence_sha256) == 64
    assert prediction.financial_authority is False
    assert label.financial_authority is False


def test_prediction_digest_detects_post_capture_probability_tampering():
    prediction = _prediction()
    tampered = replace(
        prediction,
        raw_predicted_probability=prediction.raw_predicted_probability - 0.01,
    )
    with pytest.raises(WeatherPredictionError) as raised:
        calibration_sample_from_exact_label(tampered, _label(tampered))
    assert raised.value.code == "CALIBRATION_PREDICTION_DIGEST_MISMATCH"


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"event_id": "other-event"}, "CALIBRATION_EVENT_ID_MISMATCH"),
        ({"market_id": "other-market"}, "CALIBRATION_MARKET_ID_MISMATCH"),
        ({"station": "KJFK"}, "CALIBRATION_STATION_MISMATCH"),
        ({"target_date": date(2026, 9, 12)}, "CALIBRATION_TARGET_DATE_MISMATCH"),
        ({"finalized_at": 99.0}, "CALIBRATION_LABEL_PREDATES_PREDICTION"),
        ({"evidence_version": "LEGACY"}, "CALIBRATION_LABEL_EVIDENCE_VERSION_MISMATCH"),
        ({"label_authority": False}, "CALIBRATION_LABEL_AUTHORITY_FALSE"),
        ({"settlement_state_reconstructable": False}, "CALIBRATION_SETTLEMENT_STATE_NOT_RECONSTRUCTABLE"),
        ({"label_adapter": "NWS_API_OFFICIAL_PROXY"}, "CALIBRATION_PROXY_LABEL_FORBIDDEN"),
        ({"source_role": "OFFICIAL_NWS_OBSERVATION_PROXY_ONLY"}, "CALIBRATION_PROXY_LABEL_FORBIDDEN"),
        ({"label_adapter": "FAKE_EXACT_RULE_STATE"}, "CALIBRATION_LABEL_ADAPTER_UNSUPPORTED"),
        ({"source_role": "OTHER_SETTLEMENT_STATE"}, "CALIBRATION_LABEL_SOURCE_ROLE_UNSUPPORTED"),
    ],
)
def test_exact_label_join_rejects_identity_time_authority_or_source_role_attacks(overrides, code):
    prediction = _prediction()
    label = _label(prediction, **overrides)
    with pytest.raises(WeatherPredictionError) as raised:
        calibration_sample_from_exact_label(prediction, label)
    assert raised.value.code == code


def test_label_digest_detects_tampering_after_exact_label_capture():
    prediction = _prediction()
    label = _label(prediction)
    tampered_source = replace(label, source_evidence_sha256="3" * 64)
    with pytest.raises(WeatherPredictionError) as raised:
        calibration_sample_from_exact_label(prediction, tampered_source)
    assert raised.value.code == "CALIBRATION_LABEL_DIGEST_MISMATCH"

    tampered_digest = replace(label, label_evidence_sha256="4" * 64)
    with pytest.raises(WeatherPredictionError) as raised:
        calibration_sample_from_exact_label(prediction, tampered_digest)
    assert raised.value.code == "CALIBRATION_LABEL_DIGEST_MISMATCH"


def test_label_builder_rejects_nonbinary_payout_datetime_and_bad_source_digest():
    prediction = _prediction()
    with pytest.raises(WeatherPredictionError) as raised:
        build_exact_bucket_settlement_label(
            event_id=prediction.event_id,
            market_id=prediction.market_id,
            station=prediction.station,
            target_date=prediction.target_date,
            final_payout=0.5,
            finalized_at=200.0,
            source_evidence_sha256=LABEL_SOURCE_SHA,
        )
    assert raised.value.code == "LABEL_PAYOUT_NOT_BINARY"

    with pytest.raises(WeatherPredictionError) as raised:
        build_exact_bucket_settlement_label(
            event_id=prediction.event_id,
            market_id=prediction.market_id,
            station=prediction.station,
            target_date=datetime(2026, 9, 11, 12, 0),
            final_payout=1.0,
            finalized_at=200.0,
            source_evidence_sha256=LABEL_SOURCE_SHA,
        )
    assert raised.value.code == "LABEL_TARGET_DATE_INVALID"

    with pytest.raises(WeatherPredictionError) as raised:
        build_exact_bucket_settlement_label(
            event_id=prediction.event_id,
            market_id=prediction.market_id,
            station=prediction.station,
            target_date=prediction.target_date,
            final_payout=1.0,
            finalized_at=200.0,
            source_evidence_sha256="not-a-digest",
        )
    assert raised.value.code == "LABEL_SOURCE_EVIDENCE_SHA_INVALID"


def test_direct_label_constructor_rejects_bad_label_digest_shape():
    prediction = _prediction()
    with pytest.raises(WeatherPredictionError) as raised:
        ExactBucketSettlementLabel(
            event_id=prediction.event_id,
            market_id=prediction.market_id,
            station=prediction.station,
            target_date=prediction.target_date,
            final_payout=1.0,
            label_adapter=NWS_WRH_EXACT_LABEL_ADAPTER,
            source_role=EXACT_SETTLEMENT_SOURCE_ROLE,
            evidence_version=SETTLEMENT_LABEL_EVIDENCE_VERSION,
            label_authority=True,
            settlement_state_reconstructable=True,
            finalized_at=200.0,
            source_evidence_sha256=LABEL_SOURCE_SHA,
            label_evidence_sha256="not-a-digest",
        )
    assert raised.value.code == "LABEL_EVIDENCE_SHA_INVALID"


def test_repeated_join_for_same_event_still_counts_once_in_calibration_engine():
    prediction = _prediction()
    sample = calibration_sample_from_exact_label(prediction, _label(prediction))
    assessment = assess_probability_calibration(
        [sample, sample],
        model_version=prediction.model_version,
        target_probability=prediction.raw_predicted_probability,
        policy=CalibrationPolicy(
            policy_id="tiny-integration-policy",
            probability_bins=((0.0, 1.0),),
            min_total_resolved=1,
            min_bin_resolved=1,
            min_distinct_stations=1,
            max_brier_score=1.0,
            wilson_z=1.96,
        ),
    )
    assert assessment.clean_total_resolved == 1
    assert dict(assessment.excluded_counts)["DUPLICATE_EVENT_LABEL"] == 1
    assert assessment.financial_authority is False
