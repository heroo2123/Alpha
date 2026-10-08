"""Adverse controls for the date-independent, zero-credit local packet."""

import json
from pathlib import Path

import pytest

from tools import v11_r09_gate3_g3l_identity_audit as audit
from tools import v11_r09_gate3_g3l_retained_packet as packet
from tools.v11_r09_gate3_g3l_prep import ALL_IDS, PRE_REVIEW_IDS

REPO = Path(__file__).resolve().parents[1]


def altered_packet(tmp_path, change):
    value = json.loads((REPO / packet.PACKET).read_bytes())
    change(value)
    path = tmp_path / "packet.json"
    path.write_text(json.dumps(value))
    return path


def test_exact_packet_is_offline_date_independent_and_zero_credit():
    result = packet.verify(REPO)
    assert result == {"candidate_count": 6, "pre_review_missing": 77,
                      "other_pre_review_missing": 71, "qualification_credit": 0,
                      "launchable": False, "g3l": "NO-GO"}
    value = json.loads((REPO / packet.PACKET).read_bytes())
    assert value["target_date"] is None and value["selected_cohort"] is None
    assert set(value["qualified_entries"]) == set(ALL_IDS)
    assert all(value["qualified_entries"][item] is None for item in PRE_REVIEW_IDS)
    assert set(value["candidates"]) == set(packet.ROWS)


def test_candidate_set_matches_current_identity_audit_classification():
    # The audit's required date/resource values are fixed historical test
    # inputs. They are discarded and never become a packet target or cohort.
    result = audit.audit(REPO, target_date="2026-10-04", now_utc=1790977200,
                         free_disk_bytes=3_000_000_000,
                         available_memory_bytes=1_000_000_000)
    candidates = {identity for identity, row in result["identities"].items()
                  if row["category"] in (audit.RETAINED_SCOPED, audit.OFFLINE)}
    assert candidates == set(packet.ROWS)
    assert result["category_counts"][audit.FUTURE] == 71
    assert result["screen"]["missing_before"] == 77
    assert all(row["qualified_entry"] is None for row in result["identities"].values())


@pytest.mark.parametrize("field,value", [
    ("launchable", True), ("qualification_credit", 1),
    ("provider_request_authority", True), ("target_date", "2026-10-09"),
    ("selected_cohort", {"station": "invented"}),
])
def test_authority_or_date_claim_is_rejected(tmp_path, field, value):
    path = altered_packet(tmp_path, lambda doc: doc.__setitem__(field, value))
    with pytest.raises(ValueError, match="authority or a selected date/cohort"):
        packet.verify(REPO, path)


def test_any_qualified_entry_is_rejected(tmp_path):
    path = altered_packet(tmp_path, lambda doc: doc["qualified_entries"].__setitem__(
        "protocol.g3p_original_review_terminal", {"invented": "credit"}))
    with pytest.raises(ValueError, match="all remain null"):
        packet.verify(REPO, path)


def test_missing_terminal_is_rejected(tmp_path, monkeypatch):
    terminal = "docs/V11_R09_GATE3_PROTOCOL_REVIEW_117830a_terminal.json"
    actual = audit._evidence_bytes

    def missing(repo, path, cache):
        if path == terminal:
            raise ValueError("unreadable evidence file: " + path)
        return actual(repo, path, cache)

    monkeypatch.setattr(audit, "_evidence_bytes", missing)
    with pytest.raises(ValueError, match="unreadable evidence file"):
        packet.verify(REPO)


def test_different_original_bytes_are_rejected(monkeypatch):
    source = "docs/V11_R09_GATE3_COLLECTION_PROTOCOL.md"
    actual = audit._evidence_bytes

    def changed(repo, path, cache):
        data = actual(repo, path, cache)
        return data + b"\n" if path == source else data

    monkeypatch.setattr(audit, "_evidence_bytes", changed)
    with pytest.raises(ValueError, match="original bytes changed"):
        packet.verify(REPO)


def test_different_terminal_bytes_are_rejected(monkeypatch):
    source = "docs/V11_R09_GATE3_COMPOSITION_REVIEW_0b7209d_terminal.json"
    actual = audit._evidence_bytes

    def changed(repo, path, cache):
        data = actual(repo, path, cache)
        return data.replace(b"SCOPED_PASS_OFFLINE_COMPOSITION", b"FAIL") if path == source else data

    monkeypatch.setattr(audit, "_evidence_bytes", changed)
    with pytest.raises(ValueError, match="original bytes changed"):
        packet.verify(REPO)


def test_stale_terminal_identity_is_rejected_even_with_valid_json():
    path = "docs/V11_R09_GATE3_LAUNCH_CONTRACT_REVIEW_14c2413_terminal.json"
    terminal = json.loads((REPO / path).read_bytes())
    terminal["reviewed_commit"] = audit.RECONCILIATION_COMMIT
    report, _, _ = packet.TERMINALS[path]
    with pytest.raises(ValueError, match="different or incomplete review terminal"):
        packet._terminal(path, terminal, packet._sha((REPO / report).read_bytes()), REPO)


def test_missing_or_different_candidate_reference_is_rejected(tmp_path):
    identity = "protocol.transport_design_review_terminal"
    path = altered_packet(tmp_path, lambda doc: doc["candidates"][identity]["refs"].pop())
    with pytest.raises(ValueError, match="candidate changed"):
        packet.verify(REPO, path)
    path = altered_packet(tmp_path, lambda doc: doc["candidates"][identity]["refs"][0].__setitem__(
        "sha256", "0" * 64))
    with pytest.raises(ValueError, match="original bytes changed"):
        packet.verify(REPO, path)
