"""V11-IT-001: public data remains bounded observation evidence."""
import ast
from dataclasses import FrozenInstanceError
import decimal
from decimal import Decimal
import inspect
import json
from pathlib import Path

import pytest

from polymarket_scanner.v11 import structural_evidence as evidence


FIXTURES = Path(__file__).parent / "fixtures" / "v11_inventory_transforms"


def singapore():
    fixture = json.loads((FIXTURES / "singapore_20261003_api_observed.json").read_text())
    source = evidence.Source(**{
        **fixture["source"],
        "parameters": tuple(tuple(pair) for pair in fixture["source"]["parameters"]),
    })
    return fixture, evidence.normalize_activity(fixture["payload"], source)


def test_singapore_observed_arithmetic_and_unresolved_lineage():
    fixture, batch = singapore()
    result = evidence.reconcile_event_window(batch, "highest-temperature-in-singapore-on-october-3-2026")
    expected = fixture["expected"]
    assert len(batch.rows) == 13
    assert result.purchases == expected["purchase_count"] == 11
    assert {r.timestamp for r in batch.rows if r.kind == "TRADE"} == {expected["purchase_timestamp"]}
    assert [r.timestamp for r in batch.rows if r.kind == "CONVERSION"] == [expected["conversion_timestamp"]]
    assert [r.timestamp for r in batch.rows if r.kind == "MERGE"] == [expected["merge_timestamp"]]
    assert expected["conversion_timestamp"] - expected["purchase_timestamp"] == 53
    assert expected["merge_timestamp"] - expected["conversion_timestamp"] == 19
    assert result.purchase_cash == Decimal(expected["purchase_cash"])
    assert result.conversion_cash == Decimal(expected["conversion_cash"])
    assert result.merge_cash == Decimal(expected["merge_cash"])
    assert result.apparent_cash_difference == Decimal(expected["apparent_cash_difference"])
    assert result.cash_price_discrepancy == Decimal(expected["cash_price_discrepancy"])
    assert sum((excess for _, excess in result.purchase_excess_over_observed_transform_quantity), Decimal(0)) == Decimal(expected["purchase_excess_over_reference_total"])
    assert len(result.purchase_excess_over_observed_transform_quantity) == 5
    assert "UNEQUAL_PURCHASE_QUANTITIES_NOT_VERIFIED_HOLDINGS" in result.discrepancies
    assert result.coverage is evidence.Coverage.INCOMPLETE
    assert "PAGINATION_CONTINUES" in result.discrepancies
    assert result.evidence_class is evidence.EvidenceClass.API_OBSERVED
    assert result.chain_status == "CHAIN_UNVERIFIED"
    assert set(result.unresolved) == set(fixture["unresolved"])
    assert result.account_effects == ()


def test_no_account_mutation_path_or_fill_promotion():
    _, batch = singapore()
    result = evidence.reconcile_event_window(batch, batch.rows[0].event_slug)
    with pytest.raises(FrozenInstanceError):
        result.purchase_cash = Decimal(0)
    with pytest.raises(FrozenInstanceError):
        batch.rows[0].cash = Decimal(0)
    assert not hasattr(result, "fills") and not hasattr(result, "balances")
    assert all(row.evidence_class is evidence.EvidenceClass.API_OBSERVED for row in batch.rows)
    tree = ast.parse(Path(inspect.getfile(evidence)).read_text())
    imports = [node for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))]
    assert not any("account" in ast.unparse(node) or "execution" in ast.unparse(node)
                   or "production" in ast.unparse(node) for node in imports)
    signatures = [inspect.signature(getattr(evidence, name)) for name in
                  ("normalize_activity", "normalize_positions", "reconcile_event_window", "screen_rpc_receipt")]
    assert all("account" not in signature.parameters for signature in signatures)


def test_record_byte_and_time_exhaustion_never_complete(tmp_path, monkeypatch):
    fixture, batch = singapore()
    limited = evidence.normalize_activity(fixture["payload"], batch.source,
                                          evidence.Limits(max_records=3))
    assert len(limited.rows) == 3
    assert limited.coverage is evidence.Coverage.INCOMPLETE
    assert "RECORD_LIMIT" in limited.discrepancies
    path = tmp_path / "page.json"
    path.write_text(json.dumps(fixture["payload"]))
    oversized = evidence.load_offline_json(path, evidence.Limits(max_bytes=10))
    assert oversized.payload is None and oversized.coverage is evidence.Coverage.UNKNOWN
    assert oversized.discrepancy == "BYTE_LIMIT"
    ticks = iter([0.0, 3.0, 3.0])
    monkeypatch.setattr(evidence.time, "monotonic", lambda: next(ticks))
    exhausted = evidence.normalize_activity(fixture["payload"], batch.source,
                                            evidence.Limits(max_seconds=1.0))
    assert exhausted.coverage is evidence.Coverage.INCOMPLETE
    assert "TIME_LIMIT" in exhausted.discrepancies


def test_offline_loader_hash_and_error_envelope(tmp_path):
    path = tmp_path / "page.json"
    path.write_text('{"error":"upstream overloaded"}')
    loaded = evidence.load_offline_json(path)
    assert loaded.raw_sha256 and loaded.coverage is evidence.Coverage.UNKNOWN
    source = evidence.Source("fixture", "v2", "/v2/activity", (), None, "UNKNOWN", None,
                             None, loaded.raw_sha256)
    batch = evidence.normalize_activity(loaded.payload, source)
    assert not batch.rows and batch.coverage is evidence.Coverage.UNKNOWN
    assert "INVALID_OR_ERROR_ENVELOPE" in batch.discrepancies
    symlink = tmp_path / "link.json"
    symlink.symlink_to(path)
    assert evidence.load_offline_json(symlink).discrepancy == "NOT_REGULAR_FILE"


def test_last_page_requires_explicit_matching_request_window():
    fixture, batch = singapore()
    last_page = {"data": fixture["payload"]["data"],
                 "pagination": {"has_more": False, "next_cursor": None}}
    unknown = evidence.normalize_activity(last_page, batch.source)
    assert unknown.coverage is evidence.Coverage.UNKNOWN
    source = evidence.Source("test", "v2", "/v2/activity",
                             (("start", "1790874111"), ("end", "1790874183")),
                             None, "UNKNOWN", 1790874111, 1790874183,
                             "a" * 64)
    complete = evidence.normalize_activity(last_page, source)
    assert complete.coverage is evidence.Coverage.COMPLETE


def test_contradictory_or_unverified_pagination_cannot_prove_complete():
    """IT-R1: a later page, a lying cursor/offset, or a malformed pagination
    field must never be promoted to COMPLETE, only a verified first page can."""
    window_params = (("start", "0"), ("end", "10"))

    def page(pagination, parameters=window_params, page_number=0):
        source = evidence.Source("test", "v2", "/v2/activity", parameters, None,
                                 "UNKNOWN", 0, 10, "a" * 64, page_number)
        return evidence.normalize_activity({"data": [], "pagination": pagination}, source)

    baseline = page({"has_more": False, "next_cursor": None})
    assert baseline.coverage is evidence.Coverage.COMPLETE

    # Response offset contradicts a request/window that implies the window start.
    assert page({"has_more": False, "next_cursor": None, "offset": 1000}
               ).coverage is not evidence.Coverage.COMPLETE
    # Request declares a nonzero offset even though pagination looks terminal.
    assert page({"has_more": False, "next_cursor": None},
               parameters=window_params + (("offset", "1000"),)
               ).coverage is not evidence.Coverage.COMPLETE
    # A nonempty request cursor means this cannot be an independently provable first page.
    assert page({"has_more": False, "next_cursor": None},
               parameters=window_params + (("cursor", "opaque-cursor"),)
               ).coverage is not evidence.Coverage.COMPLETE
    # Malformed pagination offset type must fail closed, not pass through.
    assert page({"has_more": False, "next_cursor": None, "offset": "0"}
               ).coverage is not evidence.Coverage.COMPLETE
    # A later page can never establish COMPLETE window coverage on its own.
    assert page({"has_more": False, "next_cursor": None}, page_number=1
               ).coverage is not evidence.Coverage.COMPLETE


def test_ambiguous_pagination_cursor_and_duplicate_parameters_cannot_prove_complete():
    """IT-R1: a malformed/ambiguous cursor identity (zero, false, explicit
    null offset) or a duplicated, contradiction-capable request key must
    never be promoted to COMPLETE, only an unambiguous verified first page
    can. Healthy, unambiguous first pages remain a positive control."""
    window_params = (("start", "0"), ("end", "10"))

    def page(pagination, parameters=window_params):
        source = evidence.Source("test", "v2", "/v2/activity", parameters, None,
                                 "UNKNOWN", 0, 10, "a" * 64, 0)
        return evidence.normalize_activity({"data": [], "pagination": pagination}, source)

    # Positive control: an unambiguous terminal first page is still COMPLETE.
    assert page({"has_more": False, "next_cursor": None}).coverage is evidence.Coverage.COMPLETE

    # A zero or False cursor is not a provable "no next page"; it is a
    # malformed cursor identity and must fail closed, not fall through via
    # Python truthiness.
    for next_cursor in (0, False):
        result = page({"has_more": False, "next_cursor": next_cursor})
        assert result.coverage is not evidence.Coverage.COMPLETE
        assert "PAGINATION_UNKNOWN" in result.discrepancies

    # An explicitly present but null response offset is not the same as an
    # absent offset key, and must not be read as a verified first page.
    explicit_null = page({"has_more": False, "offset": None, "next_cursor": None})
    assert explicit_null.coverage is not evidence.Coverage.COMPLETE

    # A duplicated cursor or offset request key encodes no single trusted
    # value and must not be collapsed via dict() into whichever value is last.
    for duplicate_parameters in (
        window_params + (("cursor", "later"), ("cursor", "")),
        window_params + (("offset", "1000"), ("offset", "0")),
    ):
        result = page({"has_more": False, "next_cursor": None}, parameters=duplicate_parameters)
        assert result.coverage is not evidence.Coverage.COMPLETE
        assert "AMBIGUOUS_DUPLICATE_REQUEST_PARAMETER" in result.discrepancies


def test_explicit_empty_request_cursor_cannot_prove_complete():
    """IT-R1 repair: an explicit `("cursor", "")` request pair is a present,
    unreviewed page identity, not an absent cursor, and must not fall through
    via Python truthiness (`not ""` is True) to a verified first page."""
    source = evidence.Source("probe", "v2", "/v2/activity",
                             (("start", "0"), ("end", "10"), ("cursor", "")),
                             None, "UNKNOWN", 0, 10, "a" * 64, 0)
    page = {"data": [], "pagination": {"has_more": False, "next_cursor": None}}
    result = evidence.normalize_activity(page, source)
    assert result.coverage is not evidence.Coverage.COMPLETE
    assert "WINDOW_OR_PRIOR_PAGES_UNVERIFIED" in result.discrepancies

    # Healthy control: the same request with no cursor key at all remains a
    # verified, provable first page.
    absent_cursor_source = evidence.Source("probe", "v2", "/v2/activity",
                                           (("start", "0"), ("end", "10")),
                                           None, "UNKNOWN", 0, 10, "a" * 64, 0)
    healthy = evidence.normalize_activity(page, absent_cursor_source)
    assert healthy.coverage is evidence.Coverage.COMPLETE


def test_loader_final_open_does_not_block_on_a_fifo(tmp_path):
    """IT-R4 repair: the final open() must not block waiting for a FIFO
    writer; a non-regular file must be rejected well inside the caller's
    deadline instead of stalling past it."""
    import os as _os
    import time as _time

    fifo = tmp_path / "input.json"
    _os.mkfifo(fifo)
    start = _time.monotonic()
    result = evidence.load_offline_json(fifo, evidence.Limits(max_seconds=0.1))
    elapsed = _time.monotonic() - start
    assert result.payload is None
    assert result.coverage is evidence.Coverage.UNKNOWN
    assert result.discrepancy == "NOT_REGULAR_FILE"
    assert elapsed < 1.0


def test_loader_final_open_fifo_bounded_in_subprocess(tmp_path):
    """IT-R4 repair, reversed adverse probe: the retained independent review
    probe killed the child after one second because the loader blocked past
    `Limits(max_seconds=0.1)`. Re-run the same shape and require the child to
    exit cleanly well within the timeout instead of being killed."""
    import subprocess
    import sys

    fifo = tmp_path / "input.json"
    os_module = evidence.os
    os_module.mkfifo(fifo)
    child = (
        "import sys; from pathlib import Path; "
        "from polymarket_scanner.v11.structural_evidence import load_offline_json, Limits; "
        "doc = load_offline_json(Path(sys.argv[1]), Limits(max_seconds=0.1)); "
        "assert doc.discrepancy == 'NOT_REGULAR_FILE'; "
        "print('FIFO_OPEN_REJECTED_WITHOUT_BLOCKING')"
    )
    completed = subprocess.run([sys.executable, "-c", child, str(fifo)],
                               timeout=1, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
    assert "FIFO_OPEN_REJECTED_WITHOUT_BLOCKING" in completed.stdout


def test_loader_reads_file_through_normal_nested_directories_after_fifo_fix(tmp_path):
    """Healthy control for the IT-R4 O_NONBLOCK change: an ordinary regular
    file, for which O_NONBLOCK has no POSIX-defined effect, must still be
    read fully and correctly."""
    path = tmp_path / "page.json"
    path.write_text('{"data": [], "ok": true}')
    result = evidence.load_offline_json(path)
    assert result.payload == {"data": [], "ok": True}
    assert result.discrepancy is None
    assert result.raw_sha256 is not None


def test_loader_preserves_full_json_decimal_precision(tmp_path):
    """IT-R3: the JSON boundary must not round a saved numeric lexeme through float."""
    path = tmp_path / "page.json"
    path.write_text('{"data": [], "cash": 0.1234567890123456789}')
    loaded = evidence.load_offline_json(path)
    assert loaded.payload["cash"] == Decimal("0.1234567890123456789")


def test_extreme_finite_decimal_rows_are_rejected_not_crashed():
    """IT-R3: an unbounded finite decimal (huge exponent or digit count) must be
    a rejected row, never a COMPLETE normalization that later crashes arithmetic."""
    _, batch = singapore()
    base_row = {"timestamp": 1790874111, "type": "TRADE", "size": 1, "price": 1,
               "condition_id": "0xabc", "transaction_hash": "0xdef",
               "event_slug": "e", "side": "BUY", "token_id": "t"}
    for extreme_cash in ("1e1000000", "1e100"):
        payload = {"data": [dict(base_row, usdc_size=extreme_cash)],
                  "pagination": {"has_more": False, "next_cursor": None}}
        result = evidence.normalize_activity(payload, batch.source)
        assert result.rows == ()
        assert "INVALID_ACTIVITY_ROW" in result.discrepancies
        assert result.coverage is not evidence.Coverage.COMPLETE
        reconciled = evidence.reconcile_event_window(result, "e")
        assert reconciled.purchase_cash == Decimal(0)


def test_reconciliation_is_independent_of_ambient_decimal_context():
    """IT-R3: reconciliation must use a fixed local context, not the caller's
    ambient decimal context, so the saved result cannot silently change."""
    fixture, batch = singapore()
    event_slug = "highest-temperature-in-singapore-on-october-3-2026"
    with decimal.localcontext() as ctx:
        ctx.prec = 4
        narrow = evidence.reconcile_event_window(batch, event_slug)
    with decimal.localcontext() as ctx:
        ctx.prec = 28
        default = evidence.reconcile_event_window(batch, event_slug)
    assert narrow.purchase_cash == default.purchase_cash == Decimal(fixture["expected"]["purchase_cash"])
    assert narrow.cash_price_discrepancy == default.cash_price_discrepancy == Decimal(fixture["expected"]["cash_price_discrepancy"])


def test_loader_rejects_symlinked_parent_directory(tmp_path):
    """IT-R4: the no-symlink loader must reject a symlinked parent directory,
    not only a symlinked final path component."""
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    (real_dir / "page.json").write_text('{"data": []}')
    linked_dir = tmp_path / "linked"
    linked_dir.symlink_to(real_dir)
    result = evidence.load_offline_json(linked_dir / "page.json")
    assert result.payload is None
    assert result.coverage is evidence.Coverage.UNKNOWN
    assert result.discrepancy == "NOT_REGULAR_FILE"


def test_loader_reads_file_through_normal_nested_directories(tmp_path):
    """Healthy positive control: the O_NOFOLLOW directory-descriptor walk
    introduced for IT-R4 must still successfully read an ordinary file nested
    several directories deep with no symlink anywhere in the path."""
    nested = tmp_path / "a" / "b" / "c"
    nested.mkdir(parents=True)
    target = nested / "page.json"
    target.write_text('{"ok": true}')
    result = evidence.load_offline_json(target)
    assert result.payload == {"ok": True}
    assert result.discrepancy is None
    assert result.raw_sha256 is not None


def test_loader_rejects_symlink_swapped_in_at_the_open_instant(tmp_path, monkeypatch):
    """IT-R4: a symlink substituted for the checked file at the exact open()
    instant (i.e. after any pre-open check could have already passed) must
    still be rejected. The loader opens every path component, including the
    final one, with O_NOFOLLOW instead of checking a path and separately
    opening it, so there is no window for this swap to be followed."""
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    checked = real_dir / "checked.json"
    other = real_dir / "other.json"
    checked.write_text('{"probe": "checked bytes"}')
    other.write_text('{"probe": "swapped bytes"}')

    real_open = evidence.os.open
    swapped = {"done": False}

    def racing_open(path, flags, *args, **kwargs):
        if not swapped["done"] and path == "checked.json" and not (flags & evidence.os.O_DIRECTORY):
            swapped["done"] = True
            checked.unlink()
            checked.symlink_to(other)
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(evidence.os, "open", racing_open)
    result = evidence.load_offline_json(checked)
    assert swapped["done"], "the race was never exercised"
    assert result.payload is None
    assert result.coverage is evidence.Coverage.UNKNOWN
    assert result.discrepancy == "NOT_REGULAR_FILE"


def test_loader_result_is_self_consistent_under_regular_file_rename_race(tmp_path, monkeypatch):
    """IT-R4 documented residual limit: no path-name-based POSIX API can
    distinguish an atomic rename of one regular file over another at the
    exact open() instant, since both are equally valid non-symlink opens.
    After this repair the loader no longer performs a separate pre-open
    stat()/is_file() "proof" that a later open() could contradict, so it
    never returns bytes/hash for a different object than the one actually
    opened: whichever regular file the kernel resolves is read consistently,
    never a corrupted mix of the two."""
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    stable = real_dir / "stable.json"
    replacement = real_dir / "replacement.json"
    stable.write_text('{"probe": "original"}')
    replacement.write_text('{"probe": "replacement"}')

    real_open = evidence.os.open
    swapped = {"done": False}

    def racing_open(path, flags, *args, **kwargs):
        if not swapped["done"] and path == "stable.json" and not (flags & evidence.os.O_DIRECTORY):
            swapped["done"] = True
            replacement.replace(stable)
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(evidence.os, "open", racing_open)
    result = evidence.load_offline_json(stable)
    assert swapped["done"], "the race was never exercised"
    assert result.payload in ({"probe": "original"}, {"probe": "replacement"})
    assert result.raw_sha256 is not None


def test_loader_bounds_deeply_nested_json(tmp_path):
    """IT-R5: JSON recursion exhaustion must be a classified UNKNOWN failure,
    not an uncaught RecursionError escaping the bounded loader interface."""
    path = tmp_path / "nested.json"
    path.write_text("[" * 10000 + "0" + "]" * 10000)
    result = evidence.load_offline_json(path)
    assert result.payload is None
    assert result.coverage is evidence.Coverage.UNKNOWN
    assert result.discrepancy == "INVALID_JSON"
    assert result.raw_sha256 is not None


def test_rpc_errors_have_no_receipt_or_economic_effect():
    fixture = json.loads((FIXTURES / "rpc_error_receipts.json").read_text())
    assert fixture["evidence_class"] == "API_OBSERVED"
    for item in fixture["files"]:
        result = evidence.screen_rpc_receipt(item["response"])
        assert result.status == "RPC_ERROR"
        assert result.accepted_receipts == ()
        assert result.cash_effects == ()
        assert result.inventory_effects == ()
    assert evidence.screen_rpc_receipt({"result": {"status": "0x1"}}).status == "UNVERIFIED_RPC_RESPONSE"


def test_position_vendor_average_is_diagnostic_and_page_coverage_unknown():
    source = evidence.Source("saved positions", "legacy", "/positions", (), None,
                             "UNKNOWN", None, None, None)
    batch = evidence.normalize_positions([{"eventSlug": "e", "conditionId": "c", "outcome": "Yes",
                                           "size": 5, "avgPrice": 0.0909, "redeemable": False}], source)
    assert batch.coverage is evidence.Coverage.UNKNOWN
    assert batch.rows[0].vendor_average_price == Decimal("0.0909")
    assert batch.rows[0].status == "OPEN"
    assert batch.rows[0].evidence_class is evidence.EvidenceClass.API_OBSERVED
    assert not hasattr(batch.rows[0], "fill_price")
