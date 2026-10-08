"""Adversarial/synthetic tests for the pure Slice R reader.

Every store here is a synthetic tmp_path fixture (chmod 0o700); none reads a
real or private ledger. One happy-path test goes through the real learning-
capture v2 writer path (`capture_forecast_vector`, via the existing
`factory`/`evaluate` fixtures reused from test_v11_learning_capture.py) to
prove compatibility with genuine production-shaped data. Every other test
hand-constructs a minimal but structurally genuine RuleGuard-observed rule
plus DECISION/MEASUREMENT audit rows directly through `EvidenceStore`, so
each adversarial scenario (quarantine, drift, reorder, tamper, late child,
scan bounds, pin discipline, write/socket spying) can be controlled exactly.
"""
import copy
from contextlib import contextmanager
from dataclasses import asdict, replace

import pytest

from polymarket_scanner.v11 import learning_capture as capture
from polymarket_scanner.v11 import official_label_review_reader as reader_module
from polymarket_scanner.v11 import official_settlement_source
from polymarket_scanner.v11.evidence import EvidenceError, EvidenceStore, ReleaseBinding, canonical, digest, identity
from polymarket_scanner.v11.official_label_review_packet import build_review_packet
from polymarket_scanner.v11.official_label_review_reader import (
    PERMANENT_HOLDS, ReviewInputs, read_review_inputs,
)
from polymarket_scanner.v11.probability import _partition as threshold_partition
from polymarket_scanner.v11.rules import GUARD_VERSION, RuleGuard, fingerprint_event

from test_v11_certification_rules import setup
from test_v11_learning_capture import bundle, evaluate, kwargs
from test_v11_strategy_pipeline import factory
from test_weather_final_gpt6_exact_replays import _event


@contextmanager
def expect_refusal(code):
    with pytest.raises(EvidenceError) as exc:
        yield
    if exc.value.args != (code,):
        raise AssertionError(f"expected refusal {code!r}, got {exc.value.args!r}")


# --------------------------------------------------------------------------
# Hand-rolled, fully-controlled fixture primitives (no capture_forecast_vector).
# --------------------------------------------------------------------------

@pytest.fixture
def rig(tmp_path):
    tmp_path.chmod(0o700)
    now = [1_700_000_000.0]
    store = EvidenceStore(tmp_path / "evidence.sqlite", "V11_PAPER", clock=lambda: now[0])
    return store, now


def _observe(store, *, event, label):
    r = fingerprint_event(event, station_timezone="America/New_York", metadata_fingerprint="a" * 64)
    raw_id = label + ":raw"
    raw = store.capture(raw_id, event_id=r.payload["event_id"], kind="RULES", provider="fixture",
                         source_identity="gamma-event", revision=label, payload={"event": event},
                         evidence_class="SYNTHETIC")
    RuleGuard(store).observe(label, r, raw_evidence_id=raw["id"])
    return r, raw_id


def _evidence_row(store, *, event_id, record_id="model-evidence"):
    try:
        return store.get(record_id)
    except EvidenceError:
        return store.capture(record_id, event_id=event_id, kind="MODEL", provider="fixture",
                              source_identity="model-1", revision="1", payload={}, evidence_class="SYNTHETIC")


def _write_children(store, *, rule, binding, event_id, order, request_sha256, evidence_row, prefix):
    buckets_by_id = {b["market_id"]: b for b in rule.payload["partition"]}
    rows = []
    for market_id in order:
        bucket = buckets_by_id[market_id]
        target = dict(market_id=bucket["market_id"], condition_id=bucket["condition_id"],
                       token_id=bucket["yes_token"], side="YES")
        explanation = dict(target_identity=target, request_sha256=request_sha256, financial_authority=False)
        decision_id = prefix + ":decision:" + market_id
        decision = store.decision(decision_id, event_id=event_id, strategy="FUTURE_FORECAST", binding=binding,
            evidence_ids=(evidence_row["id"],), feature_ready_at=evidence_row["body"]["available_at"],
            valuation_type="SETTLEMENT", target=capture.TARGET, outcome="GATED",
            reason="FORECAST_ONLY_NO_EXECUTABLE_ECONOMICS", explanation=explanation,
            expires_at=store.clock() + 600.)
        rows.append(dict(target_identity=target, decision_id=decision["id"], decision_sha256=decision["sha256"]))
    return rows


def _write_capture(store, *, event_id, rule, binding, rows, request_sha256, record_id, version=None):
    details = dict(version=version or capture.VERSION, request_sha256=request_sha256, rule=asdict(rule),
                    binding=asdict(binding), complete_event_vector=True, financial_authority=False,
                    target=capture.TARGET, selection_scope="ALL_BUCKETS_OF_THIS_EVALUATED_EVENT",
                    inference_cutoff=store.clock(), rows=rows)
    return store.audit(record_id, event_id=event_id, kind="MEASUREMENT", details=details, evidence_ids=())


def _build_simple_capture(store, *, label="base", market_order=None):
    """One clean v2 capture: observe -> 3 children in threshold order -> capture."""
    event = _event(station="KATL", family="high")
    rule, _ = _observe(store, event=event, label=label + "-rule")
    event_id = rule.payload["event_id"]
    binding = ReleaseBinding("a" * 40, "b" * 40, "c" * 64, "d" * 64, rule.sha256)
    evidence_row = _evidence_row(store, event_id=event_id, record_id=label + "-evidence")
    request_sha256 = digest(["request", label])
    order = market_order or [b["market_id"] for b in threshold_partition(rule)]
    rows = _write_children(store, rule=rule, binding=binding, event_id=event_id, order=order,
                            request_sha256=request_sha256, evidence_row=evidence_row, prefix=label)
    captured = _write_capture(store, event_id=event_id, rule=rule, binding=binding, rows=rows,
                               request_sha256=request_sha256, record_id=label + "-capture")
    return dict(rule=rule, event_id=event_id, binding=binding, rows=rows, capture=captured,
                request_sha256=request_sha256, evidence_row=evidence_row)


class TamperedView:
    """Delegates everything except `get`, which applies a per-id mutation."""
    def __init__(self, source, overrides):
        self.source, self.overrides = source, overrides

    def get(self, key):
        row = self.source.get(key)
        fn = self.overrides.get(key)
        if fn is None:
            return row
        row = copy.deepcopy(row)
        fn(row)
        return row

    def records(self, **kwargs):
        return self.source.records(**kwargs)

    def pin_read_view(self, **kwargs):
        return self.source.pin_read_view(**kwargs)


# --------------------------------------------------------------------------
# 1. Happy path through the real production writer.
# --------------------------------------------------------------------------

def test_happy_mapping_through_real_writer_pipeline(factory):
    r = evaluate(factory)
    store = r["store"]
    d = r["evaluation"]["body"]["details"]
    capture_id = d["learning_capture"]["capture_id"]
    assert d["learning_capture"]["status"] == "EVENT_VECTOR_CAPTURED_LABELS_PENDING"

    result = read_review_inputs(store=store, capture_id=capture_id)

    assert result.packet is None and result.gamma_comparator is None
    assert set(PERMANENT_HOLDS) <= set(result.holds)
    assert "CAPTURE_VERSION_UNSUPPORTED" not in result.holds
    assert "MULTIPLE_CAPTURES_FOR_EVENT" not in result.holds
    assert "CHILD_DECISION_AFTER_CAPTURE" not in result.holds
    assert "RULE_STATE_NOT_ADMISSIBLE_AT_DECISION" not in result.holds
    assert not any(getattr(result, name) for name in
                    ("independent_label_attestation", "settlement_authority", "calibration_authority",
                     "financial_authority", "automatic_promotion", "qualified"))

    rule = r["rule"]
    assert result.rule.sha256 == rule.sha256
    assert result.decision["event_id"] == result.capture["event_id"] == rule.payload["event_id"]
    assert [b["market_id"] for b in result.decision["buckets"]] == sorted(b["market_id"] for b in result.decision["buckets"])
    assert result.decision["rule_fingerprint_sha256"] == rule.sha256
    assert result.rule_receipt is not None and result.rule_receipt["fingerprint"] == rule.sha256
    assert result.provenance["no_token_provenance"] == "CAPTURE_EMBEDDED_RULE_COMMITMENT"
    assert result.provenance["rule_receipt_join"] == "FINGERPRINT_EQUALITY"
    through = store.pin_read_view()["through_seq"]
    assert result.through_seq == through


# --------------------------------------------------------------------------
# 2. T1: a quarantined RULE_STATE is not admissible as the decision's receipt.
# --------------------------------------------------------------------------

def test_quarantined_rule_state_is_not_admissible_at_decision(rig):
    store, now = rig
    event = _event(station="KATL", family="high")
    rule, raw_id = _observe(store, event=event, label="base")
    event_id = rule.payload["event_id"]
    now[0] += 1.
    RuleGuard(store).invalidate("quarantine", event_id=event_id, raw_evidence_id=raw_id,
                                 reason="TEST_ADVERSARIAL_QUARANTINE")
    now[0] += 1.
    binding = ReleaseBinding("a" * 40, "b" * 40, "c" * 64, "d" * 64, rule.sha256)
    evidence_row = _evidence_row(store, event_id=event_id)
    request_sha256 = digest(["request", "quarantine-case"])
    order = [b["market_id"] for b in threshold_partition(rule)]
    rows = _write_children(store, rule=rule, binding=binding, event_id=event_id, order=order,
                            request_sha256=request_sha256, evidence_row=evidence_row, prefix="q")
    captured = _write_capture(store, event_id=event_id, rule=rule, binding=binding, rows=rows,
                               request_sha256=request_sha256, record_id="q-capture")

    result = read_review_inputs(store=store, capture_id=captured["id"])
    assert "RULE_STATE_NOT_ADMISSIBLE_AT_DECISION" in result.holds
    # T1's trap: invalidate() copies the previous fingerprint through, so a
    # naive fingerprint-only matcher would have accepted this quarantined row.
    assert result.rule_receipt["fingerprint"] == rule.sha256


# --------------------------------------------------------------------------
# 3. Drift windows.
# --------------------------------------------------------------------------

def test_rule_drift_in_decision_window_is_flagged(rig):
    store, now = rig
    event = _event(station="KATL", family="high")
    rule, _ = _observe(store, event=event, label="base")
    event_id = rule.payload["event_id"]
    binding = ReleaseBinding("a" * 40, "b" * 40, "c" * 64, "d" * 64, rule.sha256)
    evidence_row = _evidence_row(store, event_id=event_id)
    request_sha256 = digest(["request", "drift-window"])
    order = [b["market_id"] for b in threshold_partition(rule)]

    now[0] += 1.
    first_rows = _write_children(store, rule=rule, binding=binding, event_id=event_id, order=order[:1],
                                  request_sha256=request_sha256, evidence_row=evidence_row, prefix="dw")

    now[0] += 1.
    drifted_event = copy.deepcopy(event)
    drifted_event["markets"][0]["clobTokenIds"] = ["drift-yes", "drift-no"]
    _observe(store, event=drifted_event, label="dw-drift-mid")

    now[0] += 1.
    rest_rows = _write_children(store, rule=rule, binding=binding, event_id=event_id, order=order[1:],
                                 request_sha256=request_sha256, evidence_row=evidence_row, prefix="dw")
    now[0] += 1.
    captured = _write_capture(store, event_id=event_id, rule=rule, binding=binding,
                               rows=first_rows + rest_rows, request_sha256=request_sha256,
                               record_id="dw-capture")

    result = read_review_inputs(store=store, capture_id=captured["id"])
    assert "RULE_DRIFT_IN_DECISION_WINDOW" in result.holds
    assert "RULE_DRIFT_AFTER_CAPTURE" not in result.holds
    assert "RULE_STATE_NOT_ADMISSIBLE_AT_DECISION" not in result.holds


def test_rule_drift_after_capture_is_flagged(rig):
    store, now = rig
    fixture = _build_simple_capture(store, label="da")
    now[0] += 1.
    drifted_event = copy.deepcopy(_event(station="KATL", family="high"))
    drifted_event["markets"][0]["clobTokenIds"] = ["drift2-yes", "drift2-no"]
    _observe(store, event=drifted_event, label="da-drift-after")

    result = read_review_inputs(store=store, capture_id=fixture["capture"]["id"])
    assert "RULE_DRIFT_AFTER_CAPTURE" in result.holds
    assert "RULE_DRIFT_IN_DECISION_WINDOW" not in result.holds
    assert "RULE_STATE_NOT_ADMISSIBLE_AT_DECISION" not in result.holds


# --------------------------------------------------------------------------
# 4. T5: bucket reorder normalization (reader side) and documentation of the
#    packet's own BUCKET_REORDERED check (packet side, pure, no store).
# --------------------------------------------------------------------------

def test_decision_buckets_are_sorted_by_market_id_despite_threshold_order_rows(rig):
    store, now = rig
    scrambled_labels = ["72°F or higher", "69°F or lower", "70-71°F"]
    event = _event(station="KATL", family="high", labels=scrambled_labels)
    rule, _ = _observe(store, event=event, label="t5")
    event_id = rule.payload["event_id"]
    threshold_order = [b["market_id"] for b in threshold_partition(rule)]
    assert threshold_order != sorted(threshold_order), "fixture must be genuinely scrambled"

    binding = ReleaseBinding("a" * 40, "b" * 40, "c" * 64, "d" * 64, rule.sha256)
    evidence_row = _evidence_row(store, event_id=event_id)
    request_sha256 = digest(["request", "t5"])
    rows = _write_children(store, rule=rule, binding=binding, event_id=event_id, order=threshold_order,
                            request_sha256=request_sha256, evidence_row=evidence_row, prefix="t5")
    assert [r["target_identity"]["market_id"] for r in rows] == threshold_order
    captured = _write_capture(store, event_id=event_id, rule=rule, binding=binding, rows=rows,
                               request_sha256=request_sha256, record_id="t5-capture")

    result = read_review_inputs(store=store, capture_id=captured["id"])
    mapped_order = [b["market_id"] for b in result.decision["buckets"]]
    assert mapped_order == sorted(threshold_order) != threshold_order
    for bucket in result.decision["buckets"]:
        ref = next(b for b in rule.payload["partition"] if b["market_id"] == bucket["market_id"])
        assert bucket["no_token"] == ref["no_token"]
    assert result.provenance["no_token_provenance"] == "CAPTURE_EMBEDDED_RULE_COMMITMENT"


def test_direct_packet_call_with_capture_order_buckets_flags_reordered():
    scrambled_labels = ["72°F or higher", "69°F or lower", "70-71°F"]
    event = _event(station="KATL", family="high", labels=scrambled_labels)
    rule = fingerprint_event(event, station_timezone="America/New_York", metadata_fingerprint="a" * 64)
    threshold_order = [b["market_id"] for b in threshold_partition(rule)]
    by_id = {b["market_id"]: b for b in rule.payload["partition"]}
    capture_order_buckets = tuple(
        dict(market_id=mid, condition_id=by_id[mid]["condition_id"], yes_token=by_id[mid]["yes_token"],
             no_token=by_id[mid]["no_token"])
        for mid in threshold_order)

    decision = dict(event_id=rule.payload["event_id"], seq=1, recorded_at=1., rule_fingerprint_sha256=rule.sha256,
                     buckets=capture_order_buckets)
    packet = build_review_packet(rule=rule, rule_receipt=None, decision=decision, capture=None,
                                  disclosures=(), source_claim={})
    assert "BUCKET_REORDERED" in packet.violations
    assert "BUCKET_SET_MISMATCH" not in packet.violations
    assert packet.mechanism_consistent is False


# --------------------------------------------------------------------------
# 5. T6: a mutated embedded rule cannot pass the integrity check.
# --------------------------------------------------------------------------

def test_mutated_embedded_rule_fails_integrity_check(rig):
    store, now = rig
    fixture = _build_simple_capture(store, label="t6")

    def tamper(row):
        # Flip a value character inside the embedded canonical JSON without
        # breaking its syntax, so `.payload` parses but the digest no longer
        # matches the stored `sha256` -- a tamper, not a parse failure.
        tampered = row["body"]["details"]["rule"]["canonical_json"].replace('"KATL"', '"KATZ"')
        assert tampered != row["body"]["details"]["rule"]["canonical_json"]
        row["body"]["details"]["rule"]["canonical_json"] = tampered

    view = TamperedView(store, {fixture["capture"]["id"]: tamper})
    with expect_refusal("RULE_FINGERPRINT_INTEGRITY"):
        read_review_inputs(store=view, capture_id=fixture["capture"]["id"])


# --------------------------------------------------------------------------
# 6/7. Child sha / binding / request_sha256 mismatches.
# --------------------------------------------------------------------------

def test_child_sha_mismatch_is_refused(rig):
    store, now = rig
    fixture = _build_simple_capture(store, label="csha")
    decision_id = fixture["rows"][0]["decision_id"]

    def tamper(row):
        row["body"]["explanation"]["request_sha256"] = "f" * 64
        row["sha256"] = digest(row["body"])

    view = TamperedView(store, {decision_id: tamper})
    with expect_refusal("LABEL_REVIEW_READER_CHILD_SHA_MISMATCH"):
        read_review_inputs(store=view, capture_id=fixture["capture"]["id"])


@pytest.mark.parametrize("field,refusal", [
    ("binding", "LABEL_REVIEW_READER_CHILD_BINDING_MISMATCH"),
    ("request_sha256", "LABEL_REVIEW_READER_CHILD_REQUEST_SHA_MISMATCH"),
])
def test_dishonest_store_stale_hash_cannot_mask_binding_or_request_mismatch(rig, field, refusal):
    """A store that mutates content but reports the ORIGINAL hash must still
    be caught: the reader checks binding/request_sha256 independently of the
    sha256 equality check, not only through it."""
    store, now = rig
    fixture = _build_simple_capture(store, label="dishonest-" + field)
    decision_id = fixture["rows"][0]["decision_id"]

    def tamper(row):
        if field == "binding":
            row["body"]["binding"] = dict(row["body"]["binding"], rule_fingerprint="f" * 64)
        else:
            row["body"]["explanation"]["request_sha256"] = "f" * 64
        # sha256 deliberately left stale/unchanged -- simulates a store that
        # misreports hash/content consistency.

    view = TamperedView(store, {decision_id: tamper})
    with expect_refusal(refusal):
        read_review_inputs(store=view, capture_id=fixture["capture"]["id"])


# --------------------------------------------------------------------------
# 8. T7: a genuinely later-appended (but lineage-correct) child is flagged.
# --------------------------------------------------------------------------

def test_late_child_after_capture_is_flagged(rig):
    store, now = rig
    event = _event(station="KATL", family="high")
    rule, _ = _observe(store, event=event, label="late")
    event_id = rule.payload["event_id"]
    binding = ReleaseBinding("a" * 40, "b" * 40, "c" * 64, "d" * 64, rule.sha256)
    evidence_row = _evidence_row(store, event_id=event_id)
    request_sha256 = digest(["request", "late"])
    order = [b["market_id"] for b in threshold_partition(rule)]

    now[0] += 1.
    early_rows = _write_children(store, rule=rule, binding=binding, event_id=event_id, order=order[:-1],
                                  request_sha256=request_sha256, evidence_row=evidence_row, prefix="late")

    late_market_id = order[-1]
    bucket = next(b for b in rule.payload["partition"] if b["market_id"] == late_market_id)
    target = dict(market_id=bucket["market_id"], condition_id=bucket["condition_id"],
                  token_id=bucket["yes_token"], side="YES")
    explanation = dict(target_identity=target, request_sha256=request_sha256, financial_authority=False)
    late_decision_id = "late:decision:" + late_market_id
    late_at = now[0] + 5.
    expires_at = late_at + 600.
    expected_body = dict(
        strategy=identity("FUTURE_FORECAST"), binding=asdict(binding),
        evidence=[{"id": evidence_row["id"], "sha256": evidence_row["sha256"]}],
        feature_ready_at=evidence_row["body"]["available_at"], valuation_type="SETTLEMENT",
        target=identity(capture.TARGET), outcome="GATED",
        reason=identity("FORECAST_ONLY_NO_EXECUTABLE_ECONOMICS"), explanation=explanation,
        expires_at=expires_at, execution_status="NONFINANCIAL_NOT_SUBMITTED", evidence_class="SYNTHETIC")
    expected_body = dict(expected_body, namespace=store.namespace, financial_authority=False,
                          record_id=late_decision_id, kind="DECISION", event_id=event_id,
                          recorded_at=late_at, available_at=late_at)
    expected_sha = digest(expected_body)
    late_row = dict(target_identity=target, decision_id=late_decision_id, decision_sha256=expected_sha)

    captured = _write_capture(store, event_id=event_id, rule=rule, binding=binding,
                               rows=early_rows + [late_row], request_sha256=request_sha256,
                               record_id="late-capture")

    now[0] = late_at
    late_decision = store.decision(late_decision_id, event_id=event_id, strategy="FUTURE_FORECAST",
        binding=binding, evidence_ids=(evidence_row["id"],), feature_ready_at=evidence_row["body"]["available_at"],
        valuation_type="SETTLEMENT", target=capture.TARGET, outcome="GATED",
        reason="FORECAST_ONLY_NO_EXECUTABLE_ECONOMICS", explanation=explanation, expires_at=expires_at)
    assert late_decision["sha256"] == expected_sha
    assert late_decision["seq"] > captured["seq"]

    result = read_review_inputs(store=store, capture_id=captured["id"])
    assert "CHILD_DECISION_AFTER_CAPTURE" in result.holds


# --------------------------------------------------------------------------
# 9. Legacy version / multiple captures.
# --------------------------------------------------------------------------

def test_legacy_capture_version_is_unsupported(rig):
    store, now = rig
    event = _event(station="KATL", family="high")
    rule, _ = _observe(store, event=event, label="legacy")
    event_id = rule.payload["event_id"]
    captured = store.audit("legacy-capture", event_id=event_id, kind="MEASUREMENT",
                            details=dict(version=capture.LEGACY_VERSION, note="legacy-shape"), evidence_ids=())

    result = read_review_inputs(store=store, capture_id=captured["id"])
    assert "CAPTURE_VERSION_UNSUPPORTED" in result.holds
    assert result.rule is None and result.decision is None and result.rule_receipt is None
    assert result.capture == {"event_id": event_id, "seq": captured["seq"],
                               "recorded_at": captured["body"]["recorded_at"]}
    assert result.packet is None


def test_second_capture_for_event_is_flagged(rig):
    store, now = rig
    first = _build_simple_capture(store, label="multi-a")
    now[0] += 1.
    event = _event(station="KATL", family="high")
    rule, _ = _observe(store, event=event, label="multi-b-rule")
    assert rule.sha256 == first["rule"].sha256
    event_id = first["event_id"]
    binding = first["binding"]
    evidence_row = _evidence_row(store, event_id=event_id, record_id="multi-b-evidence")
    request_sha256 = digest(["request", "multi-b"])
    order = [b["market_id"] for b in threshold_partition(rule)]
    rows = _write_children(store, rule=rule, binding=binding, event_id=event_id, order=order,
                            request_sha256=request_sha256, evidence_row=evidence_row, prefix="multi-b")
    second = _write_capture(store, event_id=event_id, rule=rule, binding=binding, rows=rows,
                             request_sha256=request_sha256, record_id="multi-b-capture")

    result = read_review_inputs(store=store, capture_id=first["capture"]["id"])
    assert "MULTIPLE_CAPTURES_FOR_EVENT" in result.holds
    assert result.provenance["other_capture_ids"] == (second["id"],)


# --------------------------------------------------------------------------
# 10. D3 disclosures: lookahead-via-packet, unselected recapture, discovery copy.
# --------------------------------------------------------------------------

def _closed_market(market_id, yes_token, no_token, *, condition_id, winner=True):
    return {"id": market_id, "closed": True, "conditionId": condition_id,
            "clobTokenIds": [yes_token, no_token], "outcomePrices": ["1", "0"] if winner else ["0", "1"]}


def test_closed_market_disclosure_before_decision_flags_lookahead_via_packet(rig):
    store, now = rig
    event = _event(station="KATL", family="high")
    rule, _ = _observe(store, event=event, label="lookahead-rule")
    event_id = rule.payload["event_id"]
    bucket = rule.payload["partition"][0]

    # Written FIRST, before anything else -- the winner is already knowable
    # before any decision exists.
    early = store.capture("lookahead-early-payout", event_id=event_id, kind="RULES", provider="GAMMA_CLOSED_MARKET",
        source_identity="market:" + bucket["market_id"], revision="gamma-1", evidence_class="PUBLIC_OBSERVED",
        payload={"response": _closed_market(bucket["market_id"], bucket["yes_token"], bucket["no_token"],
                                             condition_id=bucket["condition_id"])})

    now[0] += 1.
    fixture_market_order = [b["market_id"] for b in threshold_partition(rule)]
    binding = ReleaseBinding("a" * 40, "b" * 40, "c" * 64, "d" * 64, rule.sha256)
    evidence_row = _evidence_row(store, event_id=event_id)
    request_sha256 = digest(["request", "lookahead"])
    rows = _write_children(store, rule=rule, binding=binding, event_id=event_id, order=fixture_market_order,
                            request_sha256=request_sha256, evidence_row=evidence_row, prefix="lookahead")
    now[0] += 1.
    captured = _write_capture(store, event_id=event_id, rule=rule, binding=binding, rows=rows,
                               request_sha256=request_sha256, record_id="lookahead-capture")

    result = read_review_inputs(store=store, capture_id=captured["id"])
    assert any(d["id"] == early["id"] for d in result.disclosures)

    source_claim = dict(version=official_settlement_source.VERSION, code="SYNTHETIC_DERIVATION_ONLY",
        rule_fingerprint_sha256=rule.sha256, station=rule.payload["station"], target_date=rule.payload["target_date"],
        independent_label_attestation=False, settlement_authority=False, financial_authority=False,
        automatic_promotion=False, synthetic_mechanism_only=True, winning_market_id=bucket["market_id"],
        winning_yes_token=bucket["yes_token"], raw_sha256="f" * 64)
    packet = build_review_packet(rule=result.rule, rule_receipt=result.rule_receipt, decision=result.decision,
                                  capture=result.capture, disclosures=result.disclosures, source_claim=source_claim)
    assert "LOOKAHEAD_VIOLATION" in packet.violations


def test_unselected_recapture_disclosure_is_included(rig):
    store, now = rig
    fixture = _build_simple_capture(store, label="recapture")
    bucket = fixture["rule"].payload["partition"][0]
    now[0] += 1.
    first = store.capture("recapture-payout-1", event_id=fixture["event_id"], kind="RULES",
        provider="GAMMA_CLOSED_MARKET", source_identity="market:" + bucket["market_id"], revision="gamma-1",
        evidence_class="PUBLIC_OBSERVED",
        payload={"response": _closed_market(bucket["market_id"], bucket["yes_token"], bucket["no_token"],
                                             condition_id=bucket["condition_id"])})
    now[0] += 1.
    second = store.capture("recapture-payout-2", event_id=fixture["event_id"], kind="RULES",
        provider="GAMMA_CLOSED_MARKET", source_identity="market:" + bucket["market_id"], revision="gamma-2",
        evidence_class="PUBLIC_OBSERVED",
        payload={"response": _closed_market(bucket["market_id"], bucket["yes_token"], bucket["no_token"],
                                             condition_id=bucket["condition_id"])})

    result = read_review_inputs(store=store, capture_id=fixture["capture"]["id"])
    ids = {d["id"] for d in result.disclosures}
    assert first["id"] in ids and second["id"] in ids


def test_discovery_event_copy_with_payout_state_is_included(rig):
    store, now = rig
    fixture = _build_simple_capture(store, label="discovery")
    bucket = fixture["rule"].payload["partition"][0]
    now[0] += 1.
    market = _closed_market(bucket["market_id"], bucket["yes_token"], bucket["no_token"],
                             condition_id=bucket["condition_id"])
    event_copy = {"id": fixture["event_id"], "markets": [market]}
    disclosed = store.capture("discovery-copy", event_id=fixture["event_id"], kind="RULES",
        provider="GAMMA_DISCOVERY_EVENT", source_identity=fixture["event_id"], revision="discovery-1",
        evidence_class="PUBLIC_OBSERVED", payload={"event": event_copy})

    result = read_review_inputs(store=store, capture_id=fixture["capture"]["id"])
    assert any(d["id"] == disclosed["id"] for d in result.disclosures)


def test_label_disclosure_is_included_with_knowable_at_clamped(rig):
    store, now = rig
    fixture = _build_simple_capture(store, label="label")
    bucket = fixture["rule"].payload["partition"][0]
    now[0] += 10.
    knowable_at = now[0] - 5.
    label_row = store.capture("label-1", event_id=fixture["event_id"], kind="LABEL", provider="TEST_ONLY",
        source_identity=bucket["yes_token"], revision="test-v1", evidence_class="SYNTHETIC",
        payload=dict(target_identity=dict(market_id=bucket["market_id"], condition_id=bucket["condition_id"],
                                           token_id=bucket["yes_token"], side="YES"),
                     knowable_at=knowable_at, value=1))

    result = read_review_inputs(store=store, capture_id=fixture["capture"]["id"])
    entry = next(d for d in result.disclosures if d["id"] == label_row["id"])
    assert entry["recorded_at"] == knowable_at


def test_intraday_proxy_observation_is_reported_as_information(rig):
    store, now = rig
    fixture = _build_simple_capture(store, label="proxy")
    now[0] += 1.
    store.capture("proxy-1", event_id=fixture["event_id"], kind="OFFICIAL_OBSERVATION", provider="NOAA_AWC",
        source_identity="KATL", revision="1", evidence_class="SYNTHETIC", payload={})

    result = read_review_inputs(store=store, capture_id=fixture["capture"]["id"])
    assert "INTRADAY_PROXY_INFORMATION_PRESENT" in result.holds
    assert result.provenance["proxy_receipts_target_day_before_decision"] == 1


# --------------------------------------------------------------------------
# 11. Scan bound reached.
# --------------------------------------------------------------------------

def test_disclosure_scan_bound_reached_is_incomplete_not_truncated(rig, monkeypatch):
    store, now = rig
    fixture = _build_simple_capture(store, label="bound")
    bucket = fixture["rule"].payload["partition"][0]
    now[0] += 1.
    store.capture("bound-payout", event_id=fixture["event_id"], kind="RULES", provider="GAMMA_CLOSED_MARKET",
        source_identity="market:" + bucket["market_id"], revision="gamma-1", evidence_class="PUBLIC_OBSERVED",
        payload={"response": _closed_market(bucket["market_id"], bucket["yes_token"], bucket["no_token"],
                                             condition_id=bucket["condition_id"])})
    monkeypatch.setattr(reader_module, "_DISCLOSURE_SCAN_CAP", 0)

    result = read_review_inputs(store=store, capture_id=fixture["capture"]["id"])
    assert "DISCLOSURE_SCAN_INCOMPLETE" in result.holds
    assert result.disclosures == ()


# --------------------------------------------------------------------------
# 12. Pin discipline: appended-after-pin rows are excluded.
# --------------------------------------------------------------------------

class PinInjectingView:
    def __init__(self, source, inject):
        self.source, self.inject = source, inject

    def get(self, key):
        return self.source.get(key)

    def records(self, **kwargs):
        return self.source.records(**kwargs)

    def pin_read_view(self, **kwargs):
        pin = self.source.pin_read_view(**kwargs)
        self.inject()
        return pin


def test_records_appended_after_pin_are_excluded(rig):
    store, now = rig
    fixture = _build_simple_capture(store, label="pin")
    bucket = fixture["rule"].payload["partition"][0]
    injected_id = []

    def inject():
        now[0] += 1.
        row = store.capture("pin-late-payout", event_id=fixture["event_id"], kind="RULES",
            provider="GAMMA_CLOSED_MARKET", source_identity="market:" + bucket["market_id"], revision="gamma-late",
            evidence_class="PUBLIC_OBSERVED",
            payload={"response": _closed_market(bucket["market_id"], bucket["yes_token"], bucket["no_token"],
                                                 condition_id=bucket["condition_id"])})
        injected_id.append(row["id"])

    view = PinInjectingView(store, inject)
    result = read_review_inputs(store=view, capture_id=fixture["capture"]["id"])
    assert injected_id and injected_id[0] not in {d["id"] for d in result.disclosures}
    # Confirm the injected row really exists in the underlying store (i.e. this
    # is exclusion-by-pin, not accidental absence from a scan bug).
    assert store.get(injected_id[0])["id"] == injected_id[0]


# --------------------------------------------------------------------------
# 13. Spy test: no writer method or network access is ever used.
# --------------------------------------------------------------------------

def test_reader_never_writes_or_touches_the_network(rig, monkeypatch):
    store, now = rig
    fixture = _build_simple_capture(store, label="spy")

    def boom(*a, **kw):
        raise AssertionError("reader must not call store writer methods")

    for name in ("audit", "capture", "decision", "safety_audit", "funnel", "source_result"):
        monkeypatch.setattr(store, name, boom)

    import socket

    def boom_connect(*a, **kw):
        raise AssertionError("reader must not touch the network")

    monkeypatch.setattr(socket.socket, "connect", boom_connect)

    result = read_review_inputs(store=store, capture_id=fixture["capture"]["id"])
    assert result.packet is None
    assert set(PERMANENT_HOLDS) <= set(result.holds)


# --------------------------------------------------------------------------
# 14. holds can never be empty; no authority flag can ever be forced True.
# --------------------------------------------------------------------------

def test_holds_never_empty_and_no_authority_flag_can_be_forced(rig):
    store, now = rig
    fixture = _build_simple_capture(store, label="authority")
    result = read_review_inputs(store=store, capture_id=fixture["capture"]["id"])
    assert result.holds

    with pytest.raises(TypeError):
        read_review_inputs(store=store, capture_id=fixture["capture"]["id"], qualified=True)

    for name in ("independent_label_attestation", "settlement_authority", "calibration_authority",
                 "financial_authority", "automatic_promotion", "qualified"):
        with pytest.raises(ValueError):
            replace(result, **{name: True})

    with pytest.raises(ValueError):
        replace(result, holds=())
    with pytest.raises(ValueError):
        replace(result, packet={"forged": "packet"})
    with pytest.raises(ValueError):
        replace(result, gamma_comparator={"forged": "comparator"})

    import dataclasses
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.holds = ()


# ==========================================================================
# 15. Repair round: RULE_STATE <-> decision binding (F1).
# ==========================================================================

class RuleStateFieldForgingView(TamperedView):
    """Delegates everything except `records`, which applies `mutate` to the
    `details` dict of every returned RULE_STATE row (deep-copied first)."""

    def __init__(self, source, mutate):
        super().__init__(source, {})
        self.mutate = mutate

    def records(self, **kwargs):
        rows = self.source.records(**kwargs)
        if kwargs.get("kind") == "RULE_STATE":
            rows = copy.deepcopy(rows)
            for r in rows:
                self.mutate(r["body"]["details"])
        return rows


def test_decision_bound_to_never_admitted_rule_is_flagged(rig):
    """RULE_STATE admits rule A; the capture/children commit to a different
    rule B (same event id, never independently observed). The reader must
    not silently claim FINGERPRINT_EQUALITY -- it must flag the mismatch."""
    store, now = rig
    event = _event(station="KATL", family="high")
    rule_a, _ = _observe(store, event=event, label="admitted")
    other = copy.deepcopy(event)
    other["markets"][0]["clobTokenIds"] = ["never-admitted-yes", "never-admitted-no"]
    rule_b = fingerprint_event(other, station_timezone="America/New_York", metadata_fingerprint="a" * 64)
    assert rule_b.payload["event_id"] == rule_a.payload["event_id"] and rule_b.sha256 != rule_a.sha256
    event_id = rule_a.payload["event_id"]
    binding = ReleaseBinding("a" * 40, "b" * 40, "c" * 64, "d" * 64, rule_b.sha256)
    evidence_row = _evidence_row(store, event_id=event_id)
    request_sha256 = digest(["request", "never-admitted"])
    now[0] += 1.
    order = [b["market_id"] for b in threshold_partition(rule_b)]
    rows = _write_children(store, rule=rule_b, binding=binding, event_id=event_id, order=order,
                            request_sha256=request_sha256, evidence_row=evidence_row, prefix="nb")
    now[0] += 1.
    captured = _write_capture(store, event_id=event_id, rule=rule_b, binding=binding, rows=rows,
                               request_sha256=request_sha256, record_id="nb-capture")

    result = read_review_inputs(store=store, capture_id=captured["id"])
    assert result.rule.sha256 == rule_b.sha256
    assert result.rule_receipt["fingerprint"] == rule_a.sha256 != result.rule.sha256
    assert "RULE_RECEIPT_FINGERPRINT_MISMATCH" in result.holds
    assert result.provenance["rule_receipt_join"] == "FINGERPRINT_MISMATCH"
    # RULE_STATE_NOT_ADMISSIBLE_AT_DECISION is a separate, orthogonal fact
    # about rule A's own state -- rule A is genuinely admissible on its own.
    assert "RULE_STATE_NOT_ADMISSIBLE_AT_DECISION" not in result.holds


def test_rule_receipt_preimage_integrity_is_verified(rig):
    """The selected RULE_STATE row's declared `preimage` must actually digest
    to its declared `fingerprint`. A dishonest store rewriting only the
    preimage must be refused, not silently trusted."""
    store, now = rig
    fixture = _build_simple_capture(store, label="preimage")

    def forge_preimage(details):
        details["preimage"] = dict(details["preimage"], station="KZZZ")

    view = RuleStateFieldForgingView(store, forge_preimage)
    with expect_refusal("LABEL_REVIEW_READER_RULE_RECEIPT_PREIMAGE_INTEGRITY"):
        read_review_inputs(store=view, capture_id=fixture["capture"]["id"])


@pytest.mark.parametrize("mutate,note", [
    (lambda d: d.update(quarantined=True), "quarantined-isolated"),
    (lambda d: d.update(state="SOME_OTHER_STATE"), "state-isolated"),
    (lambda d: d.update(version="legacy-guard-v0"), "version-isolated"),
])
def test_receipt_admissibility_checks_are_each_independently_enforced(rig, mutate, note):
    """Each of the three admissibility fields (version, quarantined, state)
    must independently gate RULE_STATE_NOT_ADMISSIBLE_AT_DECISION -- a mutant
    that drops only one of the three checks must still be caught because the
    other two stay genuinely valid here."""
    store, now = rig
    fixture = _build_simple_capture(store, label="adm-" + note)
    view = RuleStateFieldForgingView(store, mutate)
    result = read_review_inputs(store=view, capture_id=fixture["capture"]["id"])
    assert "RULE_STATE_NOT_ADMISSIBLE_AT_DECISION" in result.holds
    # The mutation never touches fingerprint/preimage, so the independent
    # fingerprint-binding check must stay unaffected.
    assert "RULE_RECEIPT_FINGERPRINT_MISMATCH" not in result.holds


def test_drift_window_is_measured_against_decision_rule_not_mismatched_receipt(rig):
    """A RULE_STATE row inside the decision window that matches the rule the
    decision actually committed to must not itself be reported as drift,
    even when the *selected receipt* (the latest RULE_STATE before the first
    child) is bound to a different rule entirely.

    `RuleGuard.observe` itself always quarantines a row that changes the
    fingerprint from its predecessor, so a genuine `observe()` call cannot
    produce the discriminating fixture here (the quarantined-row check
    would mask the drift-baseline check either way). This writes the
    window's `RULE_STATE` directly through `store.audit`, exactly as
    `RuleGuard.observe` would have shaped it, to isolate the one check this
    test targets -- the same technique `test_legacy_capture_version_is_unsupported`
    uses to bypass the normal capture writer.
    """
    store, now = rig
    event = _event(station="KATL", family="high")
    rule_a, _ = _observe(store, event=event, label="base-a")
    event_id = rule_a.payload["event_id"]
    other = copy.deepcopy(event)
    other["markets"][0]["clobTokenIds"] = ["baseline-b-yes", "baseline-b-no"]
    rule_b = fingerprint_event(other, station_timezone="America/New_York", metadata_fingerprint="a" * 64)
    binding = ReleaseBinding("a" * 40, "b" * 40, "c" * 64, "d" * 64, rule_b.sha256)
    evidence_row = _evidence_row(store, event_id=event_id)
    request_sha256 = digest(["request", "baseline"])
    now[0] += 1.
    order = [b["market_id"] for b in threshold_partition(rule_b)]
    rows = _write_children(store, rule=rule_b, binding=binding, event_id=event_id, order=order,
                            request_sha256=request_sha256, evidence_row=evidence_row, prefix="baseline")
    now[0] += 1.
    store.audit("baseline-b-window-state", event_id=event_id, kind="RULE_STATE",
                details={"version": GUARD_VERSION, "fingerprint": rule_b.sha256, "preimage": rule_b.payload,
                         "source_event_sha256": rule_b.source_event_sha256, "quarantined": False,
                         "state": "SEMANTICS_OBSERVED", "changed": False}, evidence_ids=())
    now[0] += 1.
    captured = _write_capture(store, event_id=event_id, rule=rule_b, binding=binding, rows=rows,
                               request_sha256=request_sha256, record_id="baseline-capture")

    result = read_review_inputs(store=store, capture_id=captured["id"])
    assert result.rule.sha256 == rule_b.sha256
    assert result.rule_receipt["fingerprint"] == rule_a.sha256
    assert "RULE_RECEIPT_FINGERPRINT_MISMATCH" in result.holds
    assert "RULE_DRIFT_IN_DECISION_WINDOW" not in result.holds


# ==========================================================================
# 16. Repair round: embedded rule's event must bind the capture's event (F2).
# ==========================================================================

def test_capture_embedding_other_events_rule_is_refused(rig):
    store, now = rig
    rule_e, _ = _observe(store, event=_event(station="KATL", family="high", eid="event-E"), label="e")
    rule_f = fingerprint_event(_event(station="KATL", family="high", eid="event-F"),
                                station_timezone="America/New_York", metadata_fingerprint="a" * 64)
    e_id, f_id = rule_e.payload["event_id"], rule_f.payload["event_id"]
    assert e_id != f_id
    binding = ReleaseBinding("a" * 40, "b" * 40, "c" * 64, "d" * 64, rule_f.sha256)
    evidence_row = _evidence_row(store, event_id=e_id)
    request_sha256 = digest(["request", "cross-event"])
    now[0] += 1.
    order = [b["market_id"] for b in threshold_partition(rule_f)]
    rows = _write_children(store, rule=rule_f, binding=binding, event_id=e_id, order=order,
                            request_sha256=request_sha256, evidence_row=evidence_row, prefix="xe")
    now[0] += 1.
    captured = _write_capture(store, event_id=e_id, rule=rule_f, binding=binding, rows=rows,
                               request_sha256=request_sha256, record_id="xe-capture")
    with expect_refusal("LABEL_REVIEW_READER_RULE_EVENT_MISMATCH"):
        read_review_inputs(store=store, capture_id=captured["id"])


# ==========================================================================
# 17. Repair round: D3 disclosure scan is provider-agnostic (F3).
# ==========================================================================

@pytest.mark.parametrize("provider,payload_for", [
    ("GAMMA_EVENT", lambda eid, m: {"event": {"id": eid, "markets": [m]}}),
    ("GAMMA_EVENT_LIST", lambda eid, m: {"response": [{"id": eid, "markets": [m]}]}),
    ("GAMMA_MARKET", lambda eid, m: {"response": m}),
    ("GAMMA_CLOSED_MARKET_EXACT_TOKEN_PAYOUT", lambda eid, m: {"response": m}),
])
def test_pre_decision_payout_from_any_gamma_provider_is_disclosed(rig, provider, payload_for):
    """Contract section 4, D3: payout receipts disclose the winner from *any*
    provider, not only GAMMA_CLOSED_MARKET/GAMMA_DISCOVERY_EVENT. A packet
    built from the mapped disclosures must flag LOOKAHEAD_VIOLATION."""
    store, now = rig
    event = _event(station="KATL", family="high")
    rule, _ = _observe(store, event=event, label="anyprov-" + provider.lower())
    event_id = rule.payload["event_id"]
    bucket = rule.payload["partition"][0]
    market = _closed_market(bucket["market_id"], bucket["yes_token"], bucket["no_token"],
                             condition_id=bucket["condition_id"])
    early = store.capture("anyprov-early-" + provider.lower(), event_id=event_id, kind="RULES", provider=provider,
                           source_identity="event:" + event_id, revision="r1", evidence_class="PUBLIC_OBSERVED",
                           payload=payload_for(event_id, market))
    now[0] += 1.
    binding = ReleaseBinding("a" * 40, "b" * 40, "c" * 64, "d" * 64, rule.sha256)
    evidence_row = _evidence_row(store, event_id=event_id)
    request_sha256 = digest(["request", "anyprov-" + provider])
    now[0] += 1.
    order = [b["market_id"] for b in threshold_partition(rule)]
    rows = _write_children(store, rule=rule, binding=binding, event_id=event_id, order=order,
                            request_sha256=request_sha256, evidence_row=evidence_row, prefix="anyprov")
    now[0] += 1.
    captured = _write_capture(store, event_id=event_id, rule=rule, binding=binding, rows=rows,
                               request_sha256=request_sha256, record_id="anyprov-capture-" + provider.lower())

    result = read_review_inputs(store=store, capture_id=captured["id"])
    assert early["id"] in {d["id"] for d in result.disclosures}
    source_claim = dict(version=official_settlement_source.VERSION, code="SYNTHETIC_DERIVATION_ONLY",
        rule_fingerprint_sha256=rule.sha256, station=rule.payload["station"], target_date=rule.payload["target_date"],
        independent_label_attestation=False, settlement_authority=False, financial_authority=False,
        automatic_promotion=False, synthetic_mechanism_only=True, winning_market_id=bucket["market_id"],
        winning_yes_token=bucket["yes_token"], raw_sha256="f" * 64)
    packet = build_review_packet(rule=result.rule, rule_receipt=result.rule_receipt, decision=result.decision,
                                  capture=result.capture, disclosures=result.disclosures, source_claim=source_claim)
    assert "LOOKAHEAD_VIOLATION" in packet.violations


@pytest.mark.parametrize("extension", [[], [{"id": "unrelated", "closed": False}]])
def test_market_payout_with_markets_extension_is_disclosed(rig, extension):
    store, now = rig
    rule, _ = _observe(store, event=_event(station="KATL", family="high"), label="hybrid")
    event_id = rule.payload["event_id"]
    bucket = rule.payload["partition"][0]
    market = _closed_market(bucket["market_id"], bucket["yes_token"], bucket["no_token"],
                            condition_id=bucket["condition_id"])
    market["markets"] = extension
    early = store.capture("hybrid-early", event_id=event_id, kind="RULES", provider="GAMMA_EVENT",
                          source_identity="synthetic", revision="1", evidence_class="SYNTHETIC",
                          payload={"market": market})
    now[0] += 1.
    binding = ReleaseBinding("a" * 40, "b" * 40, "c" * 64, "d" * 64, rule.sha256)
    evidence_row = _evidence_row(store, event_id=event_id, record_id="hybrid-model")
    request_sha256 = digest(["request", "hybrid"])
    rows = _write_children(store, rule=rule, binding=binding, event_id=event_id,
                           order=[b["market_id"] for b in threshold_partition(rule)],
                           request_sha256=request_sha256, evidence_row=evidence_row, prefix="hybrid")
    captured = _write_capture(store, event_id=event_id, rule=rule, binding=binding, rows=rows,
                               request_sha256=request_sha256, record_id="hybrid-capture")

    result = read_review_inputs(store=store, capture_id=captured["id"])
    assert early["seq"] < result.decision["seq"]
    assert early["id"] in {d["id"] for d in result.disclosures}
    assert "DISCLOSURE_SCAN_INCOMPLETE" not in result.holds
    assert set(PERMANENT_HOLDS) <= set(result.holds)
    assert result.packet is None and result.gamma_comparator is None
    assert all(getattr(result, name) is False for name in (
        "independent_label_attestation", "settlement_authority", "calibration_authority",
        "financial_authority", "automatic_promotion", "qualified"))

    # Packet construction is a synthetic diagnostic, outside the reader.
    source_claim = dict(version=official_settlement_source.VERSION, code="SYNTHETIC_DERIVATION_ONLY",
        rule_fingerprint_sha256=rule.sha256, station=rule.payload["station"], target_date=rule.payload["target_date"],
        independent_label_attestation=False, settlement_authority=False, financial_authority=False,
        automatic_promotion=False, synthetic_mechanism_only=True, winning_market_id=bucket["market_id"],
        winning_yes_token=bucket["yes_token"], raw_sha256="f" * 64)
    packet = build_review_packet(rule=result.rule, rule_receipt=result.rule_receipt, decision=result.decision,
                                  capture=result.capture, disclosures=result.disclosures, source_claim=source_claim)
    assert "LOOKAHEAD_VIOLATION" in packet.violations


def test_market_payout_with_deep_markets_child_marks_scan_incomplete(rig):
    store, now = rig
    rule, _ = _observe(store, event=_event(station="KATL", family="high"), label="hybrid-deep")
    event_id = rule.payload["event_id"]
    bucket = rule.payload["partition"][0]
    market = _closed_market(bucket["market_id"], bucket["yes_token"], bucket["no_token"],
                            condition_id=bucket["condition_id"])
    market["markets"] = [{"response": {"response": {}}}]
    store.capture("hybrid-deep-early", event_id=event_id, kind="RULES", provider="GAMMA_EVENT",
                  source_identity="synthetic", revision="1", evidence_class="SYNTHETIC",
                  payload={"market": market})
    now[0] += 1.
    binding = ReleaseBinding("a" * 40, "b" * 40, "c" * 64, "d" * 64, rule.sha256)
    evidence_row = _evidence_row(store, event_id=event_id, record_id="hybrid-deep-model")
    request_sha256 = digest(["request", "hybrid-deep"])
    rows = _write_children(store, rule=rule, binding=binding, event_id=event_id,
                           order=[b["market_id"] for b in threshold_partition(rule)],
                           request_sha256=request_sha256, evidence_row=evidence_row, prefix="hybrid-deep")
    captured = _write_capture(store, event_id=event_id, rule=rule, binding=binding, rows=rows,
                               request_sha256=request_sha256, record_id="hybrid-deep-capture")

    result = read_review_inputs(store=store, capture_id=captured["id"])
    assert result.disclosures == ()
    assert "DISCLOSURE_SCAN_INCOMPLETE" in result.holds
    assert set(PERMANENT_HOLDS) <= set(result.holds)
    assert result.packet is None and result.gamma_comparator is None


@pytest.mark.parametrize("also_shallow", [False, True])
def test_gamma_depth_limit_marks_disclosure_scan_incomplete(rig, also_shallow):
    """A market beyond the walk bound cannot produce a complete-looking tuple.

    The second case places a valid shallow payout first. Finding that payout
    must not short-circuit inspection of the later, over-depth branch.
    """
    store, now = rig
    rule, _ = _observe(store, event=_event(station="KATL", family="high"),
                       label="deep-shallow" if also_shallow else "deep-only")
    event_id = rule.payload["event_id"]
    bucket = rule.payload["partition"][0]
    market = _closed_market(bucket["market_id"], bucket["yes_token"], bucket["no_token"],
                            condition_id=bucket["condition_id"])
    deep = {"response": {"response": {"event": {"id": event_id, "markets": [market]}}}}
    payload = {"markets": [market], "response": deep} if also_shallow else deep
    early = store.capture("early-depth-payout", event_id=event_id, kind="RULES", provider="GAMMA_EVENT",
                          source_identity="event:" + event_id, revision="r1",
                          evidence_class="SYNTHETIC", payload=payload)
    binding = ReleaseBinding("a" * 40, "b" * 40, "c" * 64, "d" * 64, rule.sha256)
    evidence_row = _evidence_row(store, event_id=event_id, record_id="depth-model")
    request_sha256 = digest(["request", "depth"])
    now[0] += 1.
    rows = _write_children(store, rule=rule, binding=binding, event_id=event_id,
                            order=[b["market_id"] for b in threshold_partition(rule)],
                            request_sha256=request_sha256, evidence_row=evidence_row, prefix="depth")
    now[0] += 1.
    captured = _write_capture(store, event_id=event_id, rule=rule, binding=binding,
                               rows=rows, request_sha256=request_sha256, record_id="depth-capture")

    result = read_review_inputs(store=store, capture_id=captured["id"])
    assert early["seq"] < result.decision["seq"]
    assert result.disclosures == ()
    assert "DISCLOSURE_SCAN_INCOMPLETE" in result.holds
    assert set(PERMANENT_HOLDS) <= set(result.holds)
    assert result.packet is None and result.gamma_comparator is None
    assert all(getattr(result, name) is False for name in (
        "independent_label_attestation", "settlement_authority", "calibration_authority",
        "financial_authority", "automatic_promotion", "qualified"))


@pytest.mark.parametrize("location", ["window", "after"])
@pytest.mark.parametrize("malformation", ["changed", "missing"])
def test_malformed_consulted_rule_state_counts_as_drift(rig, location, malformation):
    store, now = rig
    label = "malformed-" + location + "-" + malformation
    rule, _ = _observe(store, event=_event(station="KATL", family="high"), label=label)
    event_id = rule.payload["event_id"]
    binding = ReleaseBinding("a" * 40, "b" * 40, "c" * 64, "d" * 64, rule.sha256)
    evidence_row = _evidence_row(store, event_id=event_id, record_id=label + "-model")
    request_sha256 = digest(["request", label])
    now[0] += 1.
    rows = _write_children(store, rule=rule, binding=binding, event_id=event_id,
                            order=[b["market_id"] for b in threshold_partition(rule)],
                            request_sha256=request_sha256, evidence_row=evidence_row, prefix=label)
    preimage = dict(rule.payload, station="KZZZ") if malformation == "changed" else None
    details = {"version": GUARD_VERSION, "fingerprint": rule.sha256,
               "state": "SEMANTICS_OBSERVED", "quarantined": False}
    if preimage is not None:
        details["preimage"] = preimage
        assert digest(preimage) != rule.sha256
    if location == "window":
        now[0] += 1.
        state = store.audit(label + "-state", event_id=event_id, kind="RULE_STATE",
                            details=details, evidence_ids=())
    now[0] += 1.
    captured = _write_capture(store, event_id=event_id, rule=rule, binding=binding,
                               rows=rows, request_sha256=request_sha256, record_id=label + "-capture")
    if location == "after":
        now[0] += 1.
        state = store.audit(label + "-state", event_id=event_id, kind="RULE_STATE",
                            details=details, evidence_ids=())
    assert (state["seq"] <= captured["seq"]) is (location == "window")

    result = read_review_inputs(store=store, capture_id=captured["id"])
    expected = "RULE_DRIFT_IN_DECISION_WINDOW" if location == "window" else "RULE_DRIFT_AFTER_CAPTURE"
    assert expected in result.holds
    assert "RULE_RECEIPT_FINGERPRINT_MISMATCH" not in result.holds
    assert result.provenance["rule_receipt_join"] == "FINGERPRINT_EQUALITY"
    assert set(PERMANENT_HOLDS) <= set(result.holds)
    assert result.packet is None and result.gamma_comparator is None
    assert result.qualified is False


def test_digest_consistent_partition_without_market_id_has_typed_refusal(rig):
    store, _ = rig
    fixture = _build_simple_capture(store, label="missing-partition-market")
    capture_id = fixture["capture"]["id"]

    def forge(row):
        details = row["body"]["details"]
        payload = copy.deepcopy(fixture["rule"].payload)
        del payload["partition"][0]["market_id"]
        details["rule"] = dict(canonical_json=canonical(payload), sha256=digest(payload),
                               source_event_sha256=fixture["rule"].source_event_sha256)
        details["binding"]["rule_fingerprint"] = digest(payload)

    with expect_refusal("LABEL_REVIEW_READER_RULE_PARTITION_INVALID"):
        read_review_inputs(store=TamperedView(store, {capture_id: forge}), capture_id=capture_id)


# ==========================================================================
# 18. Repair round: self-declared earlier publish/observe time (F4).
# ==========================================================================

def test_self_declared_published_at_clamps_rules_disclosure_time(rig):
    store, now = rig
    fixture = _build_simple_capture(store, label="pubtime")
    bucket = fixture["rule"].payload["partition"][0]
    decision_time = min(store.get(r["decision_id"])["body"]["recorded_at"] for r in fixture["rows"])
    now[0] += 10.
    earlier_published_at = decision_time - 100.
    row = store.capture("pubtime-payout", event_id=fixture["event_id"], kind="RULES", provider="GAMMA_CLOSED_MARKET",
                         source_identity="market:" + bucket["market_id"], revision="g1",
                         evidence_class="PUBLIC_OBSERVED", published_at=earlier_published_at,
                         payload={"response": _closed_market(bucket["market_id"], bucket["yes_token"],
                                                               bucket["no_token"], condition_id=bucket["condition_id"])})
    result = read_review_inputs(store=store, capture_id=fixture["capture"]["id"])
    entry = next(d for d in result.disclosures if d["id"] == row["id"])
    assert entry["recorded_at"] == earlier_published_at < row["body"]["recorded_at"]


# ==========================================================================
# 19. Repair round: holds type/vocabulary enforcement (F5).
# ==========================================================================

def test_holds_must_be_a_tuple_within_the_closed_vocabulary_retaining_permanents(rig):
    store, now = rig
    fixture = _build_simple_capture(store, label="holdsvocab")
    result = read_review_inputs(store=store, capture_id=fixture["capture"]["id"])

    with pytest.raises(ValueError):
        replace(result, holds=("READY",))  # drops every permanent hold
    with pytest.raises(ValueError):
        replace(result, holds="QUALIFIED")  # not a tuple
    with pytest.raises(ValueError):
        replace(result, holds=result.holds + ("SOMETHING_NOT_IN_THE_VOCABULARY",))
    with pytest.raises(ValueError):
        replace(result, holds=result.holds + (7,))  # non-str element


# ==========================================================================
# 20. Repair round: child DECISION frontier discipline (F6).
# ==========================================================================

def test_child_appended_after_the_pin_is_refused(rig):
    """A child DECISION that does not exist at the pin, but is injected
    during the pin_read_view call itself, must be refused rather than
    silently folded into the decision mapping."""
    store, now = rig
    event = _event(station="KATL", family="high")
    rule, _ = _observe(store, event=event, label="frontier")
    event_id = rule.payload["event_id"]
    binding = ReleaseBinding("a" * 40, "b" * 40, "c" * 64, "d" * 64, rule.sha256)
    ev = _evidence_row(store, event_id=event_id)
    req = digest(["request", "frontier"])
    order = [b["market_id"] for b in threshold_partition(rule)]
    now[0] += 1.
    rows = _write_children(store, rule=rule, binding=binding, event_id=event_id, order=order,
                            request_sha256=req, evidence_row=ev, prefix="frontier")
    last = store.get(rows[-1]["decision_id"])
    late_id = "frontier:late-child"
    late_at = now[0] + 2.
    body = dict(last["body"], record_id=late_id, recorded_at=late_at, available_at=late_at)
    rows[-1] = dict(rows[-1], decision_id=late_id, decision_sha256=digest(body))
    now[0] += 1.
    captured = _write_capture(store, event_id=event_id, rule=rule, binding=binding, rows=rows,
                               request_sha256=req, record_id="frontier-capture")

    def inject():
        now[0] = late_at
        b = last["body"]
        row = store.decision(late_id, event_id=event_id, strategy="FUTURE_FORECAST", binding=ReleaseBinding(**b["binding"]),
                              evidence_ids=tuple(e["id"] for e in b["evidence"]),
                              feature_ready_at=b["feature_ready_at"], valuation_type=b["valuation_type"],
                              target=b["target"], outcome=b["outcome"], reason=b["reason"],
                              explanation=b["explanation"], expires_at=b["expires_at"])
        assert row["sha256"] == rows[-1]["decision_sha256"]

    view = PinInjectingView(store, inject)
    with expect_refusal("LABEL_REVIEW_READER_CHILD_AFTER_FRONTIER"):
        read_review_inputs(store=view, capture_id=captured["id"])


# ==========================================================================
# 21. Repair round: typed refusals for malformed/missing lineage shapes (F7).
# ==========================================================================

def test_missing_capture_and_missing_child_raise_typed_refusals(rig):
    store, now = rig
    fixture = _build_simple_capture(store, label="missingtyped")
    cid = fixture["capture"]["id"]

    with expect_refusal("LABEL_REVIEW_READER_CAPTURE_NOT_FOUND"):
        read_review_inputs(store=store, capture_id="no-such-capture-at-all")

    def drop(row):
        row["body"]["details"]["rows"][0]["decision_id"] = "does-not-exist-at-all"
    with expect_refusal("LABEL_REVIEW_READER_CHILD_NOT_FOUND"):
        read_review_inputs(store=TamperedView(store, {cid: drop}), capture_id=cid)


@pytest.mark.parametrize("mutate,refusal", [
    (lambda d: d.__setitem__("rows", ["not-a-dict"]), "LABEL_REVIEW_READER_ROW_SHAPE_INVALID"),
    (lambda d: d["rule"].__setitem__("canonical_json", 7), "LABEL_REVIEW_READER_RULE_PREIMAGE_INVALID"),
])
def test_malformed_capture_shapes_raise_typed_refusals(rig, mutate, refusal):
    store, now = rig
    fixture = _build_simple_capture(store, label="typedshape")
    cid = fixture["capture"]["id"]
    view = TamperedView(store, {cid: lambda row: mutate(row["body"]["details"])})
    with expect_refusal(refusal):
        read_review_inputs(store=view, capture_id=cid)


@pytest.mark.parametrize("damage", ["rule_list", "rule_json", "decision_list"])
def test_archived_malformed_rule_and_decision_id_raise_typed_refusals(rig, damage):
    store, _ = rig
    fixture = _build_simple_capture(store, label="archived-" + damage)
    details = copy.deepcopy(fixture["capture"]["body"]["details"])
    if damage == "rule_list":
        details["rule"]["canonical_json"] = canonical([])
        details["rule"]["sha256"] = digest([])
    elif damage == "rule_json":
        details["rule"]["canonical_json"] = "{"
    else:
        details["rows"][0]["decision_id"] = []
    archived = store.audit("malformed-" + damage, event_id=fixture["event_id"],
                           kind="MEASUREMENT", details=details)
    refusal = ("LABEL_REVIEW_READER_ROW_DECISION_ID_INVALID" if damage == "decision_list"
               else "LABEL_REVIEW_READER_RULE_PREIMAGE_INVALID")
    with expect_refusal(refusal):
        read_review_inputs(store=store, capture_id=archived["id"])


# ==========================================================================
# 22. Test-adequacy round (F8): one discriminating test per lineage refusal
#     the author's suite left unexercised, plus contract test 6.
# ==========================================================================

def test_decision_seq_and_time_equal_child_minimum_not_maximum(rig):
    """Contract test 6: `decision.seq`/`recorded_at` must equal the MIN over
    children, not the max -- with children genuinely spread across distinct
    seqs and times, not all written in the same tick."""
    store, now = rig
    event = _event(station="KATL", family="high")
    rule, _ = _observe(store, event=event, label="minmax")
    event_id = rule.payload["event_id"]
    binding = ReleaseBinding("a" * 40, "b" * 40, "c" * 64, "d" * 64, rule.sha256)
    evidence_row = _evidence_row(store, event_id=event_id)
    request_sha256 = digest(["request", "minmax"])
    order = [b["market_id"] for b in threshold_partition(rule)]
    rows = []
    for i, market_id in enumerate(order):
        now[0] += 1.
        rows.extend(_write_children(store, rule=rule, binding=binding, event_id=event_id, order=[market_id],
                                     request_sha256=request_sha256, evidence_row=evidence_row, prefix=f"minmax{i}"))
    now[0] += 1.
    captured = _write_capture(store, event_id=event_id, rule=rule, binding=binding, rows=rows,
                               request_sha256=request_sha256, record_id="minmax-capture")
    children = [store.get(r["decision_id"]) for r in rows]

    result = read_review_inputs(store=store, capture_id=captured["id"])
    assert result.decision["seq"] == min(c["seq"] for c in children) != max(c["seq"] for c in children)
    assert (result.decision["recorded_at"] == min(c["body"]["recorded_at"] for c in children)
            != max(c["body"]["recorded_at"] for c in children))


@pytest.mark.parametrize("field,value,refusal", [
    ("side", "NO", "LABEL_REVIEW_READER_CHILD_SIDE_INVALID"),
    ("financial_authority", True, "LABEL_REVIEW_READER_CHILD_FINANCIAL_AUTHORITY_VIOLATION"),
])
def test_child_shape_level_refusals_are_each_independently_enforced(rig, field, value, refusal):
    """CHILD_SIDE_INVALID and CHILD_FINANCIAL_AUTHORITY_VIOLATION must each
    fire on their own -- not merely as a side effect of the sha/binding/
    target-identity checks that happen to run first."""
    store, now = rig
    fixture = _build_simple_capture(store, label="childshape-" + field)
    decision_id = fixture["rows"][0]["decision_id"]
    cid = fixture["capture"]["id"]
    original_child = store.get(decision_id)
    forged_body = copy.deepcopy(original_child["body"])
    if field == "side":
        forged_body["explanation"]["target_identity"] = dict(
            forged_body["explanation"]["target_identity"], side=value)
    else:
        forged_body["explanation"][field] = value
    forged_sha = digest(forged_body)

    def tamper_child(row):
        if field == "side":
            row["body"]["explanation"]["target_identity"] = dict(
                row["body"]["explanation"]["target_identity"], side=value)
        else:
            row["body"]["explanation"][field] = value
        row["sha256"] = forged_sha

    def tamper_capture(row):
        rows = row["body"]["details"]["rows"]
        idx = next(i for i, r in enumerate(rows) if r["decision_id"] == decision_id)
        updated = dict(rows[idx], decision_sha256=forged_sha)
        if field == "side":
            updated["target_identity"] = dict(updated["target_identity"], side=value)
        rows[idx] = updated

    view = TamperedView(store, {cid: tamper_capture, decision_id: tamper_child})
    with expect_refusal(refusal):
        read_review_inputs(store=view, capture_id=cid)


def _capture_details(store, *, rule, binding, rows, request_sha256, **overrides):
    details = dict(version=capture.VERSION, request_sha256=request_sha256, rule=asdict(rule),
                    binding=asdict(binding), complete_event_vector=True, financial_authority=False,
                    target=capture.TARGET, selection_scope="ALL_BUCKETS_OF_THIS_EVALUATED_EVENT",
                    inference_cutoff=store.clock(), rows=rows)
    details.update(overrides)
    return details


@pytest.mark.parametrize("override,refusal", [
    ({"financial_authority": True}, "LABEL_REVIEW_READER_CAPTURE_FINANCIAL_AUTHORITY_VIOLATION"),
    ({"complete_event_vector": False}, "LABEL_REVIEW_READER_COMPLETE_VECTOR_REQUIRED"),
    ({"target": "SOMETHING_ELSE"}, "LABEL_REVIEW_READER_CAPTURE_TARGET_INVALID"),
    ({"selection_scope": "PARTIAL"}, "LABEL_REVIEW_READER_CAPTURE_SELECTION_SCOPE_INVALID"),
])
def test_capture_level_authority_and_shape_checks_are_each_independently_enforced(rig, override, refusal):
    store, now = rig
    label = "capdet-" + next(iter(override))
    event = _event(station="KATL", family="high")
    rule, _ = _observe(store, event=event, label=label)
    event_id = rule.payload["event_id"]
    binding = ReleaseBinding("a" * 40, "b" * 40, "c" * 64, "d" * 64, rule.sha256)
    evidence_row = _evidence_row(store, event_id=event_id)
    request_sha256 = digest(["request", label])
    order = [b["market_id"] for b in threshold_partition(rule)]
    now[0] += 1.
    rows = _write_children(store, rule=rule, binding=binding, event_id=event_id, order=order,
                            request_sha256=request_sha256, evidence_row=evidence_row, prefix=label)
    details = _capture_details(store, rule=rule, binding=binding, rows=rows,
                                request_sha256=request_sha256, **override)
    captured = store.audit(label + "-capture", event_id=event_id, kind="MEASUREMENT",
                            details=details, evidence_ids=())
    with expect_refusal(refusal):
        read_review_inputs(store=store, capture_id=captured["id"])


def test_rule_binding_mismatch_is_refused(rig):
    """binding.rule_fingerprint must equal rule.sha256 -- independent of
    whether any children exist yet."""
    store, now = rig
    event = _event(station="KATL", family="high")
    rule, _ = _observe(store, event=event, label="bindmismatch")
    event_id = rule.payload["event_id"]
    real_binding = ReleaseBinding("a" * 40, "b" * 40, "c" * 64, "d" * 64, rule.sha256)
    evidence_row = _evidence_row(store, event_id=event_id)
    request_sha256 = digest(["request", "bindmismatch"])
    order = [b["market_id"] for b in threshold_partition(rule)]
    now[0] += 1.
    rows = _write_children(store, rule=rule, binding=real_binding, event_id=event_id, order=order,
                            request_sha256=request_sha256, evidence_row=evidence_row, prefix="bindmismatch")
    forged_binding = ReleaseBinding("a" * 40, "b" * 40, "c" * 64, "d" * 64, "f" * 64)
    details = _capture_details(store, rule=rule, binding=forged_binding, rows=rows, request_sha256=request_sha256)
    captured = store.audit("bindmismatch-capture", event_id=event_id, kind="MEASUREMENT",
                            details=details, evidence_ids=())
    with expect_refusal("LABEL_REVIEW_READER_RULE_BINDING_MISMATCH"):
        read_review_inputs(store=store, capture_id=captured["id"])


def test_row_partition_mismatch_on_incomplete_vector_is_refused(rig):
    """A capture whose rows cover only a strict subset of the rule's
    partition must be refused, not silently accepted as a smaller vector."""
    store, now = rig
    event = _event(station="KATL", family="high")
    rule, _ = _observe(store, event=event, label="partialvec")
    event_id = rule.payload["event_id"]
    binding = ReleaseBinding("a" * 40, "b" * 40, "c" * 64, "d" * 64, rule.sha256)
    evidence_row = _evidence_row(store, event_id=event_id)
    request_sha256 = digest(["request", "partialvec"])
    order = [b["market_id"] for b in threshold_partition(rule)]
    assert len(order) >= 2
    now[0] += 1.
    rows = _write_children(store, rule=rule, binding=binding, event_id=event_id, order=order[:-1],
                            request_sha256=request_sha256, evidence_row=evidence_row, prefix="partialvec")
    captured = _write_capture(store, event_id=event_id, rule=rule, binding=binding, rows=rows,
                               request_sha256=request_sha256, record_id="partialvec-capture")
    with expect_refusal("LABEL_REVIEW_READER_ROW_PARTITION_MISMATCH"):
        read_review_inputs(store=store, capture_id=captured["id"])


def test_duplicate_decision_id_across_rows_is_refused(rig):
    store, now = rig
    fixture = _build_simple_capture(store, label="dupid")
    cid = fixture["capture"]["id"]

    def dup(row):
        rows = row["body"]["details"]["rows"]
        rows[1] = dict(rows[1], decision_id=rows[0]["decision_id"], decision_sha256=rows[0]["decision_sha256"])

    with expect_refusal("LABEL_REVIEW_READER_ROW_DECISION_ID_DUPLICATED"):
        read_review_inputs(store=TamperedView(store, {cid: dup}), capture_id=cid)


def test_row_target_identity_malformed_is_refused(rig):
    store, now = rig
    fixture = _build_simple_capture(store, label="rowti")
    cid = fixture["capture"]["id"]

    def mutate(row):
        rows = row["body"]["details"]["rows"]
        rows[0] = dict(rows[0], target_identity=dict(rows[0]["target_identity"], condition_id=""))

    with expect_refusal("LABEL_REVIEW_READER_ROW_TARGET_IDENTITY_INVALID"):
        read_review_inputs(store=TamperedView(store, {cid: mutate}), capture_id=cid)


def test_capture_row_kind_invalid_is_refused(rig):
    store, now = rig
    fixture = _build_simple_capture(store, label="capkind")
    cid = fixture["capture"]["id"]

    def mutate(row):
        row["kind"] = "LABEL"

    with expect_refusal("LABEL_REVIEW_READER_CAPTURE_KIND_INVALID"):
        read_review_inputs(store=TamperedView(store, {cid: mutate}), capture_id=cid)


@pytest.mark.parametrize("mutate", [
    lambda row: row.update(event_id="some-other-event"),
    lambda row: row.update(kind="MEASUREMENT"),
])
def test_child_kind_or_event_mismatch_is_refused(rig, mutate):
    store, now = rig
    fixture = _build_simple_capture(store, label="childlin")
    decision_id = fixture["rows"][0]["decision_id"]
    view = TamperedView(store, {decision_id: mutate})
    with expect_refusal("LABEL_REVIEW_READER_CHILD_LINEAGE_MISMATCH"):
        read_review_inputs(store=view, capture_id=fixture["capture"]["id"])


def test_non_proxy_provider_official_observation_is_not_counted(rig):
    """The proxy-count/hold is gated on provider == NOAA_AWC specifically --
    any other provider under the same OFFICIAL_OBSERVATION kind must not
    be swept in."""
    store, now = rig
    fixture = _build_simple_capture(store, label="nonproxy")
    now[0] += 1.
    store.capture("nonproxy-1", event_id=fixture["event_id"], kind="OFFICIAL_OBSERVATION",
                  provider="SOME_OTHER_PROVIDER", source_identity="KATL", revision="1",
                  evidence_class="SYNTHETIC", payload={})
    result = read_review_inputs(store=store, capture_id=fixture["capture"]["id"])
    assert "INTRADAY_PROXY_INFORMATION_PRESENT" not in result.holds
    assert result.provenance["proxy_receipts_target_day_before_decision"] == 0


def test_label_disclosure_for_market_outside_partition_is_excluded(rig):
    store, now = rig
    fixture = _build_simple_capture(store, label="labelfilter")
    now[0] += 1.
    label_row = store.capture("labelfilter-unknown", event_id=fixture["event_id"], kind="LABEL", provider="TEST_ONLY",
        source_identity="unknown-token", revision="v1", evidence_class="SYNTHETIC",
        payload=dict(target_identity=dict(market_id="not-a-real-market", condition_id="cX",
                                           token_id="unknown-token", side="YES"), value=1))
    result = read_review_inputs(store=store, capture_id=fixture["capture"]["id"])
    assert label_row["id"] not in {d["id"] for d in result.disclosures}
