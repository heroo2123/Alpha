"""Adversarial/synthetic tests for the pure Gamma payout comparator reader.

Every store here is a synthetic tmp_path fixture (chmod 0o700); none reads a
real or private ledger. Every fixture hand-constructs retained `RULES`/`LABEL`
rows directly through `EvidenceStore.capture`, so each adversarial scenario
(full partition, missing/duplicate/conflicting winners, late receipts,
tampered lineage, bounded-scan exhaustion, contradictory Gamma provenance,
unlinked LABEL causality, inconsistent payout vectors, malformed rule
semantics, postdecision disclosure) can be controlled exactly. All tests are
deterministic and pass under both `python -m pytest` and `python -O -m
pytest` -- none depends on `match=` message text under `pytest.raises`
(verified via `exc.value.args` instead, since `-O` strips the assert-based
message check `pytest.raises(match=...)` relies on) and none depends on a
bare `assert` inside the reader module itself.
"""
import dataclasses
from contextlib import contextmanager
from decimal import Inexact, localcontext

import pytest

from polymarket_scanner.v11 import official_gamma_comparator_reader as comparator_module
from polymarket_scanner.v11.evidence import EvidenceError, EvidenceStore, canonical, digest
from polymarket_scanner.v11.label_attestation import GAMMA_LABEL_PROVIDER
from polymarket_scanner.v11.official_gamma_comparator_reader import (
    PERMANENT_HOLDS, GammaComparatorDiagnostic, read_gamma_comparator,
)
from polymarket_scanner.v11.rules import RuleFingerprint, fingerprint_event

from test_weather_final_gpt6_exact_replays import _event


@contextmanager
def expect_refusal(code):
    with pytest.raises(EvidenceError) as exc:
        yield
    if exc.value.args != (code,):
        raise AssertionError(f"expected refusal {code!r}, got {exc.value.args!r}")


@pytest.fixture
def rig(tmp_path):
    tmp_path.chmod(0o700)
    now = [1_700_000_000.0]
    store = EvidenceStore(tmp_path / "evidence.sqlite", "V11_PAPER", clock=lambda: now[0])
    return store, now


def _rule(*, station="KATL", family="high", labels=None, eid="event-1"):
    event = _event(station=station, family=family, labels=labels, eid=eid)
    return fingerprint_event(event, station_timezone="America/New_York", metadata_fingerprint="a" * 64)


def _fake_rule(payload):
    return RuleFingerprint(canonical(payload), digest(payload), "a" * 64)


def _frontier(store):
    return store.pin_read_view()["through_seq"]


def _closed_market(market_id, yes_token, no_token, *, condition_id, winner=True, disputed=False):
    if disputed:
        prices = ["0.5", "0.5"]
    else:
        prices = ["1", "0"] if winner else ["0", "1"]
    return {"id": market_id, "closed": True, "conditionId": condition_id,
            "clobTokenIds": [yes_token, no_token], "outcomePrices": prices,
            "outcomes": ["Yes", "No"]}


def _write_payout(store, *, event_id, bucket, record_id, winner=True, disputed=False,
                   provider="GAMMA_CLOSED_MARKET", evidence_class="PUBLIC_OBSERVED", source_identity=None):
    market = _closed_market(bucket["market_id"], bucket["yes_token"], bucket["no_token"],
                             condition_id=bucket["condition_id"], winner=winner, disputed=disputed)
    return store.capture(record_id, event_id=event_id, kind="RULES", provider=provider,
                          source_identity=source_identity or ("market:" + bucket["market_id"]),
                          revision=record_id, evidence_class=evidence_class, payload={"response": market})


def _write_label(store, *, event_id, bucket, value, record_id, knowable_at, token_id=None,
                  source_record=None, source_identity=None):
    """Happy-path LABEL fixture: auto-creates a genuine, consistent RULES
    disclosure as the label's cited causal source, matching the receipt
    causality `_process_label_row` now requires (finding F2)."""
    if source_record is None:
        source_record = _write_payout(store, event_id=event_id, bucket=bucket,
                                       winner=(value == 1), record_id=record_id + "-src")
    target = dict(market_id=bucket["market_id"], condition_id=bucket["condition_id"],
                  token_id=token_id or bucket["yes_token"], side="YES")
    payload = dict(target_identity=target, value=value, knowable_at=knowable_at,
                   source_capture_id=source_record["id"], source_capture_sha256=source_record["sha256"])
    return store.capture(record_id, event_id=event_id, kind="LABEL", provider=GAMMA_LABEL_PROVIDER,
                          source_identity=source_identity or bucket["market_id"], revision=record_id,
                          evidence_class="SYNTHETIC", payload=payload)


# --------------------------------------------------------------------------
# 1. Full partition: exactly one winner, no unresolved/disputed holds.
# --------------------------------------------------------------------------

def test_full_partition_resolves_single_winner(rig):
    store, now = rig
    rule = _rule(eid="event-full")
    event_id = rule.payload["event_id"]
    buckets = rule.payload["partition"]
    for i, bucket in enumerate(buckets):
        _write_payout(store, event_id=event_id, bucket=bucket, winner=(i == 0), record_id=f"full-{i}")
        now[0] += 1.
    through_seq = _frontier(store)

    result = read_gamma_comparator(store=store, event_id=event_id, rule=rule, through_seq=through_seq)

    if result.version != "alpha_v11_official_gamma_comparator_reader_v3":
        raise AssertionError(result.version)
    assert isinstance(result, GammaComparatorDiagnostic)
    assert result.winner_market_id == buckets[0]["market_id"]
    assert len(result.candidates) == len(buckets)
    assert set(PERMANENT_HOLDS) <= set(result.holds)
    assert "PARTITION_BUCKET_UNRESOLVED" not in result.holds
    assert "DISPUTED_PAYOUT_PRESENT" not in result.holds
    assert "NO_WINNER_AMONG_RESOLVED_BUCKETS" not in result.holds
    assert result.station == rule.payload["station"]
    assert result.timezone == rule.payload["timezone"]
    assert result.target_date == rule.payload["target_date"]
    assert result.unit == rule.payload["unit"]
    assert result.through_seq == through_seq
    assert result.decision_cutoff is None
    assert all(getattr(result, name) is False for name in (
        "independent_label_attestation", "settlement_authority", "calibration_authority",
        "financial_authority", "automatic_promotion", "qualified"))


# --------------------------------------------------------------------------
# 2. Missing winner: unresolved buckets, and a fully-resolved-but-all-losing set.
# --------------------------------------------------------------------------

def test_partial_partition_flags_unresolved_bucket(rig):
    store, now = rig
    rule = _rule(eid="event-partial")
    event_id = rule.payload["event_id"]
    buckets = rule.payload["partition"]
    _write_payout(store, event_id=event_id, bucket=buckets[0], winner=True, record_id="only")
    through_seq = _frontier(store)

    result = read_gamma_comparator(store=store, event_id=event_id, rule=rule, through_seq=through_seq)
    assert result.winner_market_id == buckets[0]["market_id"]
    assert "PARTITION_BUCKET_UNRESOLVED" in result.holds


def test_no_closed_markets_leaves_winner_unresolved(rig):
    store, now = rig
    rule = _rule(eid="event-none")
    event_id = rule.payload["event_id"]
    bucket = rule.payload["partition"][0]
    market = _closed_market(bucket["market_id"], bucket["yes_token"], bucket["no_token"],
                             condition_id=bucket["condition_id"])
    market["closed"] = False
    store.capture("open-market", event_id=event_id, kind="RULES", provider="GAMMA_CLOSED_MARKET",
                  source_identity="market:" + bucket["market_id"], revision="r1",
                  evidence_class="PUBLIC_OBSERVED", payload={"response": market})
    through_seq = _frontier(store)

    result = read_gamma_comparator(store=store, event_id=event_id, rule=rule, through_seq=through_seq)
    assert result.winner_market_id is None
    assert result.candidates == ()
    assert "PARTITION_BUCKET_UNRESOLVED" in result.holds
    assert "NO_WINNER_AMONG_RESOLVED_BUCKETS" not in result.holds


# --------------------------------------------------------------------------
# 3. Duplicate/ambiguous winners.
# --------------------------------------------------------------------------

def test_ambiguous_duplicate_winners_are_refused(rig):
    store, now = rig
    rule = _rule(eid="event-ambiguous")
    event_id = rule.payload["event_id"]
    buckets = rule.payload["partition"]
    _write_payout(store, event_id=event_id, bucket=buckets[0], winner=True, record_id="w0")
    now[0] += 1.
    _write_payout(store, event_id=event_id, bucket=buckets[1], winner=True, record_id="w1")
    through_seq = _frontier(store)

    with expect_refusal("GAMMA_COMPARATOR_READER_AMBIGUOUS_WINNER"):
        read_gamma_comparator(store=store, event_id=event_id, rule=rule, through_seq=through_seq)


# --------------------------------------------------------------------------
# 4. Conflicting winners for the same market.
# --------------------------------------------------------------------------

def test_conflicting_payout_values_for_same_market_are_refused(rig):
    store, now = rig
    rule = _rule(eid="event-conflict")
    event_id = rule.payload["event_id"]
    bucket = rule.payload["partition"][0]
    _write_payout(store, event_id=event_id, bucket=bucket, winner=True, record_id="c1")
    now[0] += 1.
    _write_payout(store, event_id=event_id, bucket=bucket, winner=False, record_id="c2",
                  provider="GAMMA_CLOSED_MARKET_EXACT_TOKEN_PAYOUT")
    through_seq = _frontier(store)

    with expect_refusal("GAMMA_COMPARATOR_READER_CONFLICTING_PAYOUT_FOR_MARKET"):
        read_gamma_comparator(store=store, event_id=event_id, rule=rule, through_seq=through_seq)


def test_conflicting_rules_and_label_values_are_refused(rig):
    store, now = rig
    rule = _rule(eid="event-conflict-mixed")
    event_id = rule.payload["event_id"]
    bucket = rule.payload["partition"][0]
    _write_payout(store, event_id=event_id, bucket=bucket, winner=True, record_id="mixed-rules")
    now[0] += 1.
    _write_label(store, event_id=event_id, bucket=bucket, value=0, record_id="mixed-label", knowable_at=now[0])
    through_seq = _frontier(store)

    with expect_refusal("GAMMA_COMPARATOR_READER_CONFLICTING_PAYOUT_FOR_MARKET"):
        read_gamma_comparator(store=store, event_id=event_id, rule=rule, through_seq=through_seq)


# --------------------------------------------------------------------------
# 5. Late receipts: a row appended after the pinned frontier is excluded.
# --------------------------------------------------------------------------

def test_late_receipt_after_pinned_frontier_is_excluded(rig):
    store, now = rig
    rule = _rule(eid="event-late")
    event_id = rule.payload["event_id"]
    buckets = rule.payload["partition"]
    _write_payout(store, event_id=event_id, bucket=buckets[0], winner=True, record_id="early")
    through_seq = _frontier(store)
    now[0] += 1.
    # Written after the pin: if causality were not preserved, this would turn
    # the clean single-winner result below into an AMBIGUOUS_WINNER refusal.
    late = _write_payout(store, event_id=event_id, bucket=buckets[1], winner=True, record_id="late")

    result = read_gamma_comparator(store=store, event_id=event_id, rule=rule, through_seq=through_seq)
    assert result.winner_market_id == buckets[0]["market_id"]
    assert all(c["id"] != "late" for c in result.candidates)
    assert store.get(late["id"])["id"] == "late"


# --------------------------------------------------------------------------
# 6. Tampered lineage: market/label identity disagrees with the rule's own
#    committed partition even though the market_id string matches.
# --------------------------------------------------------------------------

def test_tampered_condition_id_in_rules_payout_is_refused(rig):
    store, now = rig
    rule = _rule(eid="event-tamper-rules")
    event_id = rule.payload["event_id"]
    bucket = rule.payload["partition"][0]
    tampered = _closed_market(bucket["market_id"], bucket["yes_token"], bucket["no_token"],
                               condition_id="not-" + bucket["condition_id"])
    store.capture("tampered-rules", event_id=event_id, kind="RULES", provider="GAMMA_CLOSED_MARKET",
                  source_identity="market:" + bucket["market_id"], revision="r1",
                  evidence_class="PUBLIC_OBSERVED", payload={"response": tampered})
    through_seq = _frontier(store)

    with expect_refusal("GAMMA_COMPARATOR_READER_TAMPERED_LINEAGE"):
        read_gamma_comparator(store=store, event_id=event_id, rule=rule, through_seq=through_seq)


def test_tampered_label_target_identity_is_refused(rig):
    store, now = rig
    rule = _rule(eid="event-tamper-label")
    event_id = rule.payload["event_id"]
    bucket = rule.payload["partition"][0]
    _write_label(store, event_id=event_id, bucket=bucket, value=1, record_id="tampered-label",
                 knowable_at=now[0], token_id="forged-token")
    through_seq = _frontier(store)

    with expect_refusal("GAMMA_COMPARATOR_READER_TAMPERED_LINEAGE"):
        read_gamma_comparator(store=store, event_id=event_id, rule=rule, through_seq=through_seq)


# --------------------------------------------------------------------------
# 7. Malformed records.
# --------------------------------------------------------------------------

def test_malformed_closed_market_payout_vector_is_refused(rig):
    store, now = rig
    rule = _rule(eid="event-malformed-rules")
    event_id = rule.payload["event_id"]
    bucket = rule.payload["partition"][0]
    malformed = {"id": bucket["market_id"], "closed": True, "conditionId": bucket["condition_id"],
                 "clobTokenIds": [bucket["yes_token"], bucket["no_token"]], "outcomePrices": ["1"]}
    store.capture("malformed-rules", event_id=event_id, kind="RULES", provider="GAMMA_CLOSED_MARKET",
                  source_identity="market:" + bucket["market_id"], revision="r1",
                  evidence_class="PUBLIC_OBSERVED", payload={"response": malformed})
    through_seq = _frontier(store)

    with expect_refusal("GAMMA_COMPARATOR_READER_MALFORMED_PAYOUT_RECORD"):
        read_gamma_comparator(store=store, event_id=event_id, rule=rule, through_seq=through_seq)


def test_malformed_label_value_is_refused(rig):
    store, now = rig
    rule = _rule(eid="event-malformed-label")
    event_id = rule.payload["event_id"]
    bucket = rule.payload["partition"][0]
    _write_label(store, event_id=event_id, bucket=bucket, value=2, record_id="malformed-label", knowable_at=now[0])
    through_seq = _frontier(store)

    with expect_refusal("GAMMA_COMPARATOR_READER_MALFORMED_PAYOUT_RECORD"):
        read_gamma_comparator(store=store, event_id=event_id, rule=rule, through_seq=through_seq)


# --------------------------------------------------------------------------
# 8. Disputed/partial payout: held, never a winner.
# --------------------------------------------------------------------------

def test_disputed_payout_is_held_not_claimed_as_winner(rig):
    store, now = rig
    rule = _rule(eid="event-disputed")
    event_id = rule.payload["event_id"]
    bucket = rule.payload["partition"][0]
    _write_payout(store, event_id=event_id, bucket=bucket, disputed=True, record_id="disputed-1")
    through_seq = _frontier(store)

    result = read_gamma_comparator(store=store, event_id=event_id, rule=rule, through_seq=through_seq)
    assert result.winner_market_id is None
    assert "DISPUTED_PAYOUT_PRESENT" in result.holds
    assert "NO_WINNER_AMONG_RESOLVED_BUCKETS" in result.holds


# --------------------------------------------------------------------------
# 9. Bounded-scan exhaustion: row cap and depth cap, both non-partial.
# --------------------------------------------------------------------------

def test_payout_scan_row_bound_reached_is_incomplete_not_truncated(rig, monkeypatch):
    store, now = rig
    rule = _rule(eid="event-bound")
    event_id = rule.payload["event_id"]
    bucket = rule.payload["partition"][0]
    _write_payout(store, event_id=event_id, bucket=bucket, winner=True, record_id="bound-1")
    through_seq = _frontier(store)
    monkeypatch.setattr(comparator_module, "_SCAN_CAP", 0)

    result = read_gamma_comparator(store=store, event_id=event_id, rule=rule, through_seq=through_seq)
    assert "PAYOUT_SCAN_INCOMPLETE" in result.holds
    assert result.candidates == ()
    assert result.winner_market_id is None
    # A truncated scan proves nothing about postdecision disclosure either --
    # the permanent limitation hold must not be dropped just because the
    # scan itself was cut short (finding F5).
    assert "POSTDECISION_DISCLOSURE_UNKNOWN_NO_DECISION_CUTOFF_SUPPLIED" in result.holds


def test_gamma_depth_limit_marks_scan_incomplete(rig):
    store, now = rig
    rule = _rule(eid="event-deep")
    event_id = rule.payload["event_id"]
    bucket = rule.payload["partition"][0]
    market = _closed_market(bucket["market_id"], bucket["yes_token"], bucket["no_token"],
                             condition_id=bucket["condition_id"])
    deep = {"response": {"response": {"event": {"id": event_id, "markets": [market]}}}}
    store.capture("deep", event_id=event_id, kind="RULES", provider="GAMMA_EVENT",
                  source_identity="event:" + event_id, revision="r1",
                  evidence_class="SYNTHETIC", payload=deep)
    through_seq = _frontier(store)

    result = read_gamma_comparator(store=store, event_id=event_id, rule=rule, through_seq=through_seq)
    assert result.candidates == ()
    assert "PAYOUT_SCAN_INCOMPLETE" in result.holds
    assert set(PERMANENT_HOLDS) <= set(result.holds)
    assert "POSTDECISION_DISCLOSURE_UNKNOWN_NO_DECISION_CUTOFF_SUPPLIED" in result.holds


# --------------------------------------------------------------------------
# 10. Provider-agnostic traversal and LABEL-based winner resolution.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("provider,payload_for,event_bound", [
    ("GAMMA_EVENT", lambda eid, m: {"event": {"id": eid, "markets": [m]}}, True),
    ("GAMMA_EVENT_LIST", lambda eid, m: {"response": [{"id": eid, "markets": [m]}]}, True),
    ("GAMMA_MARKET", lambda eid, m: {"response": m}, False),
    ("GAMMA_CLOSED_MARKET_EXACT_TOKEN_PAYOUT", lambda eid, m: {"response": m}, False),
])
def test_winner_detected_from_any_gamma_provider_shape(rig, provider, payload_for, event_bound):
    store, now = rig
    rule = _rule(eid="event-anyprov-" + provider.lower())
    event_id = rule.payload["event_id"]
    bucket = rule.payload["partition"][0]
    market = _closed_market(bucket["market_id"], bucket["yes_token"], bucket["no_token"],
                             condition_id=bucket["condition_id"])
    # Only a market actually reached through a validated event wrapper may
    # carry an event-level source_identity claim (finding F1); a bare market
    # under e.g. "response" with no such wrapper must name itself instead.
    source_identity = "event:" + event_id if event_bound else "market:" + bucket["market_id"]
    store.capture("anyprov", event_id=event_id, kind="RULES", provider=provider,
                  source_identity=source_identity, revision="r1",
                  evidence_class="PUBLIC_OBSERVED", payload=payload_for(event_id, market))
    through_seq = _frontier(store)

    result = read_gamma_comparator(store=store, event_id=event_id, rule=rule, through_seq=through_seq)
    assert result.winner_market_id == bucket["market_id"]


def test_event_identity_claim_on_bare_market_without_wrapper_is_refused(rig):
    """A GAMMA_MARKET row with only a bare `response` market and no event
    wrapper cannot smuggle in an event-level source_identity claim the
    payload never structurally proves (finding F1)."""
    store, now = rig
    rule = _rule(eid="event-f1-bareevent")
    event_id = rule.payload["event_id"]
    bucket = rule.payload["partition"][0]
    market = _closed_market(bucket["market_id"], bucket["yes_token"], bucket["no_token"],
                             condition_id=bucket["condition_id"])
    store.capture("bare-market-event-claim", event_id=event_id, kind="RULES", provider="GAMMA_MARKET",
                  source_identity="event:" + event_id, revision="r1",
                  evidence_class="PUBLIC_OBSERVED", payload={"response": market})
    through_seq = _frontier(store)

    with expect_refusal("GAMMA_COMPARATOR_READER_TAMPERED_LINEAGE"):
        read_gamma_comparator(store=store, event_id=event_id, rule=rule, through_seq=through_seq)


def test_events_envelope_market_is_traversed_and_bound(rig):
    """A `markets` list nested under an `events` list member (as opposed to
    a bare `event` wrapper) must actually be walked, not merely
    identity-checked and silently dropped -- the review's exact probe
    (finding F1 residual)."""
    store, now = rig
    rule = _rule(eid="event-f1-eventsenvelope")
    event_id = rule.payload["event_id"]
    bucket = rule.payload["partition"][0]
    market = _closed_market(bucket["market_id"], bucket["yes_token"], bucket["no_token"],
                             condition_id=bucket["condition_id"])
    payload = {"response": {"events": [{"id": event_id, "markets": [market]}]}}
    store.capture("events-envelope", event_id=event_id, kind="RULES", provider="GAMMA_EVENT_LIST",
                  source_identity="event:" + event_id, revision="r1",
                  evidence_class="PUBLIC_OBSERVED", payload=payload)
    through_seq = _frontier(store)

    result = read_gamma_comparator(store=store, event_id=event_id, rule=rule, through_seq=through_seq)
    assert result.winner_market_id == bucket["market_id"]
    assert len(result.candidates) == 1


def test_events_envelope_contradicting_member_id_is_refused(rig):
    """A member of an `events` list naming a different event id than the
    caller's own `event_id` contradicts the row's own content and must
    refuse, not skip (finding F1)."""
    store, now = rig
    rule = _rule(eid="event-f1-eventsenvelope-badid")
    event_id = rule.payload["event_id"]
    bucket = rule.payload["partition"][0]
    market = _closed_market(bucket["market_id"], bucket["yes_token"], bucket["no_token"],
                             condition_id=bucket["condition_id"])
    payload = {"response": {"events": [{"id": "some-other-event", "markets": [market]}]}}
    store.capture("events-envelope-badid", event_id=event_id, kind="RULES", provider="GAMMA_EVENT_LIST",
                  source_identity="event:" + event_id, revision="r1",
                  evidence_class="PUBLIC_OBSERVED", payload=payload)
    through_seq = _frontier(store)

    with expect_refusal("GAMMA_COMPARATOR_READER_TAMPERED_LINEAGE"):
        read_gamma_comparator(store=store, event_id=event_id, rule=rule, through_seq=through_seq)


def test_events_envelope_non_dict_member_is_refused(rig):
    """A non-dict member of an `events` list is the same self-contradiction
    refusal as a mismatched id, not a silent skip (finding F1)."""
    store, now = rig
    rule = _rule(eid="event-f1-eventsenvelope-baddict")
    event_id = rule.payload["event_id"]
    payload = {"response": {"events": ["not-a-dict"]}}
    store.capture("events-envelope-baddict", event_id=event_id, kind="RULES", provider="GAMMA_EVENT_LIST",
                  source_identity="event:" + event_id, revision="r1",
                  evidence_class="PUBLIC_OBSERVED", payload=payload)
    through_seq = _frontier(store)

    with expect_refusal("GAMMA_COMPARATOR_READER_TAMPERED_LINEAGE"):
        read_gamma_comparator(store=store, event_id=event_id, rule=rule, through_seq=through_seq)


@pytest.mark.parametrize("nested", [
    [{"id": "wrong-event", "markets": []}],
    "malformed",
    [{"id": "event-f1-mixed", "markets": []}],
])
def test_events_claim_beside_markets_is_refused(rig, nested):
    store, _ = rig
    rule = _rule(eid="event-f1-mixed")
    bucket = rule.payload["partition"][0]
    market = _closed_market(bucket["market_id"], bucket["yes_token"], bucket["no_token"],
                            condition_id=bucket["condition_id"])
    payload = {"events": [{"id": "event-f1-mixed", "markets": [market], "events": nested}]}
    store.capture("mixed-envelope", event_id="event-f1-mixed", kind="RULES",
                  provider="GAMMA_EVENT_LIST", source_identity="event:event-f1-mixed",
                  revision="r1", evidence_class="SYNTHETIC", payload=payload)
    with expect_refusal("GAMMA_COMPARATOR_READER_TAMPERED_LINEAGE"):
        read_gamma_comparator(store=store, event_id="event-f1-mixed", rule=rule,
                              through_seq=_frontier(store))


def test_label_record_resolves_winner(rig):
    store, now = rig
    rule = _rule(eid="event-label")
    event_id = rule.payload["event_id"]
    bucket = rule.payload["partition"][0]
    label = _write_label(store, event_id=event_id, bucket=bucket, value=1, record_id="label-1", knowable_at=now[0])
    through_seq = _frontier(store)

    result = read_gamma_comparator(store=store, event_id=event_id, rule=rule, through_seq=through_seq)
    assert result.winner_market_id == bucket["market_id"]
    assert any(c["id"] == label["id"] for c in result.candidates)


def test_label_knowable_at_before_source_disclosure_is_refused(rig):
    """A LABEL cannot declare a fact knowable before the very source it
    cites for that fact ever disclosed it, even though the label's own
    capture happens later (finding F2)."""
    store, now = rig
    rule = _rule(eid="event-f2-earlyknowable")
    event_id = rule.payload["event_id"]
    bucket = rule.payload["partition"][0]
    source = _write_payout(store, event_id=event_id, bucket=bucket, winner=True, record_id="src-earlyknowable")
    now[0] += 10.
    target = dict(market_id=bucket["market_id"], condition_id=bucket["condition_id"],
                  token_id=bucket["yes_token"], side="YES")
    payload = dict(target_identity=target, value=1, knowable_at=source["body"]["recorded_at"] - 1.,
                   source_capture_id=source["id"], source_capture_sha256=source["sha256"])
    store.capture("early-knowable-label", event_id=event_id, kind="LABEL", provider=GAMMA_LABEL_PROVIDER,
                  source_identity=bucket["market_id"], revision="r1",
                  evidence_class="SYNTHETIC", payload=payload)
    through_seq = _frontier(store)

    with expect_refusal("GAMMA_COMPARATOR_READER_TAMPERED_LINEAGE"):
        read_gamma_comparator(store=store, event_id=event_id, rule=rule, through_seq=through_seq)


def test_label_candidate_recorded_at_is_actual_receipt_not_knowable_at(rig):
    """The reported candidate recorded_at must be the label's own actual
    retention time, never an earlier self-declared knowable_at -- an earlier
    self-declared time must not be able to mask a later genuine receipt for
    a decision-cutoff comparison (finding F5)."""
    store, now = rig
    rule = _rule(eid="event-label-clamp")
    event_id = rule.payload["event_id"]
    bucket = rule.payload["partition"][0]
    source = _write_payout(store, event_id=event_id, bucket=bucket, winner=True, record_id="src-clamp")
    now[0] += 10.
    knowable_at = source["body"]["recorded_at"] + 2.
    label = _write_label(store, event_id=event_id, bucket=bucket, value=1, record_id="label-clamp",
                         knowable_at=knowable_at, source_record=source)
    through_seq = _frontier(store)

    result = read_gamma_comparator(store=store, event_id=event_id, rule=rule, through_seq=through_seq)
    entry = next(c for c in result.candidates if c["id"] == label["id"])
    assert entry["recorded_at"] == label["body"]["recorded_at"]
    assert entry["recorded_at"] != knowable_at


# --------------------------------------------------------------------------
# 11. No writer method or network access is ever used.
# --------------------------------------------------------------------------

def test_reader_never_writes_or_touches_the_network(rig, monkeypatch):
    store, now = rig
    rule = _rule(eid="event-spy")
    event_id = rule.payload["event_id"]
    bucket = rule.payload["partition"][0]
    _write_payout(store, event_id=event_id, bucket=bucket, winner=True, record_id="spy-1")
    through_seq = _frontier(store)

    def boom(*a, **kw):
        raise AssertionError("reader must not call store writer methods")

    for name in ("audit", "capture", "decision", "safety_audit", "funnel", "source_result"):
        monkeypatch.setattr(store, name, boom)

    import socket

    def boom_connect(*a, **kw):
        raise AssertionError("reader must not touch the network")

    monkeypatch.setattr(socket.socket, "connect", boom_connect)

    result = read_gamma_comparator(store=store, event_id=event_id, rule=rule, through_seq=through_seq)
    assert result.winner_market_id == bucket["market_id"]


# --------------------------------------------------------------------------
# 12. Input-shape refusals.
# --------------------------------------------------------------------------

def test_non_rule_fingerprint_is_refused(rig):
    store, now = rig
    with expect_refusal("GAMMA_COMPARATOR_READER_RULE_TYPE_INVALID"):
        read_gamma_comparator(store=store, event_id="evt-z", rule={"not": "a rule"}, through_seq=0)


@pytest.mark.parametrize("bad_through_seq", [-1, 1.5, "0", None])
def test_through_seq_invalid_type_is_refused(rig, bad_through_seq):
    store, now = rig
    rule = _rule(eid="event-seq")
    with expect_refusal("GAMMA_COMPARATOR_READER_THROUGH_SEQ_INVALID"):
        read_gamma_comparator(store=store, event_id=rule.payload["event_id"], rule=rule,
                               through_seq=bad_through_seq)


def test_event_id_mismatch_is_refused(rig):
    store, now = rig
    rule = _rule(eid="event-real")
    with expect_refusal("GAMMA_COMPARATOR_READER_RULE_EVENT_MISMATCH"):
        read_gamma_comparator(store=store, event_id="event-other", rule=rule, through_seq=0)


def test_rule_digest_mismatch_raises_bare_integrity_error(rig):
    store, now = rig
    rule = _rule(eid="event-integrity")
    tampered_json = rule.canonical_json.replace('"KATL"', '"KATZ"')
    assert tampered_json != rule.canonical_json
    tampered = RuleFingerprint(tampered_json, rule.sha256, rule.source_event_sha256)
    with expect_refusal("RULE_FINGERPRINT_INTEGRITY"):
        read_gamma_comparator(store=store, event_id=rule.payload["event_id"], rule=tampered, through_seq=0)


def test_rule_partition_shape_is_refused(rig):
    store, now = rig
    payload = dict(event_id="evt-partition", station="KATL", timezone="America/New_York",
                   target_date="2026-10-05", unit="F", statistic="DAILY_HIGHEST_TEMP",
                   precision_rounding="WHOLE_DEGREE_F", partition=[{"market_id": "m0"}])
    rule = _fake_rule(payload)
    with expect_refusal("GAMMA_COMPARATOR_READER_RULE_PARTITION_INVALID"):
        read_gamma_comparator(store=store, event_id="evt-partition", rule=rule, through_seq=0)


def test_rule_binding_fields_missing_is_refused(rig):
    store, now = rig
    payload = dict(event_id="evt-binding", station="KATL", target_date="2026-10-05",
                   unit="F", statistic="DAILY_HIGHEST_TEMP", precision_rounding="WHOLE_DEGREE_F",
                   partition=[{"market_id": "m0", "condition_id": "c0", "yes_token": "t0y", "no_token": "t0n"},
                              {"market_id": "m1", "condition_id": "c1", "yes_token": "t1y", "no_token": "t1n"}])
    rule = _fake_rule(payload)
    with expect_refusal("GAMMA_COMPARATOR_READER_RULE_BINDING_FIELDS_INVALID"):
        read_gamma_comparator(store=store, event_id="evt-binding", rule=rule, through_seq=0)


# --------------------------------------------------------------------------
# 13. holds/authority/winner_market_id can never be forced.
# --------------------------------------------------------------------------

def test_diagnostic_fields_cannot_be_forced(rig):
    store, now = rig
    rule = _rule(eid="event-forge")
    event_id = rule.payload["event_id"]
    bucket = rule.payload["partition"][0]
    _write_payout(store, event_id=event_id, bucket=bucket, winner=True, record_id="forge-1")
    through_seq = _frontier(store)

    result = read_gamma_comparator(store=store, event_id=event_id, rule=rule, through_seq=through_seq)
    assert result.holds

    for name in ("independent_label_attestation", "settlement_authority", "calibration_authority",
                 "financial_authority", "automatic_promotion", "qualified"):
        with pytest.raises(ValueError):
            dataclasses.replace(result, **{name: True})

    with pytest.raises(ValueError):
        dataclasses.replace(result, holds=())
    with pytest.raises(ValueError):
        dataclasses.replace(result, holds=("NOT_A_REAL_HOLD",) + PERMANENT_HOLDS)
    with pytest.raises(ValueError):
        dataclasses.replace(result, winner_market_id="not-a-real-market-id")

    with pytest.raises(dataclasses.FrozenInstanceError):
        result.holds = ()


# --------------------------------------------------------------------------
# 14. F1: Gamma source and event lineage provenance.
# --------------------------------------------------------------------------

def test_unrelated_provider_rules_row_is_not_walked(rig):
    store, now = rig
    rule = _rule(eid="event-f1-unrelated")
    event_id = rule.payload["event_id"]
    bucket = rule.payload["partition"][0]
    market = _closed_market(bucket["market_id"], bucket["yes_token"], bucket["no_token"],
                             condition_id=bucket["condition_id"], winner=True)
    store.capture("unrelated", event_id=event_id, kind="RULES", provider="UNRELATED_SOURCE",
                  source_identity="market:" + bucket["market_id"], revision="r1",
                  evidence_class="PUBLIC_OBSERVED", payload={"response": market})
    through_seq = _frontier(store)

    result = read_gamma_comparator(store=store, event_id=event_id, rule=rule, through_seq=through_seq)
    assert result.winner_market_id is None
    assert result.candidates == ()


def test_contradictory_event_wrapper_id_is_refused(rig):
    store, now = rig
    rule = _rule(eid="event-f1-wrapper")
    event_id = rule.payload["event_id"]
    bucket = rule.payload["partition"][0]
    market = _closed_market(bucket["market_id"], bucket["yes_token"], bucket["no_token"],
                             condition_id=bucket["condition_id"], winner=True)
    payload = {"event": {"id": "a-different-event", "markets": [market]}}
    store.capture("wrong-event", event_id=event_id, kind="RULES", provider="GAMMA_EVENT",
                  source_identity="event:" + event_id, revision="r1",
                  evidence_class="PUBLIC_OBSERVED", payload=payload)
    through_seq = _frontier(store)

    with expect_refusal("GAMMA_COMPARATOR_READER_TAMPERED_LINEAGE"):
        read_gamma_comparator(store=store, event_id=event_id, rule=rule, through_seq=through_seq)


def test_source_identity_contradicts_matched_market_is_refused(rig):
    store, now = rig
    rule = _rule(eid="event-f1-identity")
    event_id = rule.payload["event_id"]
    bucket = rule.payload["partition"][0]
    market = _closed_market(bucket["market_id"], bucket["yes_token"], bucket["no_token"],
                             condition_id=bucket["condition_id"], winner=True)
    store.capture("wrong-identity", event_id=event_id, kind="RULES", provider="GAMMA_CLOSED_MARKET",
                  source_identity="a-different-market", revision="r1",
                  evidence_class="PUBLIC_OBSERVED", payload={"response": market})
    through_seq = _frontier(store)

    with expect_refusal("GAMMA_COMPARATOR_READER_TAMPERED_LINEAGE"):
        read_gamma_comparator(store=store, event_id=event_id, rule=rule, through_seq=through_seq)


def test_event_provider_without_event_binding_is_refused(rig):
    store, now = rig
    rule = _rule(eid="event-f1-unbound")
    event_id = rule.payload["event_id"]
    bucket = rule.payload["partition"][0]
    market = _closed_market(bucket["market_id"], bucket["yes_token"], bucket["no_token"],
                             condition_id=bucket["condition_id"], winner=True)
    store.capture("unbound-event", event_id=event_id, kind="RULES", provider="GAMMA_EVENT",
                  source_identity="event:" + event_id, revision="r1",
                  evidence_class="PUBLIC_OBSERVED", payload={"response": market})
    through_seq = _frontier(store)

    with expect_refusal("GAMMA_COMPARATOR_READER_TAMPERED_LINEAGE"):
        read_gamma_comparator(store=store, event_id=event_id, rule=rule, through_seq=through_seq)


# --------------------------------------------------------------------------
# 15. F2: LABEL source/receipt causality.
# --------------------------------------------------------------------------

def test_label_without_source_reference_is_refused(rig):
    store, now = rig
    rule = _rule(eid="event-f2-nosource")
    event_id = rule.payload["event_id"]
    bucket = rule.payload["partition"][0]
    target = dict(market_id=bucket["market_id"], condition_id=bucket["condition_id"],
                  token_id=bucket["yes_token"], side="YES")
    payload = dict(target_identity=target, value=1, knowable_at=now[0])
    store.capture("no-source-label", event_id=event_id, kind="LABEL", provider=GAMMA_LABEL_PROVIDER,
                  source_identity=bucket["market_id"], revision="r1",
                  evidence_class="SYNTHETIC", payload=payload)
    through_seq = _frontier(store)

    with expect_refusal("GAMMA_COMPARATOR_READER_LABEL_SOURCE_REFERENCE_MISSING"):
        read_gamma_comparator(store=store, event_id=event_id, rule=rule, through_seq=through_seq)


def test_label_source_identity_mismatch_is_refused(rig):
    store, now = rig
    rule = _rule(eid="event-f2-badidentity")
    event_id = rule.payload["event_id"]
    bucket = rule.payload["partition"][0]
    source = _write_payout(store, event_id=event_id, bucket=bucket, winner=True, record_id="src-badidentity")
    target = dict(market_id=bucket["market_id"], condition_id=bucket["condition_id"],
                  token_id=bucket["yes_token"], side="YES")
    payload = dict(target_identity=target, value=1, knowable_at=now[0],
                   source_capture_id=source["id"], source_capture_sha256=source["sha256"])
    store.capture("bad-identity-label", event_id=event_id, kind="LABEL", provider=GAMMA_LABEL_PROVIDER,
                  source_identity="an-unrelated-identity", revision="r1",
                  evidence_class="SYNTHETIC", payload=payload)
    through_seq = _frontier(store)

    with expect_refusal("GAMMA_COMPARATOR_READER_TAMPERED_LINEAGE"):
        read_gamma_comparator(store=store, event_id=event_id, rule=rule, through_seq=through_seq)


def test_label_boolean_value_is_refused(rig):
    store, now = rig
    rule = _rule(eid="event-f2-bool")
    event_id = rule.payload["event_id"]
    bucket = rule.payload["partition"][0]
    source = _write_payout(store, event_id=event_id, bucket=bucket, winner=True, record_id="src-bool")
    target = dict(market_id=bucket["market_id"], condition_id=bucket["condition_id"],
                  token_id=bucket["yes_token"], side="YES")
    payload = dict(target_identity=target, value=True,
                   source_capture_id=source["id"], source_capture_sha256=source["sha256"])
    store.capture("bool-label", event_id=event_id, kind="LABEL", provider=GAMMA_LABEL_PROVIDER,
                  source_identity=bucket["market_id"], revision="r1",
                  evidence_class="SYNTHETIC", payload=payload)
    through_seq = _frontier(store)

    with expect_refusal("GAMMA_COMPARATOR_READER_MALFORMED_PAYOUT_RECORD"):
        read_gamma_comparator(store=store, event_id=event_id, rule=rule, through_seq=through_seq)


def test_label_missing_knowable_at_is_refused(rig):
    store, now = rig
    rule = _rule(eid="event-f2-noknowable")
    event_id = rule.payload["event_id"]
    bucket = rule.payload["partition"][0]
    source = _write_payout(store, event_id=event_id, bucket=bucket, winner=True, record_id="src-noknowable")
    target = dict(market_id=bucket["market_id"], condition_id=bucket["condition_id"],
                  token_id=bucket["yes_token"], side="YES")
    payload = dict(target_identity=target, value=1,
                   source_capture_id=source["id"], source_capture_sha256=source["sha256"])
    store.capture("no-knowable-label", event_id=event_id, kind="LABEL", provider=GAMMA_LABEL_PROVIDER,
                  source_identity=bucket["market_id"], revision="r1",
                  evidence_class="SYNTHETIC", payload=payload)
    through_seq = _frontier(store)

    with expect_refusal("GAMMA_COMPARATOR_READER_MALFORMED_PAYOUT_RECORD"):
        read_gamma_comparator(store=store, event_id=event_id, rule=rule, through_seq=through_seq)


def test_label_knowable_at_after_available_is_refused(rig):
    store, now = rig
    rule = _rule(eid="event-f2-future-knowable")
    event_id = rule.payload["event_id"]
    bucket = rule.payload["partition"][0]
    source = _write_payout(store, event_id=event_id, bucket=bucket, winner=True, record_id="src-future")
    target = dict(market_id=bucket["market_id"], condition_id=bucket["condition_id"],
                  token_id=bucket["yes_token"], side="YES")
    payload = dict(target_identity=target, value=1, knowable_at=now[0] + 1000.,
                   source_capture_id=source["id"], source_capture_sha256=source["sha256"])
    store.capture("future-knowable-label", event_id=event_id, kind="LABEL", provider=GAMMA_LABEL_PROVIDER,
                  source_identity=bucket["market_id"], revision="r1",
                  evidence_class="SYNTHETIC", payload=payload)
    through_seq = _frontier(store)

    with expect_refusal("GAMMA_COMPARATOR_READER_TAMPERED_LINEAGE"):
        read_gamma_comparator(store=store, event_id=event_id, rule=rule, through_seq=through_seq)


def test_label_source_reference_hash_mismatch_is_refused(rig):
    store, now = rig
    rule = _rule(eid="event-f2-hashmismatch")
    event_id = rule.payload["event_id"]
    bucket = rule.payload["partition"][0]
    source = _write_payout(store, event_id=event_id, bucket=bucket, winner=True, record_id="src-hash")
    target = dict(market_id=bucket["market_id"], condition_id=bucket["condition_id"],
                  token_id=bucket["yes_token"], side="YES")
    payload = dict(target_identity=target, value=1, knowable_at=now[0],
                   source_capture_id=source["id"], source_capture_sha256="0" * 64)
    store.capture("hash-mismatch-label", event_id=event_id, kind="LABEL", provider=GAMMA_LABEL_PROVIDER,
                  source_identity=bucket["market_id"], revision="r1",
                  evidence_class="SYNTHETIC", payload=payload)
    through_seq = _frontier(store)

    with expect_refusal("GAMMA_COMPARATOR_READER_LABEL_SOURCE_REFERENCE_MISSING"):
        read_gamma_comparator(store=store, event_id=event_id, rule=rule, through_seq=through_seq)


def test_label_source_recorded_after_label_is_refused(rig):
    """The cited source must genuinely precede the label it backs -- a label
    cannot borrow causal cover from a disclosure that only arrives later."""
    store, now = rig
    rule = _rule(eid="event-f2-afterlabel")
    event_id = rule.payload["event_id"]
    bucket = rule.payload["partition"][0]
    market = _closed_market(bucket["market_id"], bucket["yes_token"], bucket["no_token"],
                             condition_id=bucket["condition_id"], winner=True)
    source_record_id = "src-after"
    predicted_body = {
        "provider": "GAMMA_CLOSED_MARKET", "source_identity": "market:" + bucket["market_id"],
        "revision": source_record_id, "payload": {"response": market},
        "observed_at": None, "issued_at": None, "published_at": None,
        "received_at": now[0], "evidence_class": "PUBLIC_OBSERVED", "source_kind": "RULES",
        "namespace": "V11_PAPER", "financial_authority": False, "record_id": source_record_id,
        "kind": "RULES", "event_id": event_id, "recorded_at": now[0], "available_at": now[0],
    }
    predicted_sha256 = digest(predicted_body)
    target = dict(market_id=bucket["market_id"], condition_id=bucket["condition_id"],
                  token_id=bucket["yes_token"], side="YES")
    payload = dict(target_identity=target, value=1, knowable_at=now[0],
                   source_capture_id=source_record_id, source_capture_sha256=predicted_sha256)
    store.capture("after-label", event_id=event_id, kind="LABEL", provider=GAMMA_LABEL_PROVIDER,
                  source_identity=bucket["market_id"], revision="r1",
                  evidence_class="SYNTHETIC", payload=payload)
    actual_source = store.capture(source_record_id, event_id=event_id, kind="RULES",
                                   provider="GAMMA_CLOSED_MARKET", source_identity="market:" + bucket["market_id"],
                                   revision=source_record_id, evidence_class="PUBLIC_OBSERVED",
                                   payload={"response": market})
    assert actual_source["sha256"] == predicted_sha256
    through_seq = _frontier(store)

    with expect_refusal("GAMMA_COMPARATOR_READER_TAMPERED_LINEAGE"):
        read_gamma_comparator(store=store, event_id=event_id, rule=rule, through_seq=through_seq)


def test_label_source_value_conflict_is_refused(rig):
    store, now = rig
    rule = _rule(eid="event-f2-valueconflict")
    event_id = rule.payload["event_id"]
    bucket = rule.payload["partition"][0]
    source = _write_payout(store, event_id=event_id, bucket=bucket, winner=False, record_id="src-conflict")
    target = dict(market_id=bucket["market_id"], condition_id=bucket["condition_id"],
                  token_id=bucket["yes_token"], side="YES")
    payload = dict(target_identity=target, value=1, knowable_at=now[0],
                   source_capture_id=source["id"], source_capture_sha256=source["sha256"])
    store.capture("value-conflict-label", event_id=event_id, kind="LABEL", provider=GAMMA_LABEL_PROVIDER,
                  source_identity=bucket["market_id"], revision="r1",
                  evidence_class="SYNTHETIC", payload=payload)
    through_seq = _frontier(store)

    with expect_refusal("GAMMA_COMPARATOR_READER_CONFLICTING_PAYOUT_FOR_MARKET"):
        read_gamma_comparator(store=store, event_id=event_id, rule=rule, through_seq=through_seq)


# --------------------------------------------------------------------------
# 16. F3: exact whole-vector payout binding.
# --------------------------------------------------------------------------

def _capture_prices(store, rule, prices):
    bucket = rule.payload["partition"][0]
    market = _closed_market(bucket["market_id"], bucket["yes_token"], bucket["no_token"],
                            condition_id=bucket["condition_id"])
    market["outcomePrices"] = prices
    store.capture("numeric-payout", event_id=rule.payload["event_id"], kind="RULES",
                  provider="GAMMA_CLOSED_MARKET", source_identity="market:" + bucket["market_id"],
                  revision="r1", evidence_class="SYNTHETIC", payload={"response": market})


def test_exact_near_one_payout_is_disputed_without_false_winner(rig):
    store, _ = rig
    rule = _rule(eid="event-f3-nearone")
    _capture_prices(store, rule, ["0.99999999999999999", "0.00000000000000001"])
    result = read_gamma_comparator(store=store, event_id="event-f3-nearone", rule=rule,
                                   through_seq=_frontier(store))
    if result.winner_market_id is not None or "DISPUTED_PAYOUT_PRESENT" not in result.holds:
        raise AssertionError(result)
    if result.candidates[0]["value"] != "0.99999999999999999":
        raise AssertionError(result.candidates)


def test_decimal_context_does_not_change_payout_classification(rig):
    store, _ = rig
    rule = _rule(eid="event-f3-context")
    _capture_prices(store, rule, ["0.9999", "0.0002"])
    for precision in (3, 28):
        with localcontext() as context:
            context.prec = precision
            with expect_refusal("GAMMA_COMPARATOR_READER_MALFORMED_PAYOUT_RECORD"):
                read_gamma_comparator(store=store, event_id="event-f3-context", rule=rule,
                                      through_seq=_frontier(store))


def test_valid_exact_pair_survives_hostile_decimal_context(rig):
    store, _ = rig
    rule = _rule(eid="event-f3-valid-context")
    _capture_prices(store, rule, ["0.9999", "0.0001"])
    for precision in (1, 3, 28):
        with localcontext() as context:
            context.prec = precision
            context.traps[Inexact] = True
            result = read_gamma_comparator(store=store, event_id="event-f3-valid-context",
                                           rule=rule, through_seq=_frontier(store))
        if result.winner_market_id is not None or "DISPUTED_PAYOUT_PRESENT" not in result.holds:
            raise AssertionError(result)
        if result.candidates[0]["value"] != "0.9999":
            raise AssertionError(result.candidates)


@pytest.mark.parametrize("prices", [
    ["NaN", "0"], ["sNaN", "0"], ["Infinity", "-Infinity"],
    ["1e999999999", "0"], ["1e-999999999", "1"],
    ["0." + "0" * 300 + "1", "1"],
])
def test_nonfinite_or_unbounded_decimal_payout_is_typed_refusal(rig, prices):
    store, _ = rig
    rule = _rule(eid="event-f3-numeric-invalid")
    _capture_prices(store, rule, prices)
    with expect_refusal("GAMMA_COMPARATOR_READER_MALFORMED_PAYOUT_RECORD"):
        read_gamma_comparator(store=store, event_id="event-f3-numeric-invalid", rule=rule,
                              through_seq=_frontier(store))


@pytest.mark.parametrize("prices,expected", [
    ("[0.99999999999999999,0.00000000000000001]", "0.99999999999999999"),
    (["0.99999999999999999", "0.00000000000000001"], "0.99999999999999999"),
    ("[1e-8,0.99999999]", "1E-8"),
])
def test_encoded_and_native_exact_fractional_vectors_remain_disputed(rig, prices, expected):
    store, _ = rig
    rule = _rule(eid="event-f3-exact-encoded")
    _capture_prices(store, rule, prices)
    result = read_gamma_comparator(store=store, event_id=rule.payload["event_id"], rule=rule,
                                   through_seq=_frontier(store))
    if result.winner_market_id is not None or "DISPUTED_PAYOUT_PRESENT" not in result.holds:
        raise AssertionError(result)
    if result.candidates[0]["value"] != expected:
        raise AssertionError(result.candidates)


@pytest.mark.parametrize("prices", [
    "[1.00000000000000001,0]", "[1,-1e-1000]",
    "[0.99999999999999999,1e-1000]", "[0." + "9" * 500000 + ",0]",
    ["1.00000000000000001", "0"], ["1", "-1e-1000"],
    ["0.99999999999999999", "1e-1000"], ["0." + "9" * 300, "0"],
])
def test_encoded_and_native_invalid_exact_vectors_refuse_before_winner(rig, prices):
    store, _ = rig
    rule = _rule(eid="event-f3-invalid-encoded")
    _capture_prices(store, rule, prices)
    if store.get("numeric-payout")["body"]["payload"]["response"]["outcomePrices"] != prices:
        raise AssertionError("retained price spelling changed")
    with expect_refusal("GAMMA_COMPARATOR_READER_MALFORMED_PAYOUT_RECORD"):
        read_gamma_comparator(store=store, event_id=rule.payload["event_id"], rule=rule,
                              through_seq=_frontier(store))


@pytest.mark.parametrize("prices", [[1.0, 0.0], [0.5, 0.5]])
def test_native_float_prices_refuse_when_original_lexemes_are_unavailable(rig, prices):
    store, _ = rig
    rule = _rule(eid="event-f3-native-float")
    _capture_prices(store, rule, prices)
    with expect_refusal("GAMMA_COMPARATOR_READER_MALFORMED_PAYOUT_RECORD"):
        read_gamma_comparator(store=store, event_id=rule.payload["event_id"], rule=rule,
                              through_seq=_frontier(store))


def test_native_integer_price_pair_stays_exact(rig):
    store, _ = rig
    rule = _rule(eid="event-f3-native-integer")
    _capture_prices(store, rule, [1, 0])
    result = read_gamma_comparator(store=store, event_id=rule.payload["event_id"], rule=rule,
                                   through_seq=_frontier(store))
    if result.winner_market_id != rule.payload["partition"][0]["market_id"]:
        raise AssertionError(result)


def test_encoded_out_of_range_price_refuses_with_complete_partition(rig):
    store, _ = rig
    rule = _rule(eid="event-f3-complete-invalid")
    for index, bucket in enumerate(rule.payload["partition"]):
        market = _closed_market(bucket["market_id"], bucket["yes_token"], bucket["no_token"],
                                condition_id=bucket["condition_id"], winner=(index == 0))
        if index == 0:
            market["outcomePrices"] = "[1.00000000000000001,0]"
        store.capture(f"complete-{index}", event_id=rule.payload["event_id"], kind="RULES",
                      provider="GAMMA_MARKET", source_identity="market:" + bucket["market_id"],
                      revision="r1", evidence_class="SYNTHETIC", payload={"response": market})
    with expect_refusal("GAMMA_COMPARATOR_READER_MALFORMED_PAYOUT_RECORD"):
        read_gamma_comparator(store=store, event_id=rule.payload["event_id"], rule=rule,
                              through_seq=_frontier(store), decision_cutoff=1_700_000_000.)


def test_candidate_decimal_spelling_ignores_ambient_capitals(rig):
    store, _ = rig
    rule = _rule(eid="event-f3-capitals")
    _capture_prices(store, rule, ["1e-8", ".99999999"])
    values = []
    for capitals in (0, 1):
        with localcontext() as context:
            context.capitals = capitals
            result = read_gamma_comparator(store=store, event_id=rule.payload["event_id"],
                                           rule=rule, through_seq=_frontier(store))
            values.append(result.candidates[0]["value"])
    if values != ["1E-8", "1E-8"]:
        raise AssertionError(values)


def test_wrong_no_token_in_payout_vector_is_refused(rig):
    store, now = rig
    rule = _rule(eid="event-f3-wrongno")
    event_id = rule.payload["event_id"]
    bucket = rule.payload["partition"][0]
    market = {"id": bucket["market_id"], "closed": True, "conditionId": bucket["condition_id"],
              "clobTokenIds": [bucket["yes_token"], "a-different-no-token"], "outcomePrices": ["1", "0"],
              "outcomes": ["Yes", "No"]}
    store.capture("wrong-no-token", event_id=event_id, kind="RULES", provider="GAMMA_CLOSED_MARKET",
                  source_identity="market:" + bucket["market_id"], revision="r1",
                  evidence_class="PUBLIC_OBSERVED", payload={"response": market})
    through_seq = _frontier(store)

    with expect_refusal("GAMMA_COMPARATOR_READER_MALFORMED_PAYOUT_RECORD"):
        read_gamma_comparator(store=store, event_id=event_id, rule=rule, through_seq=through_seq)


def test_inconsistent_payout_vector_both_winning_is_refused(rig):
    store, now = rig
    rule = _rule(eid="event-f3-bothwin")
    event_id = rule.payload["event_id"]
    bucket = rule.payload["partition"][0]
    market = {"id": bucket["market_id"], "closed": True, "conditionId": bucket["condition_id"],
              "clobTokenIds": [bucket["yes_token"], bucket["no_token"]], "outcomePrices": ["1", "1"],
              "outcomes": ["Yes", "No"]}
    store.capture("both-win", event_id=event_id, kind="RULES", provider="GAMMA_CLOSED_MARKET",
                  source_identity="market:" + bucket["market_id"], revision="r1",
                  evidence_class="PUBLIC_OBSERVED", payload={"response": market})
    through_seq = _frontier(store)

    with expect_refusal("GAMMA_COMPARATOR_READER_MALFORMED_PAYOUT_RECORD"):
        read_gamma_comparator(store=store, event_id=event_id, rule=rule, through_seq=through_seq)


def test_duplicate_token_in_payout_vector_is_refused(rig):
    store, now = rig
    rule = _rule(eid="event-f3-dupe")
    event_id = rule.payload["event_id"]
    bucket = rule.payload["partition"][0]
    market = {"id": bucket["market_id"], "closed": True, "conditionId": bucket["condition_id"],
              "clobTokenIds": [bucket["yes_token"], bucket["yes_token"]], "outcomePrices": ["1", "0"],
              "outcomes": ["Yes", "No"]}
    store.capture("dupe-token", event_id=event_id, kind="RULES", provider="GAMMA_CLOSED_MARKET",
                  source_identity="market:" + bucket["market_id"], revision="r1",
                  evidence_class="PUBLIC_OBSERVED", payload={"response": market})
    through_seq = _frontier(store)

    with expect_refusal("GAMMA_COMPARATOR_READER_MALFORMED_PAYOUT_RECORD"):
        read_gamma_comparator(store=store, event_id=event_id, rule=rule, through_seq=through_seq)


def test_reversed_outcomes_order_is_refused(rig):
    """A market whose own outcome labelling is reversed relative to its
    committed token order cannot be read as a YES winner merely because the
    first token's price is 1 -- the token vector alone binds which tokens
    settled, not what the first of them means."""
    store, now = rig
    rule = _rule(eid="event-f3-reversedoutcomes")
    event_id = rule.payload["event_id"]
    bucket = rule.payload["partition"][0]
    market = {"id": bucket["market_id"], "closed": True, "conditionId": bucket["condition_id"],
              "clobTokenIds": [bucket["yes_token"], bucket["no_token"]], "outcomePrices": ["1", "0"],
              "outcomes": ["No", "Yes"]}
    store.capture("reversed-outcomes", event_id=event_id, kind="RULES", provider="GAMMA_CLOSED_MARKET",
                  source_identity="market:" + bucket["market_id"], revision="r1",
                  evidence_class="PUBLIC_OBSERVED", payload={"response": market})
    through_seq = _frontier(store)

    with expect_refusal("GAMMA_COMPARATOR_READER_MALFORMED_PAYOUT_RECORD"):
        read_gamma_comparator(store=store, event_id=event_id, rule=rule, through_seq=through_seq)


# --------------------------------------------------------------------------
# 17. F4: rule semantic/token/timezone validation.
# --------------------------------------------------------------------------

def test_invalid_timezone_rule_is_refused(rig):
    store, now = rig
    payload = dict(event_id="evt-tz", station="KATL", timezone="Invalid/Zone",
                   target_date="2026-10-05", unit="F", statistic="DAILY_HIGHEST_TEMP",
                   precision_rounding="WHOLE_DEGREE_F",
                   partition=[{"market_id": "m0", "condition_id": "c0", "yes_token": "t0y", "no_token": "t0n"},
                              {"market_id": "m1", "condition_id": "c1", "yes_token": "t1y", "no_token": "t1n"}])
    rule = _fake_rule(payload)
    with expect_refusal("GAMMA_COMPARATOR_READER_RULE_BINDING_FIELDS_INVALID"):
        read_gamma_comparator(store=store, event_id="evt-tz", rule=rule, through_seq=0)


def test_duplicate_token_across_buckets_rule_is_refused(rig):
    store, now = rig
    payload = dict(event_id="evt-duptok", station="KATL", timezone="America/New_York",
                   target_date="2026-10-05", unit="F", statistic="DAILY_HIGHEST_TEMP",
                   precision_rounding="WHOLE_DEGREE_F",
                   partition=[{"market_id": "m0", "condition_id": "c0", "yes_token": "tshared", "no_token": "t0n"},
                              {"market_id": "m1", "condition_id": "c1", "yes_token": "tshared", "no_token": "t1n"}])
    rule = _fake_rule(payload)
    with expect_refusal("GAMMA_COMPARATOR_READER_RULE_PARTITION_INVALID"):
        read_gamma_comparator(store=store, event_id="evt-duptok", rule=rule, through_seq=0)


def test_yes_equals_no_token_rule_is_refused(rig):
    store, now = rig
    payload = dict(event_id="evt-sametok", station="KATL", timezone="America/New_York",
                   target_date="2026-10-05", unit="F", statistic="DAILY_HIGHEST_TEMP",
                   precision_rounding="WHOLE_DEGREE_F",
                   partition=[{"market_id": "m0", "condition_id": "c0", "yes_token": "tsame", "no_token": "tsame"},
                              {"market_id": "m1", "condition_id": "c1", "yes_token": "t1y", "no_token": "t1n"}])
    rule = _fake_rule(payload)
    with expect_refusal("GAMMA_COMPARATOR_READER_RULE_PARTITION_INVALID"):
        read_gamma_comparator(store=store, event_id="evt-sametok", rule=rule, through_seq=0)


def test_malformed_rule_semantic_fields_are_refused(rig):
    """A digest-consistent RuleFingerprint proves internal self-consistency,
    not that the strict compiler ever actually produced this semantic
    triple -- a bogus target_date/unit/statistic/precision_rounding must be
    refused rather than echoed as a diagnostic."""
    store, now = rig
    payload = dict(event_id="evt-badsemantics", station="KATL", timezone="America/New_York",
                   target_date="not-a-date", unit="BOGUS", statistic="BOGUS",
                   precision_rounding="BOGUS",
                   partition=[{"market_id": "m0", "condition_id": "c0", "yes_token": "t0y", "no_token": "t0n"},
                              {"market_id": "m1", "condition_id": "c1", "yes_token": "t1y", "no_token": "t1n"}])
    rule = _fake_rule(payload)
    with expect_refusal("GAMMA_COMPARATOR_READER_RULE_BINDING_FIELDS_INVALID"):
        read_gamma_comparator(store=store, event_id="evt-badsemantics", rule=rule, through_seq=0)


def test_hko_semantic_profile_is_refused(rig):
    """`weather_only_rules._hko_profile` always appends
    HKO_DECIMAL_BUCKET_MAPPING_UNPROVEN to its structural reasons whenever
    its precision would otherwise be proven, so exactly_one_outcome_proven
    is False for every HKO event with no exception, and
    `rules.fingerprint_event` therefore refuses to ever construct a
    RuleFingerprint for one. A digest-consistent ("C",
    "ABSOLUTE_DAILY_MAX_C"/"ABSOLUTE_DAILY_MIN_C", "ONE_DECIMAL_C") triple is
    not an attainable preimage under any real event -- only a
    self-consistent forgery -- and must be refused, not echoed as a
    diagnostic (finding F4)."""
    store, now = rig
    payload = dict(event_id="evt-hko", station="HKO", timezone="Asia/Hong_Kong",
                   target_date="2026-10-05", unit="C", statistic="ABSOLUTE_DAILY_MAX_C",
                   precision_rounding="ONE_DECIMAL_C",
                   partition=[{"market_id": "m0", "condition_id": "c0", "yes_token": "t0y", "no_token": "t0n"},
                              {"market_id": "m1", "condition_id": "c1", "yes_token": "t1y", "no_token": "t1n"}])
    rule = _fake_rule(payload)
    with expect_refusal("GAMMA_COMPARATOR_READER_RULE_BINDING_FIELDS_INVALID"):
        read_gamma_comparator(store=store, event_id="evt-hko", rule=rule, through_seq=0)


def test_cross_family_semantic_profile_on_genuine_fingerprint_is_refused(rig):
    """The review's exact probe: take a genuine, real-compiler NWS
    fingerprint and splice in the HKO (unit, statistic, precision_rounding)
    triple, then rehash -- the digest is internally self-consistent, but the
    triple itself remains unattainable from any real compiled event, so it
    must still be refused (finding F4)."""
    store, now = rig
    rule = _rule(eid="evt-crossfamily")
    payload = dict(rule.payload, unit="C", statistic="ABSOLUTE_DAILY_MAX_C",
                   precision_rounding="ONE_DECIMAL_C")
    forged = _fake_rule(payload)
    with expect_refusal("GAMMA_COMPARATOR_READER_RULE_BINDING_FIELDS_INVALID"):
        read_gamma_comparator(store=store, event_id="evt-crossfamily", rule=forged, through_seq=0)


@pytest.mark.parametrize("change", ["statistic", "parent_unit", "bucket_unit", "family"])
def test_rehashed_genuine_fingerprint_cross_field_contradictions_are_refused(rig, change):
    store, _ = rig
    rule = _rule(eid="evt-crossfield")
    payload = rule.payload
    if change == "statistic":
        payload["statistic"] = "DAILY_LOWEST_TEMP"
    elif change == "parent_unit":
        payload["unit"] = "C"
        payload["precision_rounding"] = "WHOLE_DEGREE_C"
    elif change == "bucket_unit":
        payload["partition"][0]["unit"] = "C"
    else:
        payload["family"] = "daily_low_temperature"
    forged = _fake_rule(payload)
    with expect_refusal("GAMMA_COMPARATOR_READER_RULE_BINDING_FIELDS_INVALID"):
        read_gamma_comparator(store=store, event_id="evt-crossfield", rule=forged, through_seq=0)


@pytest.mark.parametrize("change,code", [
    ("coherent_c_conflicts_with_f_contract", "RULE_BINDING_FIELDS_INVALID"),
    ("coherent_low_conflicts_with_high_contract", "RULE_BINDING_FIELDS_INVALID"),
    ("station_conflicts_with_contract", "RULE_BINDING_FIELDS_INVALID"),
    ("date_conflicts_with_contract", "RULE_BINDING_FIELDS_INVALID"),
    ("contract_sha_conflicts_with_contents", "RULE_BINDING_FIELDS_INVALID"),
    ("overlapping_intervals", "RULE_PARTITION_INVALID"),
    ("missing_all_bounds", "RULE_PARTITION_INVALID"),
    ("duplicate_condition_id", "RULE_PARTITION_INVALID"),
    ("question_bounds_conflict", "RULE_BINDING_FIELDS_INVALID"),
])
def test_rehashed_compiler_impossible_rule_refuses(rig, change, code):
    store, _ = rig
    rule = _rule(eid="event-f4-rehashed")
    payload = rule.payload
    if change == "coherent_c_conflicts_with_f_contract":
        payload["unit"] = "C"
        payload["precision_rounding"] = "WHOLE_DEGREE_C"
        for bucket in payload["partition"]:
            bucket["unit"] = "C"
    elif change == "coherent_low_conflicts_with_high_contract":
        payload["family"] = "daily_low_temperature"
        payload["statistic"] = "DAILY_LOWEST_TEMP"
    elif change == "station_conflicts_with_contract":
        payload["station"] = "KLAX"
    elif change == "date_conflicts_with_contract":
        payload["target_date"] = "2026-10-06"
    elif change == "contract_sha_conflicts_with_contents":
        payload["strict_contract"]["station"] = "KLAX"
    elif change == "overlapping_intervals":
        payload["partition"][1]["lower"] = payload["partition"][0]["lower"]
        payload["partition"][1]["upper"] = payload["partition"][0]["upper"]
    elif change == "missing_all_bounds":
        for bucket in payload["partition"]:
            bucket.pop("lower")
            bucket.pop("upper")
    elif change == "duplicate_condition_id":
        payload["partition"][1]["condition_id"] = payload["partition"][0]["condition_id"]
    else:
        payload["partition"][1]["lower"] += 1
        payload["partition"][0]["upper"] += 1
    forged = _fake_rule(payload)
    with expect_refusal("GAMMA_COMPARATOR_READER_" + code):
        read_gamma_comparator(store=store, event_id=payload["event_id"], rule=forged,
                              through_seq=0)


@pytest.mark.parametrize("field,value", [
    ("station", "KLAX"), ("target_date", "2026-10-06"),
    ("family", "daily_low_temperature"),
])
def test_rehashed_inner_and_outer_contract_still_require_compiler_output(rig, field, value):
    store, _ = rig
    rule = _rule(eid="event-f4-double-rehash")
    payload = rule.payload
    payload[field] = value
    payload["strict_contract"][field] = value
    if field == "family":
        payload["statistic"] = "DAILY_LOWEST_TEMP"
    contract = payload["strict_contract"]
    contract["sha256"] = digest({key: item for key, item in contract.items() if key != "sha256"})
    forged = _fake_rule(payload)
    with expect_refusal("GAMMA_COMPARATOR_READER_RULE_BINDING_FIELDS_INVALID"):
        read_gamma_comparator(store=store, event_id=payload["event_id"], rule=forged,
                              through_seq=0)


@pytest.mark.parametrize("family,unit", [("high", "F"), ("low", "F"),
                                         ("high", "C"), ("low", "C")])
def test_genuine_strict_rule_profiles_remain_accepted(rig, family, unit):
    store, _ = rig
    labels = [f"69°{unit} or lower", f"70-71°{unit}", f"72°{unit} or higher"]
    rule = fingerprint_event(_event(station="KATL", family=family, unit=unit,
                                    eid="event-f4-profile", labels=labels),
                             station_timezone="America/New_York", metadata_fingerprint="a" * 64)
    result = read_gamma_comparator(store=store, event_id="event-f4-profile", rule=rule,
                                   through_seq=0)
    if result.unit != unit or result.winner_market_id is not None:
        raise AssertionError(result)


def test_compact_date_format_is_refused(rig):
    """`date.fromisoformat` alone accepts a compact "YYYYMMDD" string, but
    `rules.fingerprint_event` always writes `compiled.target_date.isoformat()`
    (always dashed "YYYY-MM-DD"); a target_date that parses but does not
    round-trip to the identical string could never have come from the real
    compiler and must be refused (finding F4)."""
    store, now = rig
    payload = dict(event_id="evt-compactdate", station="KATL", timezone="America/New_York",
                   target_date="20261005", unit="F", statistic="DAILY_HIGHEST_TEMP",
                   precision_rounding="WHOLE_DEGREE_F",
                   partition=[{"market_id": "m0", "condition_id": "c0", "yes_token": "t0y", "no_token": "t0n"},
                              {"market_id": "m1", "condition_id": "c1", "yes_token": "t1y", "no_token": "t1n"}])
    rule = _fake_rule(payload)
    with expect_refusal("GAMMA_COMPARATOR_READER_RULE_BINDING_FIELDS_INVALID"):
        read_gamma_comparator(store=store, event_id="evt-compactdate", rule=rule, through_seq=0)


# --------------------------------------------------------------------------
# 18. F5: postdecision disclosure is flagged, not silently unknowable.
# --------------------------------------------------------------------------

def test_no_decision_cutoff_holds_postdecision_unknown(rig):
    store, now = rig
    rule = _rule(eid="event-f5-nocutoff")
    event_id = rule.payload["event_id"]
    bucket = rule.payload["partition"][0]
    _write_payout(store, event_id=event_id, bucket=bucket, winner=True, record_id="f5-nocutoff")
    through_seq = _frontier(store)

    result = read_gamma_comparator(store=store, event_id=event_id, rule=rule, through_seq=through_seq)
    assert result.decision_cutoff is None
    assert "POSTDECISION_DISCLOSURE_UNKNOWN_NO_DECISION_CUTOFF_SUPPLIED" in result.holds


def test_decision_cutoff_flags_postdecision_candidate(rig):
    store, now = rig
    rule = _rule(eid="event-f5-postdecision")
    event_id = rule.payload["event_id"]
    bucket = rule.payload["partition"][0]
    decision_cutoff = now[0]
    now[0] += 100.
    record = _write_payout(store, event_id=event_id, bucket=bucket, winner=True, record_id="f5-post")
    through_seq = _frontier(store)

    result = read_gamma_comparator(store=store, event_id=event_id, rule=rule, through_seq=through_seq,
                                    decision_cutoff=decision_cutoff)
    assert result.decision_cutoff == decision_cutoff
    assert "POSTDECISION_DISCLOSURE_PRESENT_AMONG_CANDIDATES" in result.holds
    assert result.provenance["postdecision_candidate_ids"] == (record["id"],)
    assert "POSTDECISION_DISCLOSURE_UNKNOWN_NO_DECISION_CUTOFF_SUPPLIED" not in result.holds


def test_decision_cutoff_predecision_candidate_not_flagged(rig):
    store, now = rig
    rule = _rule(eid="event-f5-predecision")
    event_id = rule.payload["event_id"]
    bucket = rule.payload["partition"][0]
    _write_payout(store, event_id=event_id, bucket=bucket, winner=True, record_id="f5-pre")
    decision_cutoff = now[0] + 100.
    through_seq = _frontier(store)

    result = read_gamma_comparator(store=store, event_id=event_id, rule=rule, through_seq=through_seq,
                                    decision_cutoff=decision_cutoff)
    assert "POSTDECISION_DISCLOSURE_PRESENT_AMONG_CANDIDATES" not in result.holds


def test_self_declared_early_observed_at_does_not_mask_postdecision_disclosure(rig):
    """A row's own self-declared observed_at claiming an earlier time than
    its actual archive retention must not hide a genuinely late receipt
    from the postdecision-disclosure hold -- a row not retained until after
    the decision cannot have been used at the decision, regardless of an
    earlier self-declared observation claim (finding F5)."""
    store, now = rig
    rule = _rule(eid="event-f5-selfdeclared")
    event_id = rule.payload["event_id"]
    bucket = rule.payload["partition"][0]
    decision_cutoff = now[0]
    now[0] += 10.
    market = _closed_market(bucket["market_id"], bucket["yes_token"], bucket["no_token"],
                             condition_id=bucket["condition_id"], winner=True)
    record = store.capture("self-declared-early", event_id=event_id, kind="RULES", provider="GAMMA_CLOSED_MARKET",
                            source_identity="market:" + bucket["market_id"], revision="r1",
                            evidence_class="PUBLIC_OBSERVED", payload={"response": market},
                            observed_at=decision_cutoff - 1.)
    through_seq = _frontier(store)

    result = read_gamma_comparator(store=store, event_id=event_id, rule=rule, through_seq=through_seq,
                                    decision_cutoff=decision_cutoff)
    assert "POSTDECISION_DISCLOSURE_PRESENT_AMONG_CANDIDATES" in result.holds
    assert result.provenance["postdecision_candidate_ids"] == (record["id"],)


def test_decision_cutoff_invalid_type_is_refused(rig):
    store, now = rig
    rule = _rule(eid="event-f5-badcutoff")
    with expect_refusal("GAMMA_COMPARATOR_READER_DECISION_CUTOFF_INVALID"):
        read_gamma_comparator(store=store, event_id=rule.payload["event_id"], rule=rule,
                               through_seq=0, decision_cutoff="not-a-number")
