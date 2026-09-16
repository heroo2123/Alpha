import pytest

from polymarket_scanner.weather_only_calibration import CalibrationPolicy


def _kwargs():
    return {
        "policy_id": "fixture-policy-v1",
        "probability_bins": ((0.0, 0.8), (0.8, 1.0)),
        "min_total_resolved": 100,
        "min_bin_resolved": 30,
        "min_distinct_stations": 8,
        "max_brier_score": 0.08,
        "wilson_z": 1.96,
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("min_total_resolved", 1.5),
        ("min_bin_resolved", True),
        ("min_distinct_stations", 2.0),
        ("max_brier_score", True),
        ("wilson_z", False),
    ],
)
def test_calibration_policy_rejects_bool_fractional_or_float_count_gates(field, value):
    values = _kwargs()
    values[field] = value
    with pytest.raises(ValueError):
        CalibrationPolicy(**values)


def test_calibration_policy_rejects_boolean_bin_bound():
    values = _kwargs()
    values["probability_bins"] = ((False, 0.8), (0.8, 1.0))
    with pytest.raises(ValueError):
        CalibrationPolicy(**values)
