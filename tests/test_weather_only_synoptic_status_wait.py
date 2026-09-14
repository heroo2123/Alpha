from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from polymarket_scanner.weather_only_live_paper_synoptic import SYNOPTIC_PWS_RUNTIME_VERSION


SCRIPT = Path("deploy/verify-synoptic-pws-status.py")
SHA = "a" * 40


def _module():
    spec = importlib.util.spec_from_file_location("synoptic_status_verifier", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _final_status(**overrides):
    status = {
        "release_sha": SHA,
        "finished_at": 1_010.0,
        "synoptic_pws_runtime_version": SYNOPTIC_PWS_RUNTIME_VERSION,
        "pws_provider": "SYNOPTIC_CWOP",
        "pws_network_id": "65",
        "pws_configured": True,
        "pws_predictive_only": True,
        "pws_may_replace_official_observation": False,
        "pws_may_reweight_probability": False,
        "same_day_delivery_enabled": False,
        "financial_authority": False,
        "automatic_order_placement": False,
    }
    status.update(overrides)
    return status


def test_exact_release_parent_intermediate_is_waited_past():
    module = _module()
    status = _final_status()
    status.pop("synoptic_pws_runtime_version")
    assert module._wait_reason(
        status,
        expected_release_sha=SHA,
        not_before=1_000.0,
        max_age_seconds=600.0,
        now=1_011.0,
    ) == "SYNOPTIC_STATUS_INTERMEDIATE_WRAPPER"


def test_old_prestart_status_is_waited_past_even_before_wrapper_fields_exist():
    module = _module()
    status = {"release_sha": "b" * 40, "finished_at": 999.0}
    assert module._wait_reason(
        status,
        expected_release_sha=SHA,
        not_before=1_000.0,
        max_age_seconds=600.0,
        now=1_011.0,
    ) == "SYNOPTIC_STATUS_PREDATES_START"


def test_wrong_release_is_never_waited_past_or_accepted():
    module = _module()
    status = _final_status(release_sha="b" * 40)
    assert module._wait_reason(
        status,
        expected_release_sha=SHA,
        not_before=1_000.0,
        max_age_seconds=600.0,
        now=1_011.0,
    ) is None
    with pytest.raises(module.SynopticStatusError, match="SYNOPTIC_RELEASE_SHA_MISMATCH"):
        module.verify(status, expected_release_sha=SHA)


def test_stale_exact_release_intermediate_keeps_waiting_only_until_timeout():
    module = _module()
    status = {"release_sha": SHA, "finished_at": 1_001.0}
    assert module._wait_reason(
        status,
        expected_release_sha=SHA,
        not_before=1_000.0,
        max_age_seconds=600.0,
        now=2_000.0,
    ) == "SYNOPTIC_STATUS_STALE"


def test_fresh_augmented_status_passes_and_authority_drift_fails():
    module = _module()
    status = _final_status()
    assert module._wait_reason(
        status,
        expected_release_sha=SHA,
        not_before=1_000.0,
        max_age_seconds=600.0,
        now=1_011.0,
    ) is None
    accepted = module.verify(status, expected_release_sha=SHA)
    assert accepted["acceptance"] == "PASS_SYNOPTIC_PWS_RUNTIME_STATUS"

    bad = _final_status(pws_may_reweight_probability=True)
    with pytest.raises(
        module.SynopticStatusError,
        match="SYNOPTIC_PWS_REWEIGHT_NOT_FALSE",
    ):
        module.verify(bad, expected_release_sha=SHA)
