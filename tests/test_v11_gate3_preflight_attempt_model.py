"""Deterministic transition, fault, budget and malformed-input probes
P01-P12 for the Gate 3 offline attempt model
(``tools/v11_gate3_preflight_attempt_model.py``), per
``docs/V11_R09_GATE3_PREFLIGHT_NEXT_SLICE_HANDOFF_20261002.md``.

Every fixture here is synthetic/offline (``synthetic://``-prefixed paths,
fabricated sha256 values). No private package, private evidence or real
provider file is opened; no socket, DNS, subprocess or credential lookup is
performed by the candidate module. A PASS here is model-only: it confers no
real execution authority, provider right, G3-L credit or qualification
claim.
"""

from __future__ import annotations

import ast
import copy
import dataclasses
import json
import socket
import subprocess
import sys

import pytest

from tools.v11_gate3_evidence_preflight_checker import ClockObservation, ResourceObservation
from tools.v11_gate3_preflight_attempt_model import (
    BODY_CAP, BODY_CHUNK, EVENT_CAP, SCHEMA, SCRIPT_CAP, STAGE_TIME, TAGS,
    ModelResult, ModelState, SyntheticInputs, _safe_ref, admit_synthetic,
    recover_synthetic, result, run_synthetic, step,
)

from tests.v11_gate3_preflight_synthetic_cases import (
    GOOD_CLOCK, GOOD_REVIEW_TERMINAL, HAPPY_TAGS, HEAD0, RETAINED_22_REASONS,
    RETAINED_DENIAL_RECORDS, all_blocked_package_and_restrictions, drive,
    drive_happy_path, encode_raws, genesis_checkpoint, good_binding_dict,
    good_inputs, good_package_dict, good_raws, good_restrictions_dict,
    next_event,
)

ATTEMPT_MODEL_PATH = "tools/v11_gate3_preflight_attempt_model.py"


# =============================================================================
# P01 - happy path positive control
# =============================================================================

class TestP01HappyPath:
    def test_single_start_retained_unqualified_with_measured_charges(self):
        state, transitions = drive_happy_path()
        assert [t.accepted for t in transitions] == [True] * len(HAPPY_TAGS)
        assert state.phase == "RETAINED_UNQUALIFIED"
        assert state.starts == 1

        res = result(state)
        assert res.schema == SCHEMA
        assert res.synthetic is True
        assert res.outcome == "RETAINED_UNQUALIFIED"
        assert res.attempt_state == "RETAINED_UNQUALIFIED"
        assert res.starts == 1
        assert res.used_attempts == 1
        assert res.used_body_bytes == 3
        assert res.used_time_us == 200_000  # measured elapsed (close - start), not the 60s reservation
        assert res.outstanding_attempts == 0
        assert res.outstanding_body_bytes == 0
        assert res.outstanding_time_us == 0
        assert res.denials == ()
        assert res.reasons == ()
        assert [r[0] for r in res.refs] == [
            "intent", "reservation", "closure", "accounting", "seal",
        ]
        assert res.execution_authority is False
        assert res.provider_authority is False
        assert res.capture_authority is False
        assert res.qualification_credit == 0
        assert res.eligibility == "DISCOVERY_ONLY_NOT_G3E"
        assert res.parser_ref is None
        assert res.parse_result_ref is None
        assert res.parse_absence_reason == "NOT_IMPLEMENTED_IN_THIS_SLICE"
        assert res.decode_absence_reason == "NOT_PERFORMED_BYTES_ONLY"

    def test_run_synthetic_matches_manual_step_by_step_result(self):
        inputs = good_inputs()
        checkpoint = genesis_checkpoint()
        manual_state, _ = drive(inputs, checkpoint)
        manual_result = result(manual_state)

        # Rebuild the identical event tuple by replaying the same driver,
        # then feed it to run_synthetic's whole-script validator/reducer.
        owner = checkpoint.owner
        state = admit_synthetic(inputs, checkpoint)
        events = []
        for tag in HAPPY_TAGS:
            events.append(next_event(state, owner, tag))
            state = step(state, events[-1]).state
        scripted_result = run_synthetic(inputs, checkpoint, tuple(events))
        assert scripted_result == manual_result


# =============================================================================
# P02 - admission refusal: the full retained 22-reason blocker set, plus
# individual changed path/run/date/limit/key/hash/review mutations.
# =============================================================================

class TestP02AdmissionRefusal:
    def test_all_22_retained_reasons_reproduced_with_frozen_interior_clock(self):
        pkg, restrictions = all_blocked_package_and_restrictions()
        raws = encode_raws(pkg, restrictions)
        inputs = good_inputs(raws=raws)
        state = admit_synthetic(inputs, genesis_checkpoint())
        assert state.phase == "REFUSED_BEFORE_DISPATCH"
        assert state.starts == 0
        assert set(state.reasons) == set(RETAINED_22_REASONS)
        assert len(RETAINED_22_REASONS) == 22
        res = result(state)
        assert res.used_attempts == 0
        assert res.outstanding_attempts == 0
        assert res.starts == 0

    def test_positive_control_good_fixture_admits(self):
        state = admit_synthetic(good_inputs(), genesis_checkpoint())
        assert state.phase == "ADMITTED"
        assert state.reasons == ()

    @pytest.mark.parametrize("mutate,expected_reason", [
        (lambda pkg: pkg["requests"][0].__setitem__(
            "path", "/gefs.20261003/00/atmos/pgrb2ap5/gec00.t00z.pgrb2a.0p50.f024.idx"),
         "CHANGED_REQUEST_PATH"),
        (lambda pkg: pkg.__setitem__("run_utc", "2026-10-03T00:00:00Z"),
         "CHANGED_FIELD:package.run_utc"),
        (lambda pkg: pkg["limits"].__setitem__("campaign_attempts", 99),
         "CHANGED_FIELD:limits.campaign_attempts"),
        (lambda pkg: pkg["window"].__setitem__("expires_utc", "2026-10-02T23:59:59Z"),
         "CHANGED_FIELD:window.expires_utc"),
        (lambda pkg: pkg.__setitem__("unexpected_extra_field", "synthetic"),
         "UNKNOWN_KEY:package.unexpected_extra_field"),
    ])
    def test_refuses_changed_path_run_date_limit_or_unknown_key(self, mutate, expected_reason):
        pkg = good_package_dict()
        mutate(pkg)
        raws = encode_raws(pkg, good_restrictions_dict())
        state = admit_synthetic(good_inputs(raws=raws), genesis_checkpoint())
        assert state.phase == "REFUSED_BEFORE_DISPATCH"
        assert state.starts == 0
        assert expected_reason in state.reasons

    def test_refuses_duplicate_json_key_in_raw_package_bytes(self):
        package_raw, restrictions_raw, protocol_raw, _ = good_raws()
        raw_text = package_raw.decode()
        dup = raw_text.replace('"schema":', '"schema": "DUPLICATE", "schema":', 1).encode()
        binding_raw = json.dumps(good_binding_dict(dup, restrictions_raw, protocol_raw)).encode()
        inputs = good_inputs(raws=(dup, restrictions_raw, protocol_raw, binding_raw))
        state = admit_synthetic(inputs, genesis_checkpoint())
        assert state.phase == "REFUSED_BEFORE_DISPATCH"
        assert "NON_SYNTHETIC_PATH_OR_INVALID_JSON" in state.reasons
        assert state.starts == 0

    def test_refuses_changed_raw_hash_or_length_vs_binding(self):
        package_raw, restrictions_raw, protocol_raw, binding_raw = good_raws()
        tampered_package_raw = package_raw + b" "
        state = admit_synthetic(
            good_inputs(raws=(tampered_package_raw, restrictions_raw, protocol_raw, binding_raw)),
            genesis_checkpoint(),
        )
        assert state.phase == "REFUSED_BEFORE_DISPATCH"
        assert "CHANGED_PRIVATE_PACKAGE_BYTES" in state.reasons

    def test_refuses_missing_review_terminal(self):
        state = admit_synthetic(good_inputs(review_terminal=None), genesis_checkpoint())
        assert state.phase == "REFUSED_BEFORE_DISPATCH"
        assert "MISSING_OR_INVALID_REVIEW_TERMINAL" in state.reasons

    def test_refuses_design_only_verdict_in_review_terminal(self):
        terminal = dict(GOOD_REVIEW_TERMINAL, verdict="PASS_IN_SCOPE_DESIGN_BLOCKED_PACKAGE")
        state = admit_synthetic(good_inputs(review_terminal=terminal), genesis_checkpoint())
        assert state.phase == "REFUSED_BEFORE_DISPATCH"
        assert "MISSING_OR_INVALID_REVIEW_TERMINAL" in state.reasons


# =============================================================================
# P03 - disallowed request shapes and fingerprint/CheckResult integrity.
# =============================================================================

class TestP03DisallowedRequestShapes:
    @pytest.mark.parametrize("mutate,expected_reason", [
        (lambda pkg: pkg["requests"][0].__setitem__("purpose", "FIELD"),
         "ATTEMPTED_FIELD_OR_NONINDEX_REQUEST"),
        (lambda pkg: pkg["requests"][0].__setitem__(
            "origin", "https://ecmwf-forecasts.s3.eu-central-1.amazonaws.com"),
         "ATTEMPTED_ECMWF_REQUEST"),
        (lambda pkg: pkg["requests"][0].__setitem__("method", "HEAD"),
         "ATTEMPTED_HEAD_REQUEST"),
        (lambda pkg: pkg["requests"][0].__setitem__("range", {"bytes": "0-10"}),
         "CHANGED_REQUEST_FIELD:range"),
        (lambda pkg: pkg["requests"][0].__setitem__("query", {"x": "1"}),
         "CHANGED_REQUEST_FIELD:query"),
        (lambda pkg: pkg["requests"][0].__setitem__("request_body", "data"),
         "CHANGED_REQUEST_FIELD:request_body"),
        (lambda pkg: pkg["requests"][0]["request_headers"].__setitem__(
            "Authorization", "Bearer synthetic-credential"),
         "CHANGED_REQUEST_HEADERS"),
    ])
    def test_refuses_disallowed_request_mutation_no_start(self, mutate, expected_reason):
        pkg = good_package_dict()
        mutate(pkg)
        raws = encode_raws(pkg, good_restrictions_dict())
        state = admit_synthetic(good_inputs(raws=raws), genesis_checkpoint())
        assert state.phase == "REFUSED_BEFORE_DISPATCH"
        assert state.starts == 0
        assert expected_reason in state.reasons

    def test_refuses_second_request_in_same_package(self):
        pkg = good_package_dict()
        second = copy.deepcopy(pkg["requests"][0])
        second["request_id"] = "p1-gefs-2026100200-c00-f024-index-second"
        pkg["requests"].append(second)
        raws = encode_raws(pkg, good_restrictions_dict())
        state = admit_synthetic(good_inputs(raws=raws), genesis_checkpoint())
        assert state.phase == "REFUSED_BEFORE_DISPATCH"
        assert "MULTIPLE_REQUESTS_NOT_PERMITTED" in state.reasons

    def test_synthetic_inputs_has_no_cached_checkresult_field(self):
        # The model must recompute the checker outcome from the exact raw
        # bytes every time; a caller cannot hand in a pre-built CheckResult
        # to bypass admission. SyntheticInputs structurally has no such slot.
        field_names = {f.name for f in dataclasses.fields(SyntheticInputs)}
        assert "check_result" not in field_names
        assert field_names == {
            "package_raw", "restrictions_raw", "protocol_raw", "binding_raw",
            "clock", "resources", "ledger", "review_terminal", "mode",
        }

    def test_fingerprint_depends_on_exact_supplied_bytes_not_a_promotable_claim(self):
        state_a = admit_synthetic(good_inputs(), genesis_checkpoint())
        package_raw, restrictions_raw, protocol_raw, binding_raw = good_raws()
        tampered_binding = json.loads(binding_raw)
        tampered_binding["prepared_at_utc"] = "2026-10-02T09:25:30.001041+00:00"
        other_raws = (package_raw, restrictions_raw, protocol_raw,
                      json.dumps(tampered_binding).encode())
        state_b = admit_synthetic(good_inputs(raws=other_raws), genesis_checkpoint())
        assert state_a.phase == state_b.phase == "ADMITTED"
        assert state_a.fingerprint != state_b.fingerprint

    def test_model_result_rejects_output_promotion_even_for_valid_outcome(self):
        base = dict(
            schema=SCHEMA, synthetic=True, outcome="RETAINED_UNQUALIFIED",
            attempt_state="RETAINED_UNQUALIFIED", fingerprint="f",
            input_history_head=HEAD0, output_history_head=HEAD0, starts=1,
            used_attempts=1, used_body_bytes=3, used_time_us=1,
            outstanding_attempts=0, outstanding_body_bytes=0,
            outstanding_time_us=0, holds=(), reasons=(), refs=(),
        )
        ModelResult(**base)  # the honest version must construct cleanly
        for bad in (
            dict(execution_authority=True), dict(provider_authority=True),
            dict(capture_authority=True), dict(qualification_credit=1),
            dict(eligibility="G3E_QUALIFIED"),
            dict(outcome="CAPTURED"), dict(outcome="READY"),
            dict(outcome="QUALIFIED"), dict(outcome="G3L_PASS"),
        ):
            with pytest.raises(ValueError):
                ModelResult(**{**base, **bad})


# =============================================================================
# P04 - fault injection at every stage; recovery never resumes.
# =============================================================================

class TestP04FaultInjectionAndRecovery:
    def test_fault_before_dispatch_refuses_terminal_zero_charge(self):
        # Only a proven pre-intent fault (before locks are even taken) can
        # charge nothing: no intent write was ever attempted.
        state, transitions = drive(good_inputs(), genesis_checkpoint(), ("FAULT",),
                                    cfg={"fault_kind": "CRASH"})
        assert transitions[-1].accepted is True
        assert state.phase == "REFUSED_BEFORE_DISPATCH"
        res = result(state)
        assert res.starts == 0
        assert res.used_attempts == 0
        assert res.outstanding_attempts == 0

    def test_fault_while_locked_is_ambiguous_holds_full_reservation(self):
        # A fault exactly while the intent is being written (LOCKED) is
        # ambiguous: the write may or may not have landed. This must hold
        # the full reservation, not refuse with zero charge, so the same
        # checkpoint can never be re-admitted while the true fate of the
        # intent write is unknown.
        state, transitions = drive(good_inputs(), genesis_checkpoint(), ("LOCKS", "FAULT"),
                                    cfg={"fault_kind": "CRASH"})
        assert transitions[-1].accepted is True
        assert state.phase == "UNCERTAIN_HELD"
        res = result(state)
        assert res.starts == 0
        assert res.used_attempts == 0
        assert res.outstanding_attempts == 1
        assert res.outstanding_body_bytes == BODY_CAP
        assert res.outstanding_time_us == STAGE_TIME

    @pytest.mark.parametrize("tags,fault_kind", [
        (("LOCKS", "INTENT_ACK", "FAULT"), "WRITE_FAILURE"),
        (("LOCKS", "INTENT_ACK", "RESERVE_ACK", "FAULT"), "FSYNC_FAILURE"),
    ])
    def test_fault_after_intent_or_reservation_holds_full_reservation_no_start(self, tags, fault_kind):
        state, transitions = drive(good_inputs(), genesis_checkpoint(), tags,
                                    cfg={"fault_kind": fault_kind})
        assert transitions[-1].accepted is True
        assert state.phase == "UNCERTAIN_HELD"
        assert fault_kind in state.reasons
        assert state.report_reserved is True
        res = result(state)
        assert res.starts == 0
        assert res.used_attempts == 0
        assert res.outstanding_attempts == 1
        assert res.outstanding_body_bytes == BODY_CAP
        assert res.outstanding_time_us == STAGE_TIME

    def test_fault_after_start_charges_attempt_but_holds_outstanding(self):
        tags = ("LOCKS", "INTENT_ACK", "RESERVE_ACK", "START", "FAULT")
        state, transitions = drive(good_inputs(), genesis_checkpoint(), tags,
                                    cfg={"fault_kind": "DNS_FAILURE"})
        assert transitions[-1].accepted is True
        assert state.phase == "UNCERTAIN_HELD"
        res = result(state)
        assert res.starts == 1
        assert res.used_attempts == 1  # DNS/TLS failure still consumes the attempt
        # Settlement moves the reservation into usage at START, not
        # alongside it: the single stage-attempt slot is already consumed,
        # so it is no longer separately "outstanding" (that would
        # double-count the one real attempt against the campaign/pilot
        # ceilings). Body/time remain outstanding: no ACCOUNT_ACK ran.
        assert res.outstanding_attempts == 0
        assert res.outstanding_body_bytes == BODY_CAP

    def test_eager_denial_survives_a_later_fault(self):
        tags = ("LOCKS", "INTENT_ACK", "RESERVE_ACK", "START", "STATUS", "FAULT")
        state, transitions = drive(good_inputs(), genesis_checkpoint(), tags,
                                    cfg={"status": 503, "fault_kind": "TIMEOUT"})
        assert transitions[-1].accepted is True
        assert state.phase == "UNCERTAIN_HELD"
        assert any(d.startswith("synthetic://denial/status-503") for d in state.denials)
        assert "TIMEOUT" in state.reasons

    def test_fault_after_closed_and_after_accounted_both_hold(self):
        state, transitions = drive(good_inputs(), genesis_checkpoint(),
                                    ("LOCKS", "INTENT_ACK", "RESERVE_ACK", "START",
                                     "STATUS", "HEADERS", "BODY", "CLOSE_ACK", "FAULT"),
                                    cfg={"fault_kind": "CRASH"})
        assert state.phase == "UNCERTAIN_HELD"
        assert result(state).used_body_bytes == 0  # ACCOUNT_ACK never ran

        state2, _ = drive(good_inputs(), genesis_checkpoint(),
                           ("LOCKS", "INTENT_ACK", "RESERVE_ACK", "START",
                            "STATUS", "HEADERS", "BODY", "CLOSE_ACK", "ACCOUNT_ACK",
                            "FAULT"),
                           cfg={"fault_kind": "LOST_OWNER"})
        assert state2.phase == "UNCERTAIN_HELD"
        res2 = result(state2)
        assert res2.used_body_bytes == 3  # already settled before the fault
        assert res2.outstanding_body_bytes == 0  # released at ACCOUNT_ACK, not refundable twice

    def test_terminal_state_blocks_every_further_event_including_duplicate_seal(self):
        state, _ = drive_happy_path()
        assert state.phase == "RETAINED_UNQUALIFIED"
        duplicate_seal = next_event(state, genesis_checkpoint().owner, "SEAL_ACK")
        t = step(state, duplicate_seal)
        assert t.accepted is False
        assert t.reason == "TERMINAL_STATE"
        assert t.state is state  # no mutation, no double settlement

    def test_recover_synthetic_is_always_held_never_resumed(self):
        checkpoint = genesis_checkpoint()
        snapshot = {
            "schema": SCHEMA, "phase": "RECEIVING", "head": checkpoint.expected_history_head,
            "fingerprint": "deadbeef", "sequence": 4, "owner": checkpoint.owner,
            "used_attempts": 1, "delivered_bytes": 2, "denials": [],
        }
        transition = recover_synthetic(json.dumps(snapshot).encode(), checkpoint)
        assert transition.accepted is True
        assert transition.state.phase == "UNCERTAIN_HELD"
        assert "RECOVERED_INCOMPLETE_INTENT" in transition.state.reasons
        assert transition.state.report_reserved is True

        # Terminal: cannot resume with a further event.
        further = next_event(transition.state, checkpoint.owner, "STATUS",
                              {"status": 200, "explicit_denial": False})
        blocked = step(transition.state, further)
        assert blocked.accepted is False
        assert blocked.reason == "TERMINAL_STATE"

    def test_recover_synthetic_refuses_tampered_or_truncated_or_oversized_snapshot(self):
        checkpoint = genesis_checkpoint()
        good_snapshot = {
            "schema": SCHEMA, "phase": "RECEIVING", "head": checkpoint.expected_history_head,
            "fingerprint": "deadbeef", "sequence": 4, "owner": checkpoint.owner,
            "used_attempts": 1, "delivered_bytes": 2, "denials": [],
        }
        tampered = dict(good_snapshot, head="f" * 64)
        t1 = recover_synthetic(json.dumps(tampered).encode(), checkpoint)
        assert t1.accepted is False and t1.reason == "TAMPERED_SNAPSHOT"

        t2 = recover_synthetic(b"{not valid json", checkpoint)
        assert t2.accepted is False and t2.reason == "INVALID_SNAPSHOT"

        oversized = b"x" * (1_048_576 + 1)
        t3 = recover_synthetic(oversized, checkpoint)
        assert t3.accepted is False and t3.reason == "INVALID_SNAPSHOT"


# =============================================================================
# P05 - replay/reset: stale head, missing genesis, unfinished intent,
# competing owner, read-only replay of a sealed result.
# =============================================================================

class TestP05ReplayAndReset:
    def test_structurally_invalid_checkpoint_raises_not_refuses(self):
        with pytest.raises(ValueError):
            admit_synthetic(good_inputs(), genesis_checkpoint(expected_history_head="not-hex"))

    def test_stale_external_head_refuses_before_dispatch(self):
        cp = genesis_checkpoint(external_history_head="1" * 64)
        state = admit_synthetic(good_inputs(), cp)
        assert state.phase == "REFUSED_BEFORE_DISPATCH"
        assert "UNRECONCILED_HISTORY_OR_INTENT" in state.reasons
        assert state.starts == 0

    def test_missing_genesis_refuses(self):
        cp = genesis_checkpoint(synthetic_genesis=False)
        state = admit_synthetic(good_inputs(), cp)
        assert state.phase == "REFUSED_BEFORE_DISPATCH"
        assert "UNRECONCILED_HISTORY_OR_INTENT" in state.reasons

    def test_unfinished_intent_refuses_as_second_in_flight(self):
        cp = genesis_checkpoint(unfinished_intents=("synthetic://intent/earlier",))
        state = admit_synthetic(good_inputs(), cp)
        assert state.phase == "REFUSED_BEFORE_DISPATCH"
        assert "UNRECONCILED_HISTORY_OR_INTENT" in state.reasons

    def test_unknown_hold_prefix_refuses(self):
        cp = genesis_checkpoint(holds=("synthetic://not-a-retained-denial",))
        state = admit_synthetic(good_inputs(), cp)
        assert state.phase == "REFUSED_BEFORE_DISPATCH"
        assert "UNKNOWN_HOLD" in state.reasons

    def test_known_retained_denial_hold_is_accepted_at_admission(self):
        cp = genesis_checkpoint(holds=("synthetic://retained-denial/503-earlier",))
        state = admit_synthetic(good_inputs(), cp)
        assert state.phase == "ADMITTED"
        assert state.denials == cp.holds

    def test_competing_owner_event_refuses_without_mutating_state(self):
        state, _ = drive(good_inputs(), genesis_checkpoint(), ("LOCKS",))
        bad_owner_event = next_event(state, "synthetic://owner/a-different-worker", "INTENT_ACK")
        t = step(state, bad_owner_event)
        assert t.accepted is False
        assert t.reason == "SEQUENCE_OWNER_OR_HEAD_MISMATCH"
        assert t.state is state

    def test_sealed_result_is_idempotent_and_read_only(self):
        state, _ = drive_happy_path()
        assert result(state) == result(state)
        # A further ACCOUNT_ACK/SEAL_ACK replay cannot double-settle.
        for tag in ("ACCOUNT_ACK", "SEAL_ACK"):
            t = step(state, next_event(state, genesis_checkpoint().owner, tag))
            assert t.accepted is False
            assert t.reason == "TERMINAL_STATE"


# =============================================================================
# P06 - boundary tables (cap-1/cap/cap+1), DNS/TLS consumption, start spacing.
# =============================================================================

class TestP06BoundaryTables:
    def _reserve(self, checkpoint):
        return drive(good_inputs(), checkpoint, ("LOCKS", "INTENT_ACK", "RESERVE_ACK"))

    def test_stage_attempt_cap_exact_passes_and_plus_one_holds(self):
        state, transitions = self._reserve(genesis_checkpoint())
        assert transitions[-1].accepted is True
        assert state.phase == "RESERVED"

        over_cp = genesis_checkpoint(outstanding_attempts=1)
        state2, transitions2 = self._reserve(over_cp)
        assert transitions2[-1].accepted is True
        assert state2.phase == "UNCERTAIN_HELD"
        assert "BUDGET_EXCEEDED" in state2.reasons

    @pytest.mark.parametrize("pilot_used,expect_held", [(3598, False), (3599, False), (3600, True)])
    def test_pilot_combined_attempt_ceiling_boundary(self, pilot_used, expect_held):
        cp = genesis_checkpoint(pilot_used_attempts=pilot_used)
        state, _ = self._reserve(cp)
        assert (state.phase == "UNCERTAIN_HELD") == expect_held
        if expect_held:
            assert "BUDGET_EXCEEDED" in state.reasons
        else:
            assert state.phase == "RESERVED"

    @pytest.mark.parametrize("used_body,expect_held", [
        (33_554_432 - BODY_CAP - 1, False),
        (33_554_432 - BODY_CAP, False),
        (33_554_432 - BODY_CAP + 1, True),
    ])
    def test_campaign_body_ceiling_boundary(self, used_body, expect_held):
        cp = genesis_checkpoint(used_body_bytes=used_body)
        state, _ = self._reserve(cp)
        assert (state.phase == "UNCERTAIN_HELD") == expect_held

    @pytest.mark.parametrize("used_time,expect_held", [
        (120_000_000 - STAGE_TIME - 1, False),
        (120_000_000 - STAGE_TIME, False),
        (120_000_000 - STAGE_TIME + 1, True),
    ])
    def test_campaign_elapsed_ceiling_boundary(self, used_time, expect_held):
        cp = genesis_checkpoint(used_time_us=used_time)
        state, _ = self._reserve(cp)
        assert (state.phase == "UNCERTAIN_HELD") == expect_held

    @pytest.mark.parametrize("pilot_used_body,expect_held", [
        (1_073_741_824 - BODY_CAP - 1, False),
        (1_073_741_824 - BODY_CAP, False),
        (1_073_741_824 - BODY_CAP + 1, True),
    ])
    def test_pilot_combined_body_ceiling_boundary(self, pilot_used_body, expect_held):
        cp = genesis_checkpoint(pilot_used_body_bytes=pilot_used_body)
        state, _ = self._reserve(cp)
        assert (state.phase == "UNCERTAIN_HELD") == expect_held

    def test_incomplete_reservation_acknowledgement_holds(self):
        state, transitions = drive(good_inputs(), genesis_checkpoint(),
                                    ("LOCKS", "INTENT_ACK", "RESERVE_ACK"),
                                    cfg={"body_bytes": BODY_CAP - 1})
        assert state.phase == "UNCERTAIN_HELD"
        assert "INCOMPLETE_RESERVATION" in state.reasons

    @pytest.mark.parametrize("delta_us,expect_held", [
        (1_999_999, True), (2_000_000, False), (2_000_001, False),
    ])
    def test_minimum_start_spacing_boundary_across_restart(self, delta_us, expect_held):
        last_start = 50_000_000
        cp = genesis_checkpoint(last_start_us=last_start)
        state, _ = drive(good_inputs(), cp, ("LOCKS", "INTENT_ACK", "RESERVE_ACK", "START"),
                          cfg={"start_mono": last_start + delta_us})
        assert (state.phase == "UNCERTAIN_HELD") == expect_held
        if expect_held:
            assert "P1_REPLAY_OR_START_SPACING" in state.reasons
        else:
            assert state.phase == "STARTED"
        assert state.starts in (0, 1)

    def test_p1_never_starts_twice_even_when_resent(self):
        state, transitions = drive(good_inputs(), genesis_checkpoint(), HAPPY_TAGS[:5])
        assert state.phase == "RECEIVING"
        second_start = next_event(state, genesis_checkpoint().owner, "START",
                                   {"start_mono": 99_000_000})
        t = step(state, second_start)
        assert t.accepted is False
        assert t.reason == "OUT_OF_ORDER_EVENT"

    def test_dns_and_tls_faults_consume_the_reserved_attempt(self):
        for kind in ("DNS_FAILURE", "TLS_FAILURE"):
            state, _ = drive(good_inputs(), genesis_checkpoint(),
                              ("LOCKS", "INTENT_ACK", "RESERVE_ACK", "START", "FAULT"),
                              cfg={"fault_kind": kind})
            assert state.phase == "UNCERTAIN_HELD"
            assert result(state).used_attempts == 1


# =============================================================================
# P07 - denials, explicit_denial/Retry-After, immutable retained identities.
# =============================================================================

class TestP07Denials:
    @pytest.mark.parametrize("status", [401, 403, 429, 503])
    def test_denial_status_held_before_body_then_crash_preserves_hold(self, status):
        state, transitions = drive(good_inputs(), genesis_checkpoint(),
                                    ("LOCKS", "INTENT_ACK", "RESERVE_ACK", "START",
                                     "STATUS", "FAULT"),
                                    cfg={"status": status, "fault_kind": "WRITE_FAILURE"})
        assert state.phase == "UNCERTAIN_HELD"
        assert any(d == f"synthetic://denial/status-{status}" for d in state.denials)
        assert "WRITE_FAILURE" in state.reasons

    def test_explicit_denial_flag_holds_even_on_200(self):
        state, transitions = drive(good_inputs(), genesis_checkpoint(),
                                    ("LOCKS", "INTENT_ACK", "RESERVE_ACK", "START", "STATUS"),
                                    cfg={"status": 200, "explicit_denial": True})
        assert state.phase == "RECEIVING"
        assert any(d == "synthetic://denial/status-200" for d in state.denials)

    def test_retry_after_header_forces_denied_held_even_with_otherwise_valid_framing(self):
        state, transitions = drive(good_inputs(), genesis_checkpoint(), HAPPY_TAGS,
                                    cfg={"extra_headers": ((b"Retry-After", b"120"),)})
        assert state.phase == "DENIED_HELD"
        assert any(d == "synthetic://denial/header" for d in state.denials)

    def test_malformed_retry_after_value_still_holds(self):
        state, _ = drive(good_inputs(), genesis_checkpoint(), HAPPY_TAGS,
                          cfg={"extra_headers": ((b"Retry-After", b"not-a-number"),)})
        assert state.phase == "DENIED_HELD"

    def test_removing_a_retained_denial_record_refuses_admission(self):
        restrictions = good_restrictions_dict()
        restrictions["records"] = restrictions["records"][:2]  # drop the 429
        raws = encode_raws(good_package_dict(), restrictions)
        state = admit_synthetic(good_inputs(raws=raws), genesis_checkpoint())
        assert state.phase == "REFUSED_BEFORE_DISPATCH"
        assert "MISSING_OR_DUPLICATED_RETAINED_DENIAL" in state.reasons

    def test_editing_a_retained_denial_record_refuses_admission(self):
        restrictions = good_restrictions_dict()
        restrictions["records"][0]["response"]["headers"]["server"] = "tampered"
        raws = encode_raws(good_package_dict(), restrictions)
        state = admit_synthetic(good_inputs(raws=raws), genesis_checkpoint())
        assert state.phase == "REFUSED_BEFORE_DISPATCH"
        assert "TAMPERED_RETAINED_DENIAL" in state.reasons

    def test_later_synthetic_success_does_not_erase_retained_denials(self):
        # A prior campaign against this checkpoint already retained three
        # denial holds (handoff: denial records come from the retained
        # verdict, carried on the checkpoint, never re-derived from input
        # bytes). A later, independent synthetic attempt on a fresh
        # admission against that same checkpoint must not erase or
        # overwrite those prior retained denial records: they must still be
        # present in the new attempt's result, and the new attempt's own
        # accounting (a real start, a measured body) must still proceed
        # normally rather than being short-circuited by the carried holds.
        prior_holds = tuple(f"synthetic://retained-denial/{i}" for i in range(3))
        cp = genesis_checkpoint(holds=prior_holds)
        state, transitions = drive_happy_path(checkpoint=cp)
        assert [t.accepted for t in transitions] == [True] * len(HAPPY_TAGS)
        assert state.phase == "DENIED_HELD"
        res = result(state)
        assert set(prior_holds) <= set(res.holds)
        assert set(prior_holds) <= set(res.denials)
        assert res.starts == 1
        assert res.used_attempts == 1
        assert res.used_body_bytes == 3


# =============================================================================
# P08 - header/body framing edges.
# =============================================================================

class TestP08Framing:
    def _seal_outcome(self, cfg):
        state, _ = drive(good_inputs(), genesis_checkpoint(), HAPPY_TAGS, cfg=cfg)
        return state

    def test_duplicate_case_insensitive_header_name_is_invalid_framing(self):
        state = self._seal_outcome({"extra_headers": ((b"content-type", b"text/plain"),)})
        assert state.phase == "RETAINED_INVALID"

    @pytest.mark.parametrize("content_length", [b"", b"0", b"3x", b"3145729"])
    def test_malformed_or_out_of_range_content_length_is_invalid_framing(self, content_length):
        state, transitions = drive(good_inputs(), genesis_checkpoint(), HAPPY_TAGS[:5])
        assert state.phase == "RECEIVING"
        owner = genesis_checkpoint().owner
        headers = ((b"Content-Type", b"text/plain"), (b"Content-Length", content_length))
        event = {**next_event(state, owner, "HEADERS"), "headers": headers}
        t = step(state, event)
        assert t.accepted is True  # the event itself is well-formed, just not "good" framing
        assert t.state.framing_valid is False

    def test_transfer_encoding_present_is_invalid_framing(self):
        state = self._seal_outcome({"extra_headers": ((b"Transfer-Encoding", b"chunked"),)})
        assert state.phase == "RETAINED_INVALID"

    def test_compressed_body_is_invalid_framing(self):
        state = self._seal_outcome({"extra_headers": ((b"Content-Encoding", b"gzip"),)})
        assert state.phase == "RETAINED_INVALID"

    def test_non_200_status_with_otherwise_valid_framing_is_invalid_not_success(self):
        state = self._seal_outcome({"status": 418})
        assert state.phase == "RETAINED_INVALID"

    def test_short_body_vs_declared_content_length_is_invalid(self):
        state = self._seal_outcome({"content_length": 4})  # header claims 4, body delivers 3
        assert state.phase == "RETAINED_INVALID"

    def test_overdelivered_body_over_declared_length_but_under_cap_is_invalid(self):
        state = self._seal_outcome({"content_length": 3, "body": b"abc", "body_chunk": b"abcd"})
        assert state.phase == "RETAINED_INVALID"

    def test_body_crossing_cap_poisons_campaign_without_refund(self):
        # BODY chunks are individually capped at BODY_CHUNK (64 KiB); reaching
        # BODY_CAP (an exact multiple of BODY_CHUNK) takes a sequence of
        # full-size chunks, then one more byte to cross it.
        assert BODY_CAP % BODY_CHUNK == 0
        full_chunks = BODY_CAP // BODY_CHUNK
        state, transitions = drive(
            good_inputs(), genesis_checkpoint(),
            ("LOCKS", "INTENT_ACK", "RESERVE_ACK", "START", "STATUS", "HEADERS"),
            cfg={"content_length": BODY_CAP},
        )
        assert state.phase == "RECEIVING"
        owner = genesis_checkpoint().owner
        chunk = b"x" * BODY_CHUNK
        mono = 10_100_000
        for _ in range(full_chunks):
            mono += 1
            event = next_event(state, owner, "BODY", {"body_chunk": chunk, "body_mono": mono})
            t = step(state, event)
            assert t.accepted is True
            state = t.state
        assert state.phase == "RECEIVING"
        assert state.delivered_bytes == BODY_CAP

        mono += 1
        final_event = next_event(state, owner, "BODY", {"body_chunk": b"y", "body_mono": mono})
        t = step(state, final_event)
        assert t.accepted is True
        assert t.state.phase == "UNCERTAIN_HELD"
        assert t.state.poisoned is True
        assert "BODY_OVERDELIVERY" in t.state.reasons
        # The full overdelivered total is counted internally, not clipped.
        assert t.state.delivered_bytes == BODY_CAP + 1
        # The full overdelivered amount is held outstanding, not clipped
        # back to the stage cap: used_body_bytes + outstanding_body_bytes
        # must always cover the exact known-delivered total, with no refund.
        assert t.state.reserved_body_bytes == BODY_CAP + 1
        res = result(t.state)
        assert res.used_body_bytes == 0  # ACCOUNT_ACK never ran
        assert res.outstanding_body_bytes == BODY_CAP + 1

    def test_header_split_vs_single_chunk_body_produce_identical_outcome_and_charges(self):
        single, _ = drive(good_inputs(), genesis_checkpoint(), HAPPY_TAGS, cfg={"body": b"abc"})

        state, transitions = drive(good_inputs(), genesis_checkpoint(), HAPPY_TAGS[:6],
                                    cfg={"body": b"abc"})
        assert state.phase == "RECEIVING"
        owner = genesis_checkpoint().owner
        t1 = step(state, next_event(state, owner, "BODY", {"body_chunk": b"ab", "body_mono": 10_100_000}))
        assert t1.accepted
        t2 = step(t1.state, next_event(t1.state, owner, "BODY", {"body_chunk": b"c", "body_mono": 10_150_000}))
        assert t2.accepted
        t3 = step(t2.state, next_event(t2.state, owner, "CLOSE_ACK", {"close_mono": 10_200_000}))
        assert t3.accepted
        t4 = step(t3.state, next_event(t3.state, owner, "ACCOUNT_ACK"))
        assert t4.accepted
        t5 = step(t4.state, next_event(t4.state, owner, "SEAL_ACK", {"seal_mono": 10_300_000}))
        assert t5.accepted
        assert t5.state.phase == single.phase == "RETAINED_UNQUALIFIED"
        assert result(t5.state).used_body_bytes == result(single).used_body_bytes == 3


# =============================================================================
# P09 - clock edges at START/body/close/seal.
# =============================================================================

class TestP09ClockEdges:
    def test_start_measured_utc_before_window_lo_refuses_event(self):
        clock = ClockObservation("2026-10-02T09:59:59.999999Z", 1e-7, 10.0, True)
        state, transitions = drive(good_inputs(), genesis_checkpoint(),
                                    ("LOCKS", "INTENT_ACK", "RESERVE_ACK", "START"),
                                    cfg={"clock": clock})
        assert transitions[-1].accepted is False
        assert transitions[-1].reason == "INVALID_EVENT"
        assert state.phase == "RESERVED"  # unchanged: the bad event never applied

    def test_start_measured_utc_at_or_after_window_hi_refuses_event(self):
        clock = ClockObservation("2026-10-02T13:29:59.999999Z", 1e-6, 10.0, True)
        state, transitions = drive(good_inputs(), genesis_checkpoint(),
                                    ("LOCKS", "INTENT_ACK", "RESERVE_ACK", "START"),
                                    cfg={"clock": clock})
        assert transitions[-1].accepted is False

    @pytest.mark.parametrize("uncertainty", [1.0000001, 2.0])
    def test_start_excessive_uncertainty_refuses_event(self, uncertainty):
        clock = ClockObservation("2026-10-02T10:05:00Z", uncertainty, 10.0, True)
        state, transitions = drive(good_inputs(), genesis_checkpoint(),
                                    ("LOCKS", "INTENT_ACK", "RESERVE_ACK", "START"),
                                    cfg={"clock": clock})
        assert transitions[-1].accepted is False

    @pytest.mark.parametrize("age", [60.0000001, 61.0])
    def test_start_stale_calibration_refuses_event(self, age):
        clock = ClockObservation("2026-10-02T10:05:00Z", 0.3, age, True)
        state, transitions = drive(good_inputs(), genesis_checkpoint(),
                                    ("LOCKS", "INTENT_ACK", "RESERVE_ACK", "START"),
                                    cfg={"clock": clock})
        assert transitions[-1].accepted is False

    @pytest.mark.parametrize("bad", [float("nan"), float("-inf"), True])
    def test_start_nonfinite_or_bool_uncertainty_refuses_without_crashing(self, bad):
        clock = ClockObservation("2026-10-02T10:05:00Z", bad, 10.0, True)
        state, transitions = drive(good_inputs(), genesis_checkpoint(),
                                    ("LOCKS", "INTENT_ACK", "RESERVE_ACK", "START"),
                                    cfg={"clock": clock})
        assert transitions[-1].accepted is False

    def test_start_malformed_minute_offset_refuses(self):
        clock = ClockObservation("2026-10-02T11:00:00+00:60", 0.0, 10.0, True)
        state, transitions = drive(good_inputs(), genesis_checkpoint(),
                                    ("LOCKS", "INTENT_ACK", "RESERVE_ACK", "START"),
                                    cfg={"clock": clock})
        assert transitions[-1].accepted is False

    def test_start_nonmonotonic_clock_refuses(self):
        clock = ClockObservation("2026-10-02T10:05:00Z", 0.3, 10.0, False)
        state, transitions = drive(good_inputs(), genesis_checkpoint(),
                                    ("LOCKS", "INTENT_ACK", "RESERVE_ACK", "START"),
                                    cfg={"clock": clock})
        assert transitions[-1].accepted is False

    def test_monotonic_reversal_between_events_holds(self):
        state, transitions = drive(good_inputs(), genesis_checkpoint(), HAPPY_TAGS[:6])
        assert state.phase == "RECEIVING"
        owner = genesis_checkpoint().owner
        backwards = next_event(state, owner, "BODY", {"body_mono": 10_000_000 - 1})
        t = step(state, backwards)
        assert t.accepted is True
        assert t.state.phase == "UNCERTAIN_HELD"
        assert "CLOCK_OR_BOOT_CHANGE" in t.state.reasons

    def test_boot_change_between_events_holds(self):
        state, transitions = drive(good_inputs(), genesis_checkpoint(), HAPPY_TAGS[:6])
        assert state.phase == "RECEIVING"
        owner = genesis_checkpoint().owner
        changed_boot = next_event(state, owner, "BODY", {"boot": "synthetic://boot/different"})
        t = step(state, changed_boot)
        assert t.accepted is True
        assert t.state.phase == "UNCERTAIN_HELD"
        assert "CLOCK_OR_BOOT_CHANGE" in t.state.reasons

    @pytest.mark.parametrize("close_delta,expect_held", [
        (30_000_000, False), (30_000_001, True),
    ])
    def test_close_deadline_30s_boundary(self, close_delta, expect_held):
        state, _ = drive(good_inputs(), genesis_checkpoint(), HAPPY_TAGS[:8],
                          cfg={"close_mono": 10_000_000 + close_delta})
        assert (state.phase == "UNCERTAIN_HELD") == expect_held
        if expect_held:
            assert "DEADLINE_EXCEEDED" in state.reasons
        else:
            assert state.phase == "CLOSED"

    @pytest.mark.parametrize("seal_delta,expect_held", [
        (60_000_000, False), (60_000_001, True),
    ])
    def test_seal_deadline_60s_boundary(self, seal_delta, expect_held):
        state, _ = drive(good_inputs(), genesis_checkpoint(), HAPPY_TAGS,
                          cfg={"seal_mono": 10_000_000 + seal_delta})
        assert (state.phase == "UNCERTAIN_HELD") == expect_held
        if expect_held:
            assert "DEADLINE_EXCEEDED" in state.reasons
        else:
            assert state.phase == "RETAINED_UNQUALIFIED"


# =============================================================================
# P10 - physical reservation boundaries actually modeled by this slice.
# =============================================================================

class TestP10PhysicalReservation:
    def test_resource_observation_models_only_the_three_dimensions_in_scope(self):
        # Journal/record/descriptor/CPU/RSS/address-space/wall/diagnostic
        # limits remain real enforcement outside this pure synthetic model
        # (handoff section 5); confirm the dataclass does not silently claim
        # to model them.
        field_names = {f.name for f in dataclasses.fields(ResourceObservation)}
        assert field_names == {
            "free_disk_bytes_after_reservation",
            "mem_available_bytes_after_reservation",
            "physically_reserved_bytes",
        }

    @pytest.mark.parametrize("reserved,expect_ok", [
        (67_108_864 - 1, False), (67_108_864, True),
    ])
    def test_physically_reserved_bytes_boundary(self, reserved, expect_ok):
        resources = ResourceObservation(3_300_000_000, 600_000_000, reserved)
        state, transitions = drive(good_inputs(), genesis_checkpoint(),
                                    ("LOCKS", "INTENT_ACK", "RESERVE_ACK", "START"),
                                    cfg={"resources": resources})
        assert transitions[-1].accepted is expect_ok

    @pytest.mark.parametrize("free_disk,expect_ok", [
        (2_147_483_648 - 1, False), (2_147_483_648, True),
    ])
    def test_free_disk_floor_boundary(self, free_disk, expect_ok):
        resources = ResourceObservation(free_disk, 600_000_000, 67_108_864)
        state, transitions = drive(good_inputs(), genesis_checkpoint(),
                                    ("LOCKS", "INTENT_ACK", "RESERVE_ACK", "START"),
                                    cfg={"resources": resources})
        assert transitions[-1].accepted is expect_ok

    @pytest.mark.parametrize("mem,expect_ok", [
        (536_870_912 - 1, False), (536_870_912, True),
    ])
    def test_memory_floor_boundary(self, mem, expect_ok):
        resources = ResourceObservation(3_300_000_000, mem, 67_108_864)
        state, transitions = drive(good_inputs(), genesis_checkpoint(),
                                    ("LOCKS", "INTENT_ACK", "RESERVE_ACK", "START"),
                                    cfg={"resources": resources})
        assert transitions[-1].accepted is expect_ok

    def test_injected_resource_breach_fault_holds_with_report_reservation_preserved(self):
        state, _ = drive(good_inputs(), genesis_checkpoint(),
                          ("LOCKS", "INTENT_ACK", "RESERVE_ACK", "START", "FAULT"),
                          cfg={"fault_kind": "RESOURCE_BREACH"})
        assert state.phase == "UNCERTAIN_HELD"
        assert state.report_reserved is True


# =============================================================================
# P11 - state/event matrix, tampered/oversized scripts, immutability, replay.
# =============================================================================

class TestP11StateMachineIntegrity:
    ALLOWED = {
        "ADMITTED": {"LOCKS", "FAULT"}, "LOCKED": {"INTENT_ACK", "FAULT"},
        "INTENT_RECORDED": {"RESERVE_ACK", "FAULT"}, "RESERVED": {"START", "FAULT"},
        "STARTED": {"STATUS", "FAULT"},
        "RECEIVING": {"HEADERS", "BODY", "CLOSE_ACK", "FAULT"},
        "CLOSED": {"ACCOUNT_ACK", "FAULT"}, "ACCOUNTED": {"SEAL_ACK", "FAULT"},
    }
    REACH_TAGS = {
        "ADMITTED": (),
        "LOCKED": ("LOCKS",),
        "INTENT_RECORDED": ("LOCKS", "INTENT_ACK"),
        "RESERVED": ("LOCKS", "INTENT_ACK", "RESERVE_ACK"),
        "STARTED": ("LOCKS", "INTENT_ACK", "RESERVE_ACK", "START"),
        "RECEIVING": ("LOCKS", "INTENT_ACK", "RESERVE_ACK", "START", "STATUS"),
        "CLOSED": ("LOCKS", "INTENT_ACK", "RESERVE_ACK", "START", "STATUS",
                   "HEADERS", "BODY", "CLOSE_ACK"),
        "ACCOUNTED": ("LOCKS", "INTENT_ACK", "RESERVE_ACK", "START", "STATUS",
                      "HEADERS", "BODY", "CLOSE_ACK", "ACCOUNT_ACK"),
    }

    @pytest.mark.parametrize("phase", sorted(ALLOWED))
    def test_only_the_documented_tags_are_accepted_transitions_for_each_phase(self, phase):
        owner = genesis_checkpoint().owner
        for tag in TAGS:
            state, _ = drive(good_inputs(), genesis_checkpoint(), self.REACH_TAGS[phase])
            assert state.phase == phase
            event = next_event(state, owner, tag) if tag != "FAULT" else \
                next_event(state, owner, "FAULT", {"fault_kind": "CRASH"})
            t = step(state, event)
            if tag in self.ALLOWED[phase]:
                assert t.reason != "OUT_OF_ORDER_EVENT"
            else:
                assert t.accepted is False
                assert t.reason == "OUT_OF_ORDER_EVENT"

    def test_terminal_phases_refuse_every_event_as_terminal_state(self):
        state, _ = drive_happy_path()
        owner = genesis_checkpoint().owner
        for tag in TAGS:
            event = next_event(state, owner, tag)
            t = step(state, event)
            assert t.accepted is False
            assert t.reason == "TERMINAL_STATE"

    def test_duplicate_sequence_number_refuses(self):
        state, _ = drive(good_inputs(), genesis_checkpoint(), ("LOCKS",))
        owner = genesis_checkpoint().owner
        replay = {**next_event(state, owner, "INTENT_ACK"), "seq": 1}  # already-used seq
        t = step(state, replay)
        assert t.accepted is False
        assert t.reason == "SEQUENCE_OWNER_OR_HEAD_MISMATCH"

    def test_unknown_tag_refuses_as_invalid_event(self):
        state, _ = drive(good_inputs(), genesis_checkpoint(), ())
        event = {"tag": "BOGUS", "seq": 1, "owner": genesis_checkpoint().owner, "head": state.head}
        t = step(state, event)
        assert t.accepted is False
        assert t.reason == "INVALID_EVENT"

    def test_headers_as_list_instead_of_tuple_refuses_no_implicit_coercion(self):
        state, _ = drive(good_inputs(), genesis_checkpoint(), HAPPY_TAGS[:5])
        owner = genesis_checkpoint().owner
        event = {**next_event(state, owner, "HEADERS"),
                 "headers": [(b"Content-Type", b"text/plain"), (b"Content-Length", b"3")]}
        t = step(state, event)
        assert t.accepted is False
        assert t.reason == "INVALID_EVENT"

    def test_checkpoint_is_frozen_immutable(self):
        cp = genesis_checkpoint()
        with pytest.raises(dataclasses.FrozenInstanceError):
            cp.holds = ("synthetic://x",)

    def test_oversized_script_length_refuses_before_admission(self):
        events = tuple({"tag": "FAULT", "seq": i + 1, "owner": "synthetic://o", "head": HEAD0,
                         "kind": "CRASH"} for i in range(EVENT_CAP + 1))
        res = run_synthetic(good_inputs(), genesis_checkpoint(), events)
        assert res.attempt_state == "REFUSED_BEFORE_DISPATCH"
        assert "INVALID_SCRIPT" in res.reasons

    def test_event_cap_exact_is_accepted_by_the_length_gate(self):
        checkpoint = genesis_checkpoint()
        events = tuple({"tag": "FAULT", "seq": i + 1, "owner": checkpoint.owner, "head": HEAD0,
                         "kind": "CRASH"} for i in range(EVENT_CAP))
        res = run_synthetic(good_inputs(), checkpoint, events)
        # First FAULT refuses pre-dispatch (ADMITTED+FAULT); every further
        # event in the oversized tail is blocked by TERMINAL_STATE and
        # cannot reduce further charges/holds.
        assert res.attempt_state == "REFUSED_BEFORE_DISPATCH"
        assert res.starts == 0

    def test_oversized_aggregate_script_payload_refuses(self):
        chunk = b"x" * BODY_CHUNK
        one_event_size = BODY_CHUNK + 1024
        count = (SCRIPT_CAP // one_event_size) + 2
        events = tuple({
            "tag": "BODY", "seq": i + 1, "owner": "synthetic://o", "head": HEAD0,
            "data": chunk, "clock": GOOD_CLOCK, "monotonic_us": 1, "host": "synthetic://h",
            "boot": "synthetic://b",
        } for i in range(count))
        res = run_synthetic(good_inputs(), genesis_checkpoint(), events)
        assert res.attempt_state == "REFUSED_BEFORE_DISPATCH"
        assert "OVERSIZED_SCRIPT" in res.reasons

    def test_oversized_raw_input_refuses_at_admission(self):
        package_raw, restrictions_raw, protocol_raw, binding_raw = good_raws()
        huge = package_raw + b" " * 1_048_576
        state = admit_synthetic(good_inputs(raws=(huge, restrictions_raw, protocol_raw, binding_raw)),
                                 genesis_checkpoint())
        assert state.phase == "REFUSED_BEFORE_DISPATCH"
        assert "INVALID_OR_OVERSIZED_RAW" in state.reasons

    def test_deterministic_replay_two_independent_runs_match_exactly(self):
        result_a, _ = drive_happy_path()
        result_b, _ = drive_happy_path()
        assert result(result_a) == result(result_b)


# =============================================================================
# P12 - import/run guards: no socket/DNS/subprocess/credential/filesystem
# writes in candidate code; result/promotion/type tests survive -O.
# =============================================================================

class TestP12ImportAndRunGuards:
    def test_candidate_module_source_has_no_forbidden_imports_or_calls(self):
        with open(ATTEMPT_MODEL_PATH, "r", encoding="utf-8") as fh:
            source = fh.read()
        tree = ast.parse(source, filename=ATTEMPT_MODEL_PATH)
        forbidden_modules = {
            "socket", "subprocess", "urllib", "http", "http.client", "requests",
            "ssl", "ftplib", "smtplib", "telnetlib", "os", "shutil",
        }
        forbidden_calls = {"open", "eval", "exec", "compile", "__import__"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name.split(".")[0] not in forbidden_modules, alias.name
            elif isinstance(node, ast.ImportFrom):
                assert (node.module or "").split(".")[0] not in (
                    forbidden_modules - {"tools"}
                ), node.module
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in forbidden_calls, node.func.id

    def test_running_the_full_happy_path_never_touches_socket_or_subprocess(self, monkeypatch):
        def deny_socket(*args, **kwargs):
            raise AssertionError("candidate code must never create a socket")

        def deny_subprocess(*args, **kwargs):
            raise AssertionError("candidate code must never spawn a subprocess")

        monkeypatch.setattr(socket.socket, "connect", deny_socket)
        monkeypatch.setattr(socket, "create_connection", deny_socket)
        monkeypatch.setattr(socket, "getaddrinfo", deny_socket)
        monkeypatch.setattr(subprocess, "Popen", deny_subprocess)
        monkeypatch.setattr(subprocess, "run", deny_subprocess)

        state, transitions = drive_happy_path()
        assert state.phase == "RETAINED_UNQUALIFIED"
        assert all(t.accepted for t in transitions)

    def test_no_private_evidence_path_or_real_provider_file_referenced(self):
        with open(ATTEMPT_MODEL_PATH, "r", encoding="utf-8") as fh:
            source = fh.read()
        assert "AlphaV11_Gate3EvidencePreflight" not in source
        assert "AlphaV11_R09Extrema" not in source
        assert "/home/" not in source

    def test_result_and_promotion_type_checks_survive_python_dash_o(self):
        code = (
            "import sys; sys.path.insert(0, '.');"
            "from tools.v11_gate3_preflight_attempt_model import ModelResult, SCHEMA;"
            "base = dict(schema=SCHEMA, synthetic=True, outcome='RETAINED_UNQUALIFIED',"
            " attempt_state='RETAINED_UNQUALIFIED', fingerprint='f', input_history_head='0'*64,"
            " output_history_head='0'*64, starts=1, used_attempts=1, used_body_bytes=3,"
            " used_time_us=1, outstanding_attempts=0, outstanding_body_bytes=0,"
            " outstanding_time_us=0, holds=(), reasons=(), refs=());"
            "ModelResult(**base);"
            "\ntry:\n"
            "    ModelResult(**{**base, 'execution_authority': True})\n"
            "    print('NO_RAISE')\n"
            "except ValueError:\n"
            "    print('RAISED')\n"
        )
        proc = subprocess.run(
            [sys.executable, "-O", "-c", code], cwd=".",
            capture_output=True, text=True, timeout=30,
        )
        assert proc.returncode == 0, proc.stderr
        assert "RAISED" in proc.stdout
        assert "NO_RAISE" not in proc.stdout


# =============================================================================
# R - repair-review probe tests for the H1-H4/M1-M9 findings recorded in the
# independent Opus/high review of commit b31bed0 (offline, synthetic-only;
# confers no execution/provider/capture authority or G3-L credit).
# =============================================================================

OWNER = genesis_checkpoint().owner
EXPIRED = ClockObservation("2026-10-02T13:30:00Z", 0, 10.0, True)


def _recv(cfg=None):
    s, _ = drive(good_inputs(), genesis_checkpoint(), HAPPY_TAGS[:6], cfg=cfg or {})
    assert s.phase == "RECEIVING"
    return s


def _script(n, patch=None):
    inputs, cp = good_inputs(), genesis_checkpoint()
    s = admit_synthetic(inputs, cp)
    ev = []
    for tag in HAPPY_TAGS[:n]:
        e = next_event(s, OWNER, tag)
        s = step(s, e).state
        ev.append(e)
    if patch:
        ev[patch[0]] = {**ev[patch[0]], **patch[1]}
    return run_synthetic(inputs, cp, tuple(ev))


class TestRRepairProbes:
    @pytest.mark.parametrize("kind", ["CRASH", "WRITE_FAILURE", "FSYNC_FAILURE", "JOURNAL_FULL"])
    def test_r1_intent_persistence_fault_holds_full_reservation(self, kind):
        s, _ = drive(good_inputs(), genesis_checkpoint(), ("LOCKS", "FAULT"), cfg={"fault_kind": kind})
        r = result(s)
        assert s.phase == "UNCERTAIN_HELD"
        assert (r.outstanding_attempts, r.outstanding_body_bytes, r.outstanding_time_us) == (1, BODY_CAP, STAGE_TIME)

    def test_r2_overdelivery_fully_counted_in_result(self):
        s, mono = _recv({"content_length": BODY_CAP}), 10_100_000
        for _ in range(BODY_CAP // BODY_CHUNK + 1):
            mono += 1
            t = step(s, next_event(s, OWNER, "BODY", {"body_chunk": b"x" * BODY_CHUNK, "body_mono": mono}))
            assert t.accepted
            s = t.state
        assert s.poisoned and s.delivered_bytes == BODY_CAP + BODY_CHUNK
        r = result(s)
        assert r.used_body_bytes + r.outstanding_body_bytes >= BODY_CAP + BODY_CHUNK

    def test_r3_recovery_conservative_and_bound(self):
        cp = genesis_checkpoint()
        base = {"schema": SCHEMA, "phase": "RESERVED", "head": cp.expected_history_head, "fingerprint": "a" * 64,
                "sequence": 3, "owner": cp.owner, "used_attempts": 0, "delivered_bytes": 0, "denials": []}
        r = result(recover_synthetic(json.dumps(base).encode(), cp).state)
        assert r.used_attempts + r.outstanding_attempts >= 1
        for bad in ({"fingerprint": ["x"]}, {"phase": "BOGUS"}, {"used_attempts": 2}):
            assert recover_synthetic(json.dumps({**base, **bad}).encode(), cp).accepted is False, bad
        for badcp in (genesis_checkpoint(external_history_head="1" * 64),
                      genesis_checkpoint(unfinished_intents=("synthetic://intent/x",))):
            assert recover_synthetic(json.dumps(base).encode(), badcp).accepted is False
        over = {**base, "used_attempts": 1, "delivered_bytes": BODY_CAP + BODY_CHUNK}
        r = result(recover_synthetic(json.dumps(over).encode(), cp).state)
        assert r.used_body_bytes + r.outstanding_body_bytes >= BODY_CAP + BODY_CHUNK

    def test_r4a_expired_body_clock_holds(self):
        s = _recv()
        t = step(s, next_event(s, OWNER, "BODY", {"body_clock": EXPIRED}))
        assert t.accepted and t.state.phase == "UNCERTAIN_HELD"

    def test_r4b_rejected_expiry_then_backdated_clock_cannot_succeed(self):
        s = _recv({"clock": ClockObservation("2026-10-02T13:29:00Z", 0, 10.0, True)})
        t = step(s, next_event(s, OWNER, "BODY", {"body_clock": EXPIRED}))
        s = t.state if t.accepted else s
        for tag in ("BODY", "CLOSE_ACK", "ACCOUNT_ACK", "SEAL_ACK"):
            t = step(s, next_event(s, OWNER, tag))
            s = t.state
            if not t.accepted:
                break
        assert s.phase != "RETAINED_UNQUALIFIED"

    def test_r4c_run_synthetic_post_start_clock_failure_is_held_not_predispatch(self):
        r = _script(10, (6, {"clock": EXPIRED}))
        assert r.outcome == "UNCERTAIN_HELD" and r.starts == 1

    def test_r5_attempt_conservation_after_start(self):
        s, _ = drive(good_inputs(), genesis_checkpoint(), HAPPY_TAGS[:4])
        r = result(s)
        assert r.used_attempts + r.outstanding_attempts == 1

    def test_r6_head_binds_event_content(self):
        a, _ = drive_happy_path()
        b, _ = drive_happy_path(cfg={"status": 503})
        assert a.phase != b.phase and a.head != b.head

    def test_r7_forged_unadmitted_state_cannot_start(self):
        cp = genesis_checkpoint()
        f = ModelState("RESERVED", "x", 0, HEAD0, HEAD0, cp.owner, cp)
        t = step(f, next_event(f, cp.owner, "START"))
        assert not (t.accepted and t.state.phase == "STARTED")

    @pytest.mark.parametrize("ev", [{"tag": []}, {"tag": {}}, "LOCKS", None])
    def test_r8_malformed_event_never_raises(self, ev):
        s = admit_synthetic(good_inputs(), genesis_checkpoint())
        assert step(s, ev).accepted is False
        assert run_synthetic(good_inputs(), genesis_checkpoint(), (ev,)).outcome == "REFUSED_BEFORE_DISPATCH"

    @pytest.mark.parametrize("depth", [900, 990, 1400, 3000, 20000])
    def test_r8b_deep_json_never_raises(self, depth):
        i = good_inputs()
        raw = b"[" * depth + b"]" * depth
        assert admit_synthetic(dataclasses.replace(i, package_raw=raw), genesis_checkpoint()).phase == "REFUSED_BEFORE_DISPATCH"

    @pytest.mark.parametrize("bad", [{"attempt_state": "G3L_PASS"}, {"parse_absence_reason": "PARSED"},
        {"decode_absence_reason": "DECODED"}, {"qualification_credit": False}, {"qualification_credit": 0.0},
        {"fingerprint": None}, {"outcome": "RECEIVING"}])
    def test_r9_result_constants_closed(self, bad):
        res = result(drive_happy_path()[0])
        with pytest.raises(ValueError):
            dataclasses.replace(res, **bad)

    def test_r10_truncated_script_is_uncertain_held(self):
        assert _script(5).outcome == "UNCERTAIN_HELD"

    def test_r11_header_value_control_bytes_invalid(self):
        s, _ = drive_happy_path(cfg={"extra_headers": ((b"ETag", b'"a"\r\nX-Injected: 1'),)})
        assert s.phase == "RETAINED_INVALID"

    @pytest.mark.parametrize("extra,ok", [
        (tuple((b"X-H%02d" % i, b"v") for i in range(30)), True),
        (tuple((b"X-H%02d" % i, b"v") for i in range(31)), False),
        (((b"N" * 64, b"v"),), True), (((b"N" * 65, b"v"),), False),
        (((b"X-V", b"v" * 1024),), True), (((b"X-V", b"v" * 1025),), False),
        (((b"X-A", b"a" * 1024), (b"X-B", b"b" * 1024), (b"X-C", b"c" * 1024), (b"X-D", b"d" * 975)), True),
        (((b"X-A", b"a" * 1024), (b"X-B", b"b" * 1024), (b"X-C", b"c" * 1024), (b"X-D", b"d" * 976)), False)])
    def test_r12_header_limits(self, extra, ok):
        s, _ = drive_happy_path(cfg={"extra_headers": extra})
        assert (s.phase == "RETAINED_UNQUALIFIED") is ok

    def test_r13_body_after_30s_close_deadline_holds(self):
        s = _recv()
        t = step(s, next_event(s, OWNER, "BODY", {"body_mono": 10_000_000 + 30_000_001}))
        assert t.state.phase == "UNCERTAIN_HELD"

    def test_r14_utc_vs_monotonic_divergence_holds(self):
        s = _recv()
        jump = ClockObservation("2026-10-02T13:00:00Z", 0, 10.0, True)
        assert step(s, next_event(s, OWNER, "BODY", {"body_clock": jump})).state.phase == "UNCERTAIN_HELD"

    @pytest.mark.parametrize("v", ["synthetic://", "synthetic:///etc/passwd", "synthetic://a/%2e%2e/b"])
    def test_r15_refs_reject_absolute_empty_encoded(self, v):
        assert not _safe_ref(v)


# =============================================================================
# R2 - regression probes for the independent Opus/high review of this
# repair's own commit (`4bfdf3d`), which returned CHANGES_REQUIRED against
# findings H-1, H-2, M-1, M-2 and M-4 below (offline, synthetic-only;
# confers no execution/provider/capture authority or G3-L credit).
# =============================================================================

STALE_CALIBRATION_CLOCK = ClockObservation("2026-10-02T10:05:00Z", 0.3, 61.0, True)
HIGH_UNCERTAINTY_CLOCK = ClockObservation("2026-10-02T10:05:00Z", 2.0, 10.0, True)
MONOTONIC_INCONSISTENT_CLOCK = ClockObservation("2026-10-02T10:05:00Z", 0.3, 10.0, False)


class TestR2RepairProbes:
    @pytest.mark.parametrize("bad_clock", [STALE_CALIBRATION_CLOCK, HIGH_UNCERTAINTY_CLOCK,
                                            MONOTONIC_INCONSISTENT_CLOCK])
    def test_h1_post_start_clock_failure_holds_not_stuck_or_predispatch(self, bad_clock):
        # Direct step: a structurally bad post-START clock must hold the
        # attempt (charged), not reject the event and leave the state stuck
        # in a retryable non-terminal phase.
        s = _recv()
        t = step(s, next_event(s, OWNER, "BODY", {"body_clock": bad_clock}))
        assert t.accepted is True
        assert t.state.phase == "UNCERTAIN_HELD"
        assert "CLOCK_FAILURE" in t.state.reasons
        # A retry with a good clock must not reach a clean outcome: the
        # state is now terminal (UNCERTAIN_HELD), so every further event is
        # rejected outright.
        retry = step(t.state, next_event(t.state, OWNER, "BODY"))
        assert retry.accepted is False
        assert retry.state.phase == "UNCERTAIN_HELD"

        # run_synthetic: the same failure must not escape as a zero-charge
        # pre-dispatch refusal.
        r = _script(10, (6, {"clock": bad_clock}))
        assert r.outcome == "UNCERTAIN_HELD"
        assert r.starts == 1
        assert (r.used_attempts, r.outstanding_attempts) == (1, 0)
        assert r.outstanding_body_bytes == BODY_CAP and r.outstanding_time_us == STAGE_TIME

    def test_h2_sealed_snapshot_replay_rejects_phase_inconsistent_as_tampered(self):
        cp = genesis_checkpoint()
        base = {"schema": SCHEMA, "head": cp.expected_history_head, "fingerprint": "a" * 64,
                "sequence": 9, "owner": cp.owner, "denials": []}
        # A RETAINED_UNQUALIFIED outcome can never be sealed without having
        # actually started (used_attempts must be 1), and never with more
        # than BODY_CAP delivered (the real machine holds before sealing).
        for bad in (
            {**base, "phase": "RETAINED_UNQUALIFIED", "used_attempts": 0, "delivered_bytes": 0},
            {**base, "phase": "RETAINED_UNQUALIFIED", "used_attempts": 1, "delivered_bytes": BODY_CAP + 1},
            {**base, "phase": "REFUSED_BEFORE_DISPATCH", "used_attempts": 1, "delivered_bytes": 5_000_000},
            # step's SEAL_ACK branch picks DENIED_HELD exactly when denials
            # are non-empty and RETAINED_* exactly when they are empty: the
            # other combination is an outcome the live machine never makes.
            {**base, "phase": "RETAINED_UNQUALIFIED", "used_attempts": 1, "delivered_bytes": 3,
             "denials": ["synthetic://denial/status-403"]},
            {**base, "phase": "DENIED_HELD", "used_attempts": 1, "delivered_bytes": 3, "denials": []},
        ):
            t = recover_synthetic(json.dumps(bad).encode(), cp)
            assert t.accepted is False
            assert t.state.phase == "REFUSED_BEFORE_DISPATCH"
            assert "TAMPERED_SNAPSHOT" in t.state.reasons

    def test_h2_sealed_snapshot_replay_allows_denied_held_with_denials(self):
        cp = genesis_checkpoint()
        good = {"schema": SCHEMA, "phase": "DENIED_HELD", "head": cp.expected_history_head,
                "fingerprint": "a" * 64, "sequence": 9, "owner": cp.owner,
                "used_attempts": 1, "delivered_bytes": 3, "denials": ["synthetic://denial/status-403"]}
        t = recover_synthetic(json.dumps(good).encode(), cp)
        assert t.accepted is True
        assert t.state.phase == "DENIED_HELD"

    def test_h2_sealed_snapshot_replay_charges_full_stage_time(self):
        cp = genesis_checkpoint()
        good = {"schema": SCHEMA, "phase": "RETAINED_UNQUALIFIED", "head": cp.expected_history_head,
                "fingerprint": "a" * 64, "sequence": 9, "owner": cp.owner,
                "used_attempts": 1, "delivered_bytes": 3, "denials": []}
        t = recover_synthetic(json.dumps(good).encode(), cp)
        assert t.accepted is True
        r = result(t.state)
        assert r.used_time_us == STAGE_TIME
        assert r.used_attempts == 1

    def test_m1_early_end_at_locked_holds_full_reservation_like_fault(self):
        inputs, cp = good_inputs(), genesis_checkpoint()
        s = admit_synthetic(inputs, cp)
        locks_event = next_event(s, OWNER, "LOCKS")
        r = run_synthetic(inputs, cp, (locks_event,))
        assert r.outcome == "UNCERTAIN_HELD"
        assert (r.outstanding_attempts, r.outstanding_body_bytes, r.outstanding_time_us) == (1, BODY_CAP, STAGE_TIME)
        assert "SCRIPT_ENDED_EARLY" in r.reasons

    def test_m1_rejected_event_at_locked_also_holds_full_reservation(self):
        inputs, cp = good_inputs(), genesis_checkpoint()
        s = admit_synthetic(inputs, cp)
        locks_event = next_event(s, OWNER, "LOCKS")
        bad_intent = {**next_event(step(s, locks_event).state, OWNER, "INTENT_ACK"), "seq": 999}
        r = run_synthetic(inputs, cp, (locks_event, bad_intent))
        assert r.outcome == "UNCERTAIN_HELD"
        assert (r.outstanding_attempts, r.outstanding_body_bytes, r.outstanding_time_us) == (1, BODY_CAP, STAGE_TIME)
        assert "SCRIPT_ENDED_EARLY" in r.reasons
        assert "SEQUENCE_OWNER_OR_HEAD_MISMATCH" in r.reasons

    def test_m2_forged_receiving_state_cannot_settle(self):
        s = _recv()
        forged = dataclasses.replace(s, starts=0, used_attempts=0, start_us=None)
        t = step(forged, next_event(forged, OWNER, "CLOSE_ACK"))
        assert t.accepted is False
        assert t.reason == "FORGED_OR_INCONSISTENT_STATE"

    def test_m2_forged_unreconciled_checkpoint_cannot_start(self):
        cp = genesis_checkpoint(unfinished_intents=("synthetic://intent/x",))
        forged = ModelState("RESERVED", "a" * 64, 2, HEAD0, HEAD0, cp.owner, cp,
                             reserved_attempts=1, reserved_body_bytes=BODY_CAP, reserved_time_us=STAGE_TIME,
                             refs=(("intent", "synthetic://intent/p01"), ("reservation", "synthetic://reservation/p01")))
        t = step(forged, next_event(forged, cp.owner, "START"))
        assert not (t.accepted and t.state.phase == "STARTED")

    def test_m4_field_invalid_checkpoint_never_raises(self):
        bad_cp = genesis_checkpoint(owner="bad")
        r = run_synthetic(good_inputs(), bad_cp, ())
        assert r.outcome == "REFUSED_BEFORE_DISPATCH"
        assert "INVALID_CHECKPOINT" in r.reasons

    def test_m4_counter_overflow_saturates_instead_of_raising(self):
        from tools.v11_gate3_preflight_attempt_model import MAX
        cp = genesis_checkpoint(outstanding_body_bytes=MAX)
        # Exercise result()'s saturation path: a reservation on top of an
        # already-near-ceiling checkpoint must saturate, not raise.
        inputs = good_inputs()
        s = admit_synthetic(inputs, cp)
        ev = []
        for tag in ("LOCKS", "INTENT_ACK"):
            e = next_event(s, OWNER, tag)
            s = step(s, e).state
            ev.append(e)
        out = run_synthetic(inputs, cp, tuple(ev))
        assert out.outstanding_body_bytes == MAX
        assert "COUNTER_SATURATED" in out.reasons
