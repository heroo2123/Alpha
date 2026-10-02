"""Bounded, offline public evidence for inventory-transform research.

Nothing here publishes fills, balances, receipts, or account effects. In particular,
an activity row's cash field is a vendor observation, not spendable collateral.
"""
from __future__ import annotations

from dataclasses import dataclass
import decimal
from decimal import Decimal, InvalidOperation
from enum import Enum
import hashlib
import json
import os
from pathlib import Path
import stat
import time
from typing import Any, Mapping


_MAX_DECIMAL_DIGITS = 40
_MAX_DECIMAL_ADJUSTED_EXPONENT = 24
_ARITHMETIC_CONTEXT = decimal.Context(
    prec=160, Emax=999_999, Emin=-999_999,
    traps=[decimal.InvalidOperation, decimal.DivisionByZero, decimal.Overflow],
)


class EvidenceClass(str, Enum):
    API_OBSERVED = "API_OBSERVED"
    CHAIN_RECEIPT = "CHAIN_RECEIPT"
    SYNTHETIC_PROOF = "SYNTHETIC_PROOF"


class Coverage(str, Enum):
    COMPLETE = "COMPLETE"
    INCOMPLETE = "INCOMPLETE"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class Limits:
    max_bytes: int = 2_000_000
    max_records: int = 2_000
    max_seconds: float = 2.0

    def __post_init__(self) -> None:
        if not (type(self.max_bytes) is int and 1 <= self.max_bytes <= 10_000_000
                and type(self.max_records) is int and 1 <= self.max_records <= 10_000
                and type(self.max_seconds) in (int, float) and 0 < self.max_seconds <= 30):
            raise ValueError("INVALID_EVIDENCE_LIMITS")


@dataclass(frozen=True)
class Source:
    name: str
    api_version: str
    route: str
    parameters: tuple[tuple[str, str], ...]
    captured_at_utc: str | None
    capture_time_basis: str
    window_start: int | None
    window_end: int | None
    raw_sha256: str | None
    page_number: int = 0

    def __post_init__(self) -> None:
        if not all(isinstance(x, str) and x for x in (self.name, self.api_version,
                                                     self.route, self.capture_time_basis)):
            raise ValueError("SOURCE_IDENTITY_REQUIRED")
        if type(self.page_number) is not int or self.page_number < 0:
            raise ValueError("INVALID_PAGE_NUMBER")
        if any(type(x) is not int for x in (self.window_start, self.window_end) if x is not None):
            raise ValueError("INVALID_WINDOW")
        if self.window_start is not None and self.window_end is not None and self.window_start > self.window_end:
            raise ValueError("INVALID_WINDOW")
        if self.raw_sha256 is not None and (len(self.raw_sha256) != 64
                                            or any(c not in "0123456789abcdef" for c in self.raw_sha256)):
            raise ValueError("INVALID_RAW_HASH")
        if not isinstance(self.parameters, tuple) or any(
                not isinstance(pair, tuple) or len(pair) != 2
                or not all(isinstance(v, str) for v in pair) for pair in self.parameters):
            raise ValueError("INVALID_PARAMETERS")


@dataclass(frozen=True)
class OfflineDocument:
    payload: Any | None
    raw_sha256: str | None
    coverage: Coverage
    discrepancy: str | None


def _open_regular_nofollow(path: Path) -> int:
    """Open the target by walking each path component with O_NOFOLLOW via
    directory descriptors, anchored from the root.

    This performs no separate stat()-then-open() on any component: every
    component, including the final one, is resolved and rejected-if-a-symlink
    by the same open() call that produces the descriptor which is later read.
    There is therefore no window, between a "checked" lookup and a later
    "opened" lookup of the same name, in which a concurrent symlink swap of
    any ancestor or the final component could substitute a different object
    than the one O_NOFOLLOW just resolved.

    This does not and cannot prove that a *regular* file is not replaced by
    another regular file via an atomic rename at the exact instant of the
    final open() (no path-name-based POSIX API can prove that); the loader's
    contract is limited to: the returned bytes came from a non-symlink,
    regular file reached without following a symlink at any path component.

    The final open() also carries O_NONBLOCK. A named pipe (or other
    non-regular file for which open() blocks waiting for a peer) would
    otherwise stall inside this call, past any caller deadline, before the
    caller's fstat() ever gets a chance to reject it as non-regular. Per
    POSIX, O_NONBLOCK on a regular file has no effect on open() or
    subsequent reads, so this adds no behavior change for the regular-file
    path this loader actually accepts.
    """
    absolute = path if path.is_absolute() else Path.cwd() / path
    parts = absolute.parts
    dir_fd = os.open(parts[0], os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in parts[1:-1]:
            next_fd = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=dir_fd)
            os.close(dir_fd)
            dir_fd = next_fd
        return os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dir_fd)
    finally:
        os.close(dir_fd)


def load_offline_json(path: Path, limits: Limits = Limits()) -> OfflineDocument:
    """Read only an explicit, regular local file; never fetch or follow a symlink.

    Every path component, not only the final one, is opened with O_NOFOLLOW
    via directory descriptors (see `_open_regular_nofollow`): there is no
    separate pre-open check of the path that a later open() could contradict.
    A byte/deadline failure returns no payload and UNKNOWN coverage. The
    caller must not treat any partial bytes as a complete page. A regular
    file replaced by another regular file via rename at the exact open()
    instant is out of this loader's provable contract; see
    `_open_regular_nofollow`.
    """
    path = Path(path)
    start = time.monotonic()
    try:
        fd = _open_regular_nofollow(path)
    except OSError:
        return OfflineDocument(None, None, Coverage.UNKNOWN, "NOT_REGULAR_FILE")
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            return OfflineDocument(None, None, Coverage.UNKNOWN, "NOT_REGULAR_FILE")
        if os.fstat(stream.fileno()).st_size > limits.max_bytes:
            return OfflineDocument(None, None, Coverage.UNKNOWN, "BYTE_LIMIT")
        raw = stream.read(limits.max_bytes + 1)
    if len(raw) > limits.max_bytes:
        return OfflineDocument(None, None, Coverage.UNKNOWN, "BYTE_LIMIT")
    if time.monotonic() - start > limits.max_seconds:
        return OfflineDocument(None, None, Coverage.UNKNOWN, "TIME_LIMIT")
    digest = hashlib.sha256(raw).hexdigest()
    try:
        payload = json.loads(raw, parse_float=Decimal)
    except (UnicodeError, ValueError, RecursionError):
        return OfflineDocument(None, digest, Coverage.UNKNOWN, "INVALID_JSON")
    if time.monotonic() - start > limits.max_seconds:
        return OfflineDocument(None, digest, Coverage.UNKNOWN, "TIME_LIMIT")
    return OfflineDocument(payload, digest, Coverage.UNKNOWN, None)


def _decimal(value: Any) -> Decimal | None:
    """Parse a bounded finite decimal; reject magnitudes arithmetic cannot safely hold."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        return None
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        return None
    if not result.is_finite():
        return None
    if (abs(result.adjusted()) > _MAX_DECIMAL_ADJUSTED_EXPONENT
            or len(result.as_tuple().digits) > _MAX_DECIMAL_DIGITS):
        return None
    return result


@dataclass(frozen=True)
class Activity:
    timestamp: int
    kind: str
    event_slug: str
    condition_id: str
    transaction_hash: str
    side: str | None
    token_id: str | None
    quantity: Decimal
    cash: Decimal
    displayed_price: Decimal | None
    evidence_class: EvidenceClass = EvidenceClass.API_OBSERVED


@dataclass(frozen=True)
class Position:
    event_slug: str
    condition_id: str
    side: str
    quantity: Decimal
    vendor_average_price: Decimal | None
    status: str | None
    evidence_class: EvidenceClass = EvidenceClass.API_OBSERVED


@dataclass(frozen=True)
class Observations:
    source: Source
    rows: tuple[Activity | Position, ...]
    coverage: Coverage
    discrepancies: tuple[str, ...]
    evidence_class: EvidenceClass = EvidenceClass.API_OBSERVED


def _valid_cursor(cursor: Any) -> bool:
    """A next-page cursor must be either absent/null or a non-empty string.

    Values like `0`, `False`, or `""` are ambiguous placeholder identities,
    not a provable absence of further pages, and must never be read as
    equivalent to "no next cursor" via truthiness."""
    return cursor is None or (type(cursor) is str and cursor != "")


def _page_rows(payload: Any, limits: Limits) -> tuple[list[Any], Coverage, list[str], Mapping | None]:
    if not isinstance(payload, Mapping) or "error" in payload or not isinstance(payload.get("data"), list):
        return [], Coverage.UNKNOWN, ["INVALID_OR_ERROR_ENVELOPE"], None
    data = payload["data"]
    pagination = payload.get("pagination")
    offset = pagination.get("offset") if isinstance(pagination, Mapping) else None
    cursor = pagination.get("next_cursor") if isinstance(pagination, Mapping) else None
    valid_pagination = (isinstance(pagination, Mapping) and type(pagination.get("has_more")) is bool
                        and (offset is None or type(offset) is int)
                        and _valid_cursor(cursor))
    if not valid_pagination:
        coverage, findings = Coverage.UNKNOWN, ["PAGINATION_UNKNOWN"]
    elif pagination["has_more"] or cursor:
        coverage, findings = Coverage.INCOMPLETE, ["PAGINATION_CONTINUES"]
    else:
        coverage, findings = Coverage.COMPLETE, []
    if len(data) > limits.max_records:
        coverage = Coverage.INCOMPLETE
        findings.append("RECORD_LIMIT")
    return data[:limits.max_records], coverage, findings, pagination if isinstance(pagination, Mapping) else None


def _request_parameters(source: Source) -> tuple[dict[str, str], bool]:
    """Collapse request parameters into a dict, flagging any repeated key.

    A key sent more than once (even with identical values) encodes no single
    trusted value for that key, since a real HTTP server's handling of a
    duplicate query parameter is implementation-defined; such a request must
    never be read as part of a verified first-page proof."""
    seen: dict[str, str] = {}
    duplicate_keys = False
    for key, value in source.parameters:
        if key in seen:
            duplicate_keys = True
        seen[key] = value
    return seen, duplicate_keys


def _finalize_coverage(coverage: Coverage, findings: list[str], source: Source,
                       start: float, limits: Limits, pagination: Mapping | None = None) -> Coverage:
    """A later page, an unverified first page, or contradictory/ambiguous
    pagination metadata can never establish COMPLETE window coverage alone."""
    parameters, duplicate_keys = _request_parameters(source)
    request_offset = parameters.get("offset")
    response_offset_explicit_null = (isinstance(pagination, Mapping) and "offset" in pagination
                                     and pagination["offset"] is None)
    response_offset = pagination.get("offset") if isinstance(pagination, Mapping) else None
    # A present cursor key, even with an empty-string value, is a request
    # page identity the loader has not independently reviewed as meaning
    # "first page"; only the key's absence verifies a first page. Checking
    # truthiness instead would let an explicit `("cursor", "")` pair fall
    # through as if no cursor had been sent at all.
    is_verified_first_page = (source.page_number == 0
                              and not duplicate_keys
                              and "cursor" not in parameters
                              and request_offset in (None, "0")
                              and response_offset in (None, 0)
                              and not response_offset_explicit_null)
    window_bound = (source.window_start is not None and source.window_end is not None
                    and parameters.get("start") == str(source.window_start)
                    and parameters.get("end") == str(source.window_end))
    if not is_verified_first_page or not window_bound:
        if coverage is Coverage.COMPLETE:
            coverage = Coverage.UNKNOWN
        findings.append("WINDOW_OR_PRIOR_PAGES_UNVERIFIED")
    if duplicate_keys:
        findings.append("AMBIGUOUS_DUPLICATE_REQUEST_PARAMETER")
    if time.monotonic() - start > limits.max_seconds:
        coverage = Coverage.INCOMPLETE
        findings.append("TIME_LIMIT")
    if source.raw_sha256 is None:
        findings.append("RAW_HASH_UNKNOWN")
    return coverage


def normalize_activity(payload: Any, source: Source, limits: Limits = Limits()) -> Observations:
    """Normalize a saved activity page, keeping vendor amounts as observations."""
    start = time.monotonic()
    candidates, coverage, findings, pagination = _page_rows(payload, limits)
    rows: list[Activity] = []
    for raw in candidates:
        if time.monotonic() - start > limits.max_seconds:
            coverage = Coverage.INCOMPLETE
            findings.append("TIME_LIMIT")
            break
        if not isinstance(raw, Mapping):
            findings.append("INVALID_ACTIVITY_ROW")
            coverage = Coverage.INCOMPLETE
            continue
        stamp, quantity, cash = raw.get("timestamp"), _decimal(raw.get("size")), _decimal(raw.get("usdc_size"))
        kind = raw.get("type")
        if (type(stamp) is not int or not isinstance(kind, str) or kind not in {"TRADE", "CONVERSION", "MERGE", "REDEEM"}
                or quantity is None or cash is None or quantity < 0 or cash < 0
                or not all(isinstance(raw.get(key), str) and raw[key] for key in
                           ("event_slug", "condition_id", "transaction_hash"))):
            findings.append("INVALID_ACTIVITY_ROW")
            coverage = Coverage.INCOMPLETE
            continue
        if ((source.window_start is not None and stamp < source.window_start)
                or (source.window_end is not None and stamp > source.window_end)):
            findings.append("ROW_OUTSIDE_REQUESTED_WINDOW")
            coverage = Coverage.INCOMPLETE
            continue
        rows.append(Activity(stamp, kind, raw["event_slug"], raw["condition_id"],
                             raw["transaction_hash"], raw.get("side") or None,
                             raw.get("token_id") or None, quantity, cash,
                             _decimal(raw.get("price"))))
    coverage = _finalize_coverage(coverage, findings, source, start, limits, pagination)
    return Observations(source, tuple(rows), coverage, tuple(dict.fromkeys(findings)))


def normalize_positions(payload: Any, source: Source, limits: Limits = Limits()) -> Observations:
    """Normalize a saved position page without treating displayed basis as fills."""
    start = time.monotonic()
    pagination = None
    if isinstance(payload, list):
        candidates = payload[:limits.max_records]
        coverage = Coverage.INCOMPLETE if len(payload) > limits.max_records else Coverage.UNKNOWN
        findings = ["RECORD_LIMIT"] if len(payload) > limits.max_records else ["PAGINATION_UNKNOWN"]
    else:
        candidates, coverage, findings, pagination = _page_rows(payload, limits)
    rows: list[Position] = []
    for raw in candidates:
        if time.monotonic() - start > limits.max_seconds:
            coverage = Coverage.INCOMPLETE
            findings.append("TIME_LIMIT")
            break
        if not isinstance(raw, Mapping):
            coverage = Coverage.INCOMPLETE
            findings.append("INVALID_POSITION_ROW")
            continue
        quantity = _decimal(raw.get("size"))
        event_slug = raw.get("eventSlug", raw.get("event_slug"))
        condition_id = raw.get("conditionId", raw.get("condition_id"))
        if (quantity is None or quantity < 0 or not isinstance(event_slug, str)
                or not isinstance(condition_id, str) or raw.get("outcome") not in ("Yes", "No")):
            coverage = Coverage.INCOMPLETE
            findings.append("INVALID_POSITION_ROW")
            continue
        status = "REDEEMABLE" if raw.get("redeemable") is True else "OPEN" if raw.get("redeemable") is False else None
        rows.append(Position(event_slug, condition_id, raw["outcome"].upper(),
                             quantity, _decimal(raw.get("avgPrice", raw.get("avg_price"))), status))
    coverage = _finalize_coverage(coverage, findings, source, start, limits, pagination)
    return Observations(source, tuple(rows), coverage, tuple(dict.fromkeys(findings)))


@dataclass(frozen=True)
class EventReconciliation:
    event_slug: str
    purchases: int
    purchase_cash: Decimal
    displayed_quantity_times_price: Decimal
    conversion_cash: Decimal
    merge_cash: Decimal
    apparent_cash_difference: Decimal
    cash_price_discrepancy: Decimal
    purchase_excess_over_observed_transform_quantity: tuple[tuple[str, Decimal], ...]
    coverage: Coverage
    discrepancies: tuple[str, ...]
    unresolved: tuple[str, ...]
    evidence_class: EvidenceClass = EvidenceClass.API_OBSERVED
    chain_status: str = "CHAIN_UNVERIFIED"
    account_effects: tuple[()] = ()


def reconcile_event_window(batch: Observations, event_slug: str) -> EventReconciliation:
    """Report arithmetic and possible residuals; never infer a chain inventory path."""
    if batch.evidence_class is not EvidenceClass.API_OBSERVED:
        raise ValueError("PUBLIC_OBSERVATIONS_REQUIRED")
    rows = [row for row in batch.rows if isinstance(row, Activity) and row.event_slug == event_slug]
    buys = [row for row in rows if row.kind == "TRADE" and row.side == "BUY"]
    conversions = [row for row in rows if row.kind == "CONVERSION"]
    merges = [row for row in rows if row.kind == "MERGE"]
    # A fixed local context keeps this arithmetic deterministic regardless of
    # the caller's ambient decimal context; inputs are already bounded by
    # _decimal, so this precision and exponent range cannot overflow them.
    with decimal.localcontext(_ARITHMETIC_CONTEXT):
        purchase_cash = sum((r.cash for r in buys), Decimal(0))
        displayed = sum((r.quantity * r.displayed_price for r in buys if r.displayed_price is not None), Decimal(0))
        conversion_cash = sum((r.cash for r in conversions), Decimal(0))
        merge_cash = sum((r.cash for r in merges), Decimal(0))
        reference = conversions[0].quantity if len(conversions) == 1 else None
        excess = tuple(sorted((r.condition_id, r.quantity - reference) for r in buys
                              if reference is not None and r.quantity > reference))
        findings = list(batch.discrepancies)
        if any(r.displayed_price is None for r in buys):
            findings.append("DISPLAYED_PRICE_MISSING")
        if purchase_cash != displayed:
            findings.append("PURCHASE_CASH_NE_DISPLAYED_PRICE_PRODUCT")
        if excess:
            findings.append("UNEQUAL_PURCHASE_QUANTITIES_NOT_VERIFIED_HOLDINGS")
        if batch.coverage is not Coverage.COMPLETE:
            findings.append("EVENT_WINDOW_COVERAGE_UNPROVEN")
        # The saved API price 0.978999999 is a float representation. Report this
        # diagnostic at the six-decimal precision of the supplied cash amounts.
        discrepancy = (purchase_cash - displayed).quantize(Decimal("0.000001"))
        conversion_merge_minus_purchase = conversion_cash + merge_cash - purchase_cash
    return EventReconciliation(event_slug, len(buys), purchase_cash, displayed,
                               conversion_cash, merge_cash,
                               conversion_merge_minus_purchase,
                               discrepancy, excess, batch.coverage,
                               tuple(dict.fromkeys(findings)),
                               ("OPENING_INVENTORY_UNKNOWN", "BASIS_UNKNOWN", "FEES_UNKNOWN",
                                "CONVERSION_MASK_UNKNOWN", "TRANSACTION_LINEAGE_UNKNOWN",
                                "DEPLOYED_ROUTE_UNKNOWN"))


@dataclass(frozen=True)
class ReceiptScreen:
    status: str
    accepted_receipts: tuple[()] = ()
    cash_effects: tuple[()] = ()
    inventory_effects: tuple[()] = ()


def screen_rpc_receipt(payload: Any) -> ReceiptScreen:
    """Negative-only screen. Successful chain attestation belongs to a later path."""
    if not isinstance(payload, Mapping):
        return ReceiptScreen("MALFORMED_RPC_RESPONSE")
    if "error" in payload:
        return ReceiptScreen("RPC_ERROR")
    return ReceiptScreen("UNVERIFIED_RPC_RESPONSE")
