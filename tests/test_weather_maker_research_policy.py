from __future__ import annotations

import pytest

from polymarket_scanner.weather_only_maker_research_policy import (
    MAKER_RESEARCH_POLICY_VERSION,
    MakerResearchPolicy,
    MakerResearchPolicyError,
    build_uncalibrated_gefs_fair_band,
    fair_band_research_metadata,
)


SHA = "a" * 64
TOKEN = "123456789"


def _band(*, hits=20, count=31, as_of=100.0):
    return build_uncalibrated_gefs_fair_band(
        token_id=TOKEN,
        member_hits=hits,
        member_count=count,
        as_of=as_of,
        source_evidence_sha256=SHA,
    )


def test_policy_is_frozen_and_hashable():
    policy = MakerResearchPolicy()
    assert policy.version == MAKER_RESEARCH_POLICY_VERSION
    assert policy.expected_members == 31
    assert policy.stress_member_votes == 3
    assert policy.minimum_edge_per_share == pytest.approx(0.05)
    assert len(policy.policy_sha256) == 64
    shadow = policy.shadow_policy()
    assert shadow.policy_id == policy.version
    assert shadow.minimum_edge_per_share == policy.minimum_edge_per_share


def test_raw_frequency_is_stressed_by_exactly_three_member_votes_without_calibration_claim():
    fair = _band(hits=20)
    assert fair.lower == pytest.approx(17 / 31)
    assert fair.point == pytest.approx(20 / 31)
    assert fair.upper == pytest.approx(23 / 31)
    assert fair.calibrated is False
    assert "stress=3_of_31" in fair.model_version
    assert SHA in fair.model_version


def test_stress_clamps_at_zero_and_one():
    low = _band(hits=1)
    high = _band(hits=30)
    assert low.lower == 0.0
    assert low.point == pytest.approx(1 / 31)
    assert high.upper == 1.0
    assert high.point == pytest.approx(30 / 31)


def test_member_count_other_than_exact_31_fails_closed():
    with pytest.raises(MakerResearchPolicyError) as exc:
        _band(hits=20, count=30)
    assert exc.value.code == "MAKER_RESEARCH_MEMBER_COUNT_MISMATCH"


def test_member_hits_out_of_range_fail_closed():
    for hits in (-1, 32):
        with pytest.raises(MakerResearchPolicyError) as exc:
            _band(hits=hits)
        assert exc.value.code == "MAKER_RESEARCH_MEMBER_HITS_INVALID"


def test_invalid_source_digest_fails_closed():
    with pytest.raises(MakerResearchPolicyError) as exc:
        build_uncalibrated_gefs_fair_band(
            token_id=TOKEN,
            member_hits=20,
            member_count=31,
            as_of=100.0,
            source_evidence_sha256="not-a-digest",
        )
    assert exc.value.code == "MAKER_RESEARCH_SOURCE_EVIDENCE_INVALID"


def test_policy_drift_requires_new_version_instead_of_silent_retuning():
    with pytest.raises(MakerResearchPolicyError) as exc:
        MakerResearchPolicy(stress_member_votes=4)
    assert exc.value.code == "MAKER_RESEARCH_POLICY_DRIFT"


def test_metadata_explicitly_denies_probability_and_financial_authority():
    policy = MakerResearchPolicy()
    metadata = fair_band_research_metadata(_band(hits=25), policy)
    assert metadata["calibrated_probability"] is False
    assert metadata["confidence_interval"] is False
    assert metadata["statistical_lower_bound"] is False
    assert metadata["settlement_authority"] is False
    assert metadata["financial_authority"] is False
    assert metadata["raw_member_frequency"] == pytest.approx(25 / 31)
    assert metadata["fair_lower_reference"] == pytest.approx(22 / 31)
