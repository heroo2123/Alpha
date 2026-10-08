"""Pure, fixture-free unit tests for the extracted READY-run selection
primitive. No manifest, repo, or private-root fixture is needed: the real
Gate-3 time arithmetic (``PILOT_WINDOW_FIXED``) always yields exactly one
day-grain candidate per provider, so these synthetic multi-row inventories
exist to pin the general-purpose behavior the two validators rely on, not
to claim any shape the real schema itself ever produces.
"""
import pytest

from tools.v11_r09_gate3_eligibility import latest_ready_run


def test_no_candidates_returns_none():
    assert latest_ready_run([], 1000) is None


def test_no_ready_candidate_returns_none():
    inventory = [
        {'run_utc': 100, 'status': 'INCOMPLETE', 'ready_upper_utc': 200},
        {'run_utc': 200, 'status': 'UNAVAILABLE', 'ready_upper_utc': 300},
    ]
    assert latest_ready_run(inventory, 1000) is None


def test_exactly_one_ready_candidate_selected():
    inventory = [
        {'run_utc': 100, 'status': 'INCOMPLETE', 'ready_upper_utc': 200},
        {'run_utc': 200, 'status': 'READY', 'ready_upper_utc': 300},
    ]
    assert latest_ready_run(inventory, 1000) == 200


def test_multiple_ready_candidates_select_the_maximum():
    inventory = [
        {'run_utc': 100, 'status': 'READY', 'ready_upper_utc': 150},
        {'run_utc': 300, 'status': 'READY', 'ready_upper_utc': 350},
        {'run_utc': 200, 'status': 'READY', 'ready_upper_utc': 250},
    ]
    assert latest_ready_run(inventory, 1000) == 300


def test_result_is_independent_of_input_order():
    rows = [
        {'run_utc': 100, 'status': 'READY', 'ready_upper_utc': 150},
        {'run_utc': 300, 'status': 'READY', 'ready_upper_utc': 350},
        {'run_utc': 200, 'status': 'READY', 'ready_upper_utc': 250},
    ]
    forward = latest_ready_run(rows, 1000)
    backward = latest_ready_run(list(reversed(rows)), 1000)
    assert forward == backward == 300


def test_ready_upper_utc_boundary_is_inclusive():
    inventory = [{'run_utc': 500, 'status': 'READY', 'ready_upper_utc': 1000}]
    assert latest_ready_run(inventory, 1000) == 500


def test_ready_after_lower_is_excluded():
    inventory = [{'run_utc': 500, 'status': 'READY', 'ready_upper_utc': 1001}]
    assert latest_ready_run(inventory, 1000) is None


def test_stale_ready_candidate_loses_to_a_newer_ready_candidate():
    """A READY row still inside the caller's allowed candidate window, but
    older than another qualifying READY row, must never be selected over
    the newer one -- the selection is always the maximum qualifying
    ``run_utc``, never the first or an arbitrary qualifying row."""
    inventory = [
        {'run_utc': 100, 'status': 'READY', 'ready_upper_utc': 150},
        {'run_utc': 200, 'status': 'READY', 'ready_upper_utc': 250},
    ]
    assert latest_ready_run(inventory, 1000) == 200


def test_duplicate_run_utc_rows_do_not_change_the_result():
    inventory = [
        {'run_utc': 200, 'status': 'READY', 'ready_upper_utc': 250},
        {'run_utc': 200, 'status': 'READY', 'ready_upper_utc': 250},
    ]
    assert latest_ready_run(inventory, 1000) == 200


def test_non_ready_status_never_outranks_a_ready_candidate():
    inventory = [
        {'run_utc': 900, 'status': 'UNAVAILABLE', 'ready_upper_utc': 950},
        {'run_utc': 100, 'status': 'READY', 'ready_upper_utc': 150},
    ]
    assert latest_ready_run(inventory, 1000) == 100


def test_bool_ready_upper_utc_is_compared_as_its_int_value():
    """This helper performs no shape/type validation of its own -- that is
    the validators' job (``RUN_CANDIDATE_VALUE`` rejects a bool there
    before this function is ever reached). Documented here only to pin
    that a bool is compared by its numeric value (``True == 1``), not
    specially rejected, since Python's ``bool`` is an ``int`` subtype."""
    inventory = [{'run_utc': 1, 'status': 'READY', 'ready_upper_utc': True}]
    assert latest_ready_run(inventory, 1) == 1
    assert latest_ready_run(inventory, 0) is None


def test_malformed_row_missing_required_key_raises():
    with pytest.raises(KeyError):
        latest_ready_run([{'status': 'READY', 'ready_upper_utc': 1}], 1)
