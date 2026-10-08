"""Pure, offline reader from a retained learning-capture v2 record to the
exact packet input shapes defined by `official_label_review_packet.py`.

Given an already-constructed `EvidenceStore` and an explicit `capture_id`,
this module pins a read frontier, verifies the genuine retained lineage of
the learning-capture v2 `MEASUREMENT` record and its child `DECISION` rows
against the independently re-derived original `RULE_STATE` admission, and
maps the result into the packet's plain-dict input shapes. It scans every
retained `RULES`/`LABEL` receipt that could disclose the winner for the
event (bounded, never a truncated tuple).

It never opens a path, never calls any writer method on the store (`audit`,
`capture`, `decision`, `safety_audit`, `funnel`, `source_result`), never
uses a wall clock, and never performs network or provider access of any
kind. It never calls `official_label_review_packet.build_review_packet` or
`official_settlement_source.derive_offline_settlement_source` -- it does not
even import either module.

No V11 writer today retains a WRH/WU settlement-source byte receipt (see
`official_settlement_source.py`), so a `ReviewPacket` can never be built from
genuine retained evidence yet. Rather than build one from a caller-supplied
or fabricated source, or leave `packet` sitting on an ambiguous `None` that
might later be confused with "not yet checked", this module hard-codes
`packet=None` and `gamma_comparator=None` with their own permanent holds, and
`ReviewInputs.__post_init__` refuses to hold any other value for either
field -- the same belt-and-suspenders style `ReviewPacket.__post_init__`
uses for its own six authority fields, which this module's output also
carries, hardcoded to literal `False`.

`settlement_source_record_id` exists on the public signature only for future
source-code compatibility with an eventual additive writer slice. It is
never read, resolved, or otherwise used by this module -- the return value
does not depend on it -- because no schema yet exists against which a
retained record could even be verified without risking fabricated authority.
"""
from __future__ import annotations

from dataclasses import dataclass
from json import JSONDecodeError

from .evidence import EvidenceError, EvidenceStore, digest
from .learning_capture import TARGET as CAPTURE_TARGET, VERSION as CAPTURE_VERSION
from .rules import GUARD_VERSION as RULE_GUARD_VERSION, RuleFingerprint
from ..settlement import exact_token_payout


READER_VERSION = "alpha_v11_official_label_review_reader_v1"
ADMISSIBLE_RULE_STATES = ("SEMANTICS_OBSERVED", "REVIEWED_RECERTIFIED")
PROXY_OBSERVATION_PROVIDER = "NOAA_AWC"

# The closed hold vocabulary this reader can emit. Six are permanent
# declared residuals of this slice (always present, every call, regardless
# of input); the rest are conditional on the mapped evidence.
PERMANENT_HOLDS = (
    "SETTLEMENT_SOURCE_NOT_RETAINED",
    "PUBLISHER_RIGHTS_AND_AUTHENTICITY_UNREVIEWED",
    "ARCHIVE_NOT_INDEPENDENT_TAMPERPROOF",
    "GAMMA_COMPARATOR_NOT_CONSTRUCTED",
    "DISCOVERY_CATALOG_NOT_SCANNED",
    "WU_FALLBACK_OUT_OF_SCOPE",
)
CONDITIONAL_HOLDS = (
    "RULE_STATE_NOT_ADMISSIBLE_AT_DECISION",
    "RULE_RECEIPT_FINGERPRINT_MISMATCH",
    "RULE_DRIFT_IN_DECISION_WINDOW",
    "RULE_DRIFT_AFTER_CAPTURE",
    "CHILD_DECISION_AFTER_CAPTURE",
    "CAPTURE_VERSION_UNSUPPORTED",
    "MULTIPLE_CAPTURES_FOR_EVENT",
    "DISCLOSURE_SCAN_INCOMPLETE",
    "INTRADAY_PROXY_INFORMATION_PRESENT",
)
HOLDS = PERMANENT_HOLDS + CONDITIONAL_HOLDS

_SCAN_PAGE_LIMIT = 200
_RULE_STATE_SCAN_CAP = 2000
_CAPTURE_UNIQUENESS_SCAN_CAP = 2000
_DISCLOSURE_SCAN_CAP = 2000


def _require(ok: bool, reason: str) -> None:
    if not ok:
        raise EvidenceError("LABEL_REVIEW_READER_" + reason)


def _get(store, record_id, *, missing_reason: str) -> dict:
    """`store.get`, re-typed to the reader's namespaced refusal vocabulary.

    `EvidenceStore.get` raises a bare, unprefixed `EvidenceError('EVIDENCE_MISSING')`
    for both a non-str key and an absent id. Contract section 5 requires every
    lineage defect this reader finds to raise `EvidenceError('LABEL_REVIEW_READER_<REASON>')`.
    """
    try:
        return store.get(record_id)
    except EvidenceError as exc:
        if exc.args == ("EVIDENCE_MISSING",):
            raise EvidenceError("LABEL_REVIEW_READER_" + missing_reason) from None
        raise


@dataclass(frozen=True)
class ReviewInputs:
    version: str
    through_seq: int
    tip_sha256: str | None
    capture_id: str
    rule: RuleFingerprint | None
    rule_receipt: dict | None
    decision: dict | None
    capture: dict | None
    disclosures: tuple[dict, ...]
    provenance: dict
    holds: tuple[str, ...]
    packet: object = None
    gamma_comparator: object = None
    independent_label_attestation: bool = False
    settlement_authority: bool = False
    calibration_authority: bool = False
    financial_authority: bool = False
    automatic_promotion: bool = False
    qualified: bool = False

    def __post_init__(self) -> None:
        # Defense in depth, mirroring ReviewPacket.__post_init__: a direct
        # `ReviewInputs(...)` or `dataclasses.replace(...)` call bypasses
        # `read_review_inputs` entirely. The type itself refuses to hold a
        # True authority flag, or a non-None packet/comparator, regardless.
        for name in ("independent_label_attestation", "settlement_authority",
                     "calibration_authority", "financial_authority",
                     "automatic_promotion", "qualified"):
            if getattr(self, name) is not False:
                raise ValueError(f"{name} must be literal False")
        if self.packet is not None:
            raise ValueError("packet must be None in this reader slice")
        if self.gamma_comparator is not None:
            raise ValueError("gamma_comparator must be None in this reader slice")
        if type(self.holds) is not tuple or not all(type(h) is str for h in self.holds):
            raise ValueError("holds must be a tuple of str")
        if not set(PERMANENT_HOLDS) <= set(self.holds):
            raise ValueError("holds must retain every permanent hold")
        if not set(self.holds) <= set(HOLDS):
            raise ValueError("holds must stay within the closed hold vocabulary")


def _scan(store, *, kind: str, event_id: str, through_seq: int, cap: int) -> tuple[list[dict], bool]:
    """Bounded forward page scan of one (kind, event) stream, seq-frontier filtered."""
    rows: list[dict] = []
    cursor = 0
    while True:
        page = store.records(kind=kind, event_id=event_id, after_seq=cursor, limit=_SCAN_PAGE_LIMIT)
        if not page:
            return rows, False
        cursor = page[-1]["seq"]
        rows.extend(row for row in page if row["seq"] <= through_seq)
        if len(rows) > cap:
            return rows, True
        if cursor >= through_seq or len(page) < _SCAN_PAGE_LIMIT:
            return rows, False


_GAMMA_WRAPPERS = ("event", "market", "response")
_GAMMA_MAX_DEPTH = 4


class _GammaTraversalIncomplete(Exception):
    """A receipt has descendants beyond the bounded payout walk."""


def _gamma_market_candidates(value, depth: int = 0):
    """Bounded-depth, provider-agnostic walk of a Gamma RULES payload.

    Contract section 4 (D3) requires scanning payout receipts "from any
    provider", not just the two providers this reader happened to special-
    case. Every retained provider shape nests the market object under some
    combination of ``event``/``market``/``response`` and/or a ``markets``
    list (`tools/v11_brain_label_attestation.py:_gamma_market` is the
    precedent). The depth bound fails closed on any unexpectedly deep or
    malformed nesting rather than scanning without limit.
    """
    if depth > _GAMMA_MAX_DEPTH:
        raise _GammaTraversalIncomplete
    if isinstance(value, list):
        for item in value:
            yield from _gamma_market_candidates(item, depth + 1)
    elif isinstance(value, dict):
        # A market may also carry a ``markets`` array. Its own payout and
        # every child branch must be examined independently.
        if "clobTokenIds" in value or "outcomePrices" in value:
            yield value
        markets = value.get("markets")
        if isinstance(markets, list):
            yield from _gamma_market_candidates(markets, depth + 1)
        for key in _GAMMA_WRAPPERS:
            if key in value:
                yield from _gamma_market_candidates(value[key], depth + 1)


def _d3_discloses(row: dict, partition_by_id: dict) -> bool:
    body = row["body"]
    payload = body.get("payload")
    if not isinstance(payload, dict):
        return False
    disclosed = False
    for market in _gamma_market_candidates(payload):
        if not isinstance(market, dict):
            continue
        bucket = partition_by_id.get(str(market.get("id")))
        if bucket is None:
            continue
        try:
            payout = exact_token_payout(bucket["yes_token"], market)
        except Exception:
            continue
        if payout is not None:
            disclosed = True
    return disclosed


def _disclosure_entry(row: dict, *, recorded_at: float | None = None) -> dict:
    body = row["body"]
    candidates = [body["recorded_at"] if recorded_at is None else recorded_at]
    # Contract section 4, "disclosure time": min(archive recorded_at, any
    # self-declared earlier receive/publish time). An earlier self-declared
    # time can only make lookahead more likely to be flagged -- fail-closed.
    for key in ("observed_at", "issued_at", "published_at"):
        value = body.get(key)
        if isinstance(value, (int, float)):
            candidates.append(value)
    return {"id": row["id"], "seq": row["seq"], "recorded_at": min(candidates)}


def _scan_disclosures(store, *, event_id: str, through_seq: int,
                       partition_by_id: dict) -> tuple[tuple[dict, ...], dict, list[str]]:
    """D3 (RULES payout receipts) + D4 (LABEL) only; D1/D2/D6 are declared residuals."""
    holds: list[str] = []
    provenance = {"disclosure_scan_kinds": ("RULES", "LABEL")}

    rules_rows, rules_truncated = _scan(store, kind="RULES", event_id=event_id,
                                         through_seq=through_seq, cap=_DISCLOSURE_SCAN_CAP)
    label_rows, label_truncated = _scan(store, kind="LABEL", event_id=event_id,
                                         through_seq=through_seq, cap=_DISCLOSURE_SCAN_CAP)
    if rules_truncated or label_truncated:
        # Never return a truncated tuple: an incomplete scan cannot prove
        # completeness, so this fails closed to "no disclosure", not a
        # partial one that could be mistaken for the full candidate set.
        holds.append("DISCLOSURE_SCAN_INCOMPLETE")
        return (), provenance, holds

    disclosures = []
    for row in rules_rows:
        try:
            disclosed = _d3_discloses(row, partition_by_id)
        except _GammaTraversalIncomplete:
            holds.append("DISCLOSURE_SCAN_INCOMPLETE")
            return (), provenance, holds
        if disclosed:
            disclosures.append(_disclosure_entry(row))
    for row in label_rows:
        payload = row["body"].get("payload")
        target = payload.get("target_identity") if isinstance(payload, dict) else None
        market_id = target.get("market_id") if isinstance(target, dict) else None
        if market_id not in partition_by_id:
            continue
        recorded_at = row["body"]["recorded_at"]
        knowable_at = payload.get("knowable_at")
        if isinstance(knowable_at, (int, float)):
            # A self-declared earlier knowable time can only make lookahead
            # more likely to be flagged later, never less -- fail-closed.
            recorded_at = min(recorded_at, knowable_at)
        disclosures.append(_disclosure_entry(row, recorded_at=recorded_at))
    disclosures.sort(key=lambda d: d["seq"])

    proxy_rows, proxy_truncated = _scan(store, kind="OFFICIAL_OBSERVATION", event_id=event_id,
                                         through_seq=through_seq, cap=_DISCLOSURE_SCAN_CAP)
    if proxy_truncated:
        holds.append("DISCLOSURE_SCAN_INCOMPLETE")
    proxy_count = sum(1 for row in proxy_rows if row["body"].get("provider") == PROXY_OBSERVATION_PROVIDER)
    provenance["proxy_receipts_target_day_before_decision"] = proxy_count
    if proxy_count:
        holds.append("INTRADAY_PROXY_INFORMATION_PRESENT")
    return tuple(disclosures), provenance, holds


def read_review_inputs(*, store: EvidenceStore, capture_id: str,
                        settlement_source_record_id: str | None = None) -> ReviewInputs:
    """Map one explicitly supplied retained learning-capture v2 record.

    ``store`` is an already-constructed `EvidenceStore` (or any object with
    its `get`/`records`/`pin_read_view` read methods); this function never
    opens a path. ``capture_id`` is an explicit `MEASUREMENT` record id --
    there is no "latest" fallback. ``settlement_source_record_id`` is never
    read; see the module docstring.
    """
    if settlement_source_record_id is not None and not isinstance(settlement_source_record_id, str):
        raise EvidenceError("LABEL_REVIEW_READER_SETTLEMENT_SOURCE_RECORD_ID_TYPE_INVALID")

    capture_row = _get(store, capture_id, missing_reason="CAPTURE_NOT_FOUND")
    _require(capture_row["kind"] == "MEASUREMENT", "CAPTURE_KIND_INVALID")
    event_id = capture_row["event_id"]

    pin = store.pin_read_view(heads=(("RULE_STATE", event_id), ("MEASUREMENT", event_id),
                                      ("LABEL", event_id), ("RULES", event_id)))
    through_seq, tip_sha256 = pin["through_seq"], pin["tip_sha256"]
    _require(capture_row["seq"] <= through_seq, "CAPTURE_AFTER_FRONTIER")

    holds: list[str] = list(PERMANENT_HOLDS)

    def hold(code: str) -> None:
        if code not in holds:
            holds.append(code)

    details = capture_row["body"].get("details")
    _require(isinstance(details, dict), "CAPTURE_DETAILS_INVALID")
    capture_out = {"event_id": event_id, "seq": capture_row["seq"],
                   "recorded_at": capture_row["body"]["recorded_at"]}

    other_rows, other_truncated = _scan(store, kind="MEASUREMENT", event_id=event_id,
                                         through_seq=through_seq, cap=_CAPTURE_UNIQUENESS_SCAN_CAP)
    _require(not other_truncated, "MEASUREMENT_SCAN_INCOMPLETE")
    other_ids = sorted(row["id"] for row in other_rows
                        if row["id"] != capture_id
                        and isinstance(row["body"].get("details"), dict)
                        and row["body"]["details"].get("version") == CAPTURE_VERSION)

    provenance: dict = {}
    if other_ids:
        hold("MULTIPLE_CAPTURES_FOR_EVENT")
        provenance["other_capture_ids"] = tuple(other_ids)

    if details.get("version") != CAPTURE_VERSION:
        hold("CAPTURE_VERSION_UNSUPPORTED")
        disclosures, disc_prov, disc_holds = _scan_disclosures(
            store, event_id=event_id, through_seq=through_seq, partition_by_id={})
        for code in disc_holds:
            hold(code)
        provenance.update(disc_prov)
        return ReviewInputs(version=READER_VERSION, through_seq=through_seq, tip_sha256=tip_sha256,
                             capture_id=capture_id, rule=None, rule_receipt=None, decision=None,
                             capture=capture_out, disclosures=disclosures, provenance=provenance,
                             holds=tuple(holds))

    # --- Reader-verified lineage of a v2 capture: any failure here is a
    # reader refusal, not a packet input (official_label_review_packet.py
    # never sees malformed data through this path). ---
    _require(details.get("complete_event_vector") is True, "COMPLETE_VECTOR_REQUIRED")
    _require(details.get("financial_authority") is False, "CAPTURE_FINANCIAL_AUTHORITY_VIOLATION")
    _require(details.get("target") == CAPTURE_TARGET, "CAPTURE_TARGET_INVALID")
    _require(details.get("selection_scope") == "ALL_BUCKETS_OF_THIS_EVALUATED_EVENT", "CAPTURE_SELECTION_SCOPE_INVALID")

    rule_dict = details.get("rule")
    _require(isinstance(rule_dict, dict) and set(rule_dict) == {"canonical_json", "sha256", "source_event_sha256"}
              and isinstance(rule_dict.get("canonical_json"), str) and isinstance(rule_dict.get("sha256"), str)
              and isinstance(rule_dict.get("source_event_sha256"), str), "RULE_PREIMAGE_INVALID")
    rule = RuleFingerprint(**rule_dict)
    try:
        rule_payload = rule.payload  # keeps RULE_FINGERPRINT_INTEGRITY on digest mismatch.
    except JSONDecodeError:
        raise EvidenceError("LABEL_REVIEW_READER_RULE_PREIMAGE_INVALID") from None
    _require(isinstance(rule_payload, dict), "RULE_PREIMAGE_INVALID")
    _require(rule_payload.get("event_id") == event_id, "RULE_EVENT_MISMATCH")

    binding = details.get("binding")
    _require(isinstance(binding, dict) and binding.get("rule_fingerprint") == rule.sha256, "RULE_BINDING_MISMATCH")

    partition = rule_payload.get("partition")
    _require(isinstance(partition, list) and len(partition) >= 2, "RULE_PARTITION_INVALID")
    _require(all(isinstance(b, dict) and all(isinstance(b.get(k), str) and b[k]
                 for k in ("market_id", "condition_id", "yes_token", "no_token"))
                 for b in partition), "RULE_PARTITION_INVALID")
    partition_by_id = {b["market_id"]: b for b in partition}
    _require(len(partition_by_id) == len(partition), "RULE_PARTITION_INVALID")

    rows = details.get("rows")
    _require(isinstance(rows, list) and rows, "ROWS_INVALID")
    for r in rows:
        _require(isinstance(r, dict), "ROW_SHAPE_INVALID")
        ti = r.get("target_identity")
        _require(isinstance(ti, dict) and set(ti) == {"market_id", "condition_id", "token_id", "side"}
                  and all(isinstance(ti[k], str) and ti[k] for k in ti), "ROW_TARGET_IDENTITY_INVALID")
    row_market_ids = [r["target_identity"]["market_id"] for r in rows]
    _require(len(set(row_market_ids)) == len(row_market_ids)
             and set(row_market_ids) == set(partition_by_id), "ROW_PARTITION_MISMATCH")
    _require(all(isinstance(r.get("decision_id"), str) and r["decision_id"] for r in rows),
             "ROW_DECISION_ID_INVALID")
    _require(len({r.get("decision_id") for r in rows}) == len(rows), "ROW_DECISION_ID_DUPLICATED")

    request_sha256 = details.get("request_sha256")
    _require(isinstance(request_sha256, str) and bool(request_sha256), "REQUEST_SHA_MISSING")

    children = []
    for row in rows:
        decision_id = row.get("decision_id")
        child = _get(store, decision_id, missing_reason="CHILD_NOT_FOUND")
        _require(child["seq"] <= through_seq, "CHILD_AFTER_FRONTIER")
        _require(child["kind"] == "DECISION" and child["event_id"] == event_id, "CHILD_LINEAGE_MISMATCH")
        _require(child["sha256"] == row.get("decision_sha256"), "CHILD_SHA_MISMATCH")
        body = child["body"]
        _require(body.get("binding") == binding, "CHILD_BINDING_MISMATCH")
        target_identity = row["target_identity"]
        explanation = body.get("explanation")
        _require(isinstance(explanation, dict) and explanation.get("target_identity") == target_identity,
                  "CHILD_TARGET_IDENTITY_MISMATCH")
        _require(target_identity.get("side") == "YES", "CHILD_SIDE_INVALID")
        _require(explanation.get("request_sha256") == request_sha256, "CHILD_REQUEST_SHA_MISMATCH")
        _require(explanation.get("financial_authority") is False, "CHILD_FINANCIAL_AUTHORITY_VIOLATION")
        seq, recorded_at = child["seq"], body.get("recorded_at")
        _require(type(seq) is int and type(recorded_at) in (int, float), "CHILD_SHAPE_INVALID")
        children.append({"seq": seq, "recorded_at": recorded_at, "target_identity": target_identity})

    min_seq = min(c["seq"] for c in children)
    max_seq = max(c["seq"] for c in children)
    min_recorded_at = min(c["recorded_at"] for c in children)
    max_recorded_at = max(c["recorded_at"] for c in children)

    if not (max_seq < capture_row["seq"] and max_recorded_at <= capture_row["body"]["recorded_at"]):
        hold("CHILD_DECISION_AFTER_CAPTURE")

    buckets = []
    for child in sorted(children, key=lambda c: c["target_identity"]["market_id"]):
        ti = child["target_identity"]
        ref = partition_by_id[ti["market_id"]]
        buckets.append({"market_id": ti["market_id"], "condition_id": ti["condition_id"],
                         "yes_token": ti["token_id"], "no_token": ref["no_token"]})

    decision = {"event_id": event_id, "seq": min_seq, "recorded_at": min_recorded_at,
                "rule_fingerprint_sha256": binding["rule_fingerprint"], "buckets": tuple(buckets)}

    provenance.update({
        "no_token_provenance": "CAPTURE_EMBEDDED_RULE_COMMITMENT",
        "decision_seq_policy": "MIN_CHILD",
        "inference_cutoff": details.get("inference_cutoff"),
    })

    # --- Rule-state admission receipt, as of the decision, and drift windows. ---
    rule_state_rows, rule_state_truncated = _scan(store, kind="RULE_STATE", event_id=event_id,
                                                   through_seq=through_seq, cap=_RULE_STATE_SCAN_CAP)
    _require(not rule_state_truncated, "RULE_HISTORY_SCAN_INCOMPLETE")

    before = [r for r in rule_state_rows if r["seq"] < min_seq]
    rule_receipt_row = before[-1] if before else None
    rule_receipt = None
    receipt_fingerprint = None

    def rule_state_integrity(row: dict) -> bool:
        details = row["body"].get("details")
        return (isinstance(details, dict) and isinstance(details.get("preimage"), dict)
                and isinstance(details.get("fingerprint"), str)
                and digest(details["preimage"]) == details["fingerprint"])

    if rule_receipt_row is not None:
        rb, rd = rule_receipt_row["body"], rule_receipt_row["body"].get("details", {})
        # The receipt's own internal consistency: its declared preimage must
        # actually digest to its declared fingerprint. Without this, a
        # dishonest store could rewrite only `preimage` and this reader would
        # never notice -- the admissibility and drift checks below trust
        # `fingerprint` alone.
        _require(rule_state_integrity(rule_receipt_row), "RULE_RECEIPT_PREIMAGE_INTEGRITY")
        receipt_fingerprint = rd["fingerprint"]
        rule_receipt = {"event_id": rule_receipt_row["event_id"], "seq": rule_receipt_row["seq"],
                         "recorded_at": rb.get("recorded_at"), "fingerprint": receipt_fingerprint}
        admissible = (rd.get("version") == RULE_GUARD_VERSION and rd.get("quarantined") is False
                      and rd.get("state") in ADMISSIBLE_RULE_STATES)
        if not admissible:
            hold("RULE_STATE_NOT_ADMISSIBLE_AT_DECISION")
        # The receipt being independently admissible says nothing about
        # whether it is the rule the decision actually committed to -- that
        # is a separate, independent cross-check against the capture-
        # embedded `rule.sha256`.
        if receipt_fingerprint != rule.sha256:
            hold("RULE_RECEIPT_FINGERPRINT_MISMATCH")
        provenance["rule_receipt_join"] = ("FINGERPRINT_EQUALITY" if receipt_fingerprint == rule.sha256
                                           else "FINGERPRINT_MISMATCH")
        run_seq = rule_receipt_row["seq"]
        for candidate in reversed(before[:-1]):
            cd = candidate["body"].get("details", {})
            if (rule_state_integrity(candidate) and cd.get("quarantined") is False
                    and cd.get("fingerprint") == receipt_fingerprint):
                run_seq = candidate["seq"]
            else:
                break
        provenance["first_matching_rule_state_seq"] = run_seq
    else:
        hold("RULE_STATE_NOT_ADMISSIBLE_AT_DECISION")
        provenance["rule_receipt_join"] = "NO_RECEIPT"

    def drifted(row: dict) -> bool:
        # Measured against the decision's own committed fingerprint
        # (`rule.sha256`), not the (possibly wrong) selected receipt's own
        # fingerprint -- otherwise a receipt bound to the wrong rule would
        # make every genuinely-drifted row look consistent with it.
        if not rule_state_integrity(row):
            return True
        d = row["body"]["details"]
        return d.get("quarantined") is True or d["fingerprint"] != rule.sha256

    window_rows = [r for r in rule_state_rows if min_seq <= r["seq"] <= capture_row["seq"]]
    after_rows = [r for r in rule_state_rows if r["seq"] > capture_row["seq"]]
    if any(drifted(r) for r in window_rows):
        hold("RULE_DRIFT_IN_DECISION_WINDOW")
    if any(drifted(r) for r in after_rows):
        hold("RULE_DRIFT_AFTER_CAPTURE")

    disclosures, disc_prov, disc_holds = _scan_disclosures(
        store, event_id=event_id, through_seq=through_seq, partition_by_id=partition_by_id)
    for code in disc_holds:
        hold(code)
    provenance.update(disc_prov)

    return ReviewInputs(version=READER_VERSION, through_seq=through_seq, tip_sha256=tip_sha256,
                         capture_id=capture_id, rule=rule, rule_receipt=rule_receipt, decision=decision,
                         capture=capture_out, disclosures=disclosures, provenance=provenance,
                         holds=tuple(holds))
