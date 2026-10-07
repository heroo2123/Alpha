"""Optimization-safe exact refusal-code checks for synthetic Gate 3 tests."""

from contextlib import contextmanager

import pytest

from tools.v11_r09_gate3_launch import LaunchContractError


@contextmanager
def expect_refusal(expected_code):
    """Require a LaunchContractError whose sole argument is an allowed code."""
    expected_codes = (expected_code,) if isinstance(expected_code, str) else tuple(expected_code)
    with pytest.raises(LaunchContractError) as caught:
        yield
    actual = caught.value.args
    if len(actual) != 1 or actual[0] not in expected_codes:
        pytest.fail(f"expected refusal code {expected_codes!r}, got {actual!r}")
