from dataclasses import replace
from pathlib import Path
import sqlite3

import pytest

from polymarket_scanner.v11.evidence import EvidenceError, EvidenceStore, Limits, ReleaseBinding
from polymarket_scanner.v11.measurement import executable_depth, measure_markout, score_binary


@pytest.fixture
def archive(tmp_path):
    root = tmp_path / "private"
    root.mkdir(mode=0o700)
    clock = [100.0]
    store = EvidenceStore(root / "v11.sqlite", "V11_PAPER", clock=lambda: clock[0])
    return store, clock


def capture(store, key="weather", **overrides):
    kwargs = dict(event_id="e1", kind="OFFICIAL_OBSERVATION", provider="official",
                  source_identity="station:day", revision="r1", payload={"temperature": 32},
                  observed_at=90.0, evidence_class="SYNTHETIC")
    kwargs.update(overrides)
    return store.capture(key, **kwargs)


def decision(store, **overrides):
    kwargs = dict(event_id="e1", strategy="measurement-v1",
                  binding=ReleaseBinding("1"*40,"2"*40,"3"*64,"4"*64,"5"*64),
                  evidence_ids=("weather",), feature_ready_at=100.0,
                  valuation_type="OBSERVATION_ONLY", target="NEXT_OFFICIAL_OBSERVATION",
                  outcome="GATED", reason="NO_SETTLEMENT_OR_EXIT_MODEL",
                  explanation={"temperature": 32}, expires_at=120.0)
    kwargs.update(overrides)
    return store.decision("decision", **kwargs)


def test_later_revision_and_label_never_rewrite_original_replay(archive):
    store, clock = archive
    first = capture(store)
    decision(store)
    clock[0] = 130
    capture(store,"revision",revision="r2",payload={"temperature":33},observed_at=90)
    capture(store,"label",kind="LABEL",payload={"settlement":0})
    assert [r["id"] for r in store.causal_inputs("e1",100)] == [first["id"]]
    replay = store.replay("decision",lambda inputs,binding: {"temperature":inputs[0]["body"]["payload"]["temperature"]})
    assert replay["matches"]
    assert replay["binding"]["bundle_sha256"] == "4"*64
    assert not store.replay("decision",lambda *_:{"temperature":33})["matches"]


def test_backfilled_old_sensor_time_is_not_early_arrival(archive):
    store, clock = archive
    clock[0] = 300
    capture(store,observed_at=50)
    assert store.causal_inputs("e1",200) == []


def test_namespace_is_not_a_shared_account_or_ledger(archive):
    store, _ = archive
    capture(store)
    with pytest.raises(EvidenceError,match="NAMESPACE_OR_VERSION"):
        EvidenceStore(store.path,"CHALLENGER:test")
    with pytest.raises(EvidenceError,match="NONFINANCIAL_NAMESPACE"):
        EvidenceStore(store.path,"LIVE")
    challenger = EvidenceStore(store.path.parent / "challenger.sqlite","CHALLENGER:test")
    with pytest.raises(EvidenceError,match="EVIDENCE_MISSING"):
        challenger.get("weather")


def test_existing_control_database_is_not_migrated_or_permission_changed(tmp_path):
    tmp_path.chmod(0o700)
    path=tmp_path/"control.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE weather_paper_positions(id INTEGER)")
    path.chmod(0o600)
    before=path.read_bytes()
    with pytest.raises(EvidenceError,match="FOREIGN_DATABASE"):
        EvidenceStore(path,"V11_PAPER")
    assert path.read_bytes()==before
    assert not Path(str(path)+"-wal").exists()


@pytest.mark.parametrize("change",[
    {"issued_at":101}, {"observed_at":float('nan')},
    {"payload":{"api_secret":"must never persist"}},
    {"payload":{"nested":{"private_key":"must never persist"}}},
    {"payload":{"value":float('inf')}}, {"evidence_class":"REAL_FILL"},
])
def test_bad_or_sensitive_capture_is_refused(archive,change):
    store,_=archive
    with pytest.raises(EvidenceError):
        capture(store,**change)
    assert store.causal_inputs("e1",1000)==[]


def test_duplicate_identity_conflict_and_append_only(archive):
    store,_=archive
    assert capture(store)==capture(store)
    with pytest.raises(EvidenceError,match="CONFLICT"):
        capture(store,payload={"temperature":33})
    with sqlite3.connect(store.path) as db:
        with pytest.raises(sqlite3.IntegrityError,match="APPEND_ONLY"):
            db.execute("DELETE FROM v11_records")


def test_archive_capacity_fails_without_erasing_previous_evidence(archive):
    store,_=archive
    store.limits=replace(store.limits,max_records=1)
    capture(store)
    with pytest.raises(EvidenceError,match="RECORD_LIMIT"):
        capture(store,"next")
    assert store.get("weather")["body"]["payload"]["temperature"]==32


def test_clock_regression_fails_without_backdating(archive):
    store,clock=archive
    capture(store)
    clock[0]=99
    with pytest.raises(EvidenceError,match="CLOCK_REGRESSION"):
        capture(store,"next")


@pytest.mark.parametrize("change",[
    {"event_id":"other"}, {"evidence_ids":("missing",)},
    {"feature_ready_at":99}, {"feature_ready_at":101},
    {"expires_at":100}, {"outcome":"ACCEPT_RESEARCH"},
    {"valuation_type":"CROSSING_AS_SETTLEMENT"},
    {"evidence_ids":("weather","weather")},
])
def test_noncausal_or_incompatible_decision_is_refused(archive,change):
    store,_=archive
    capture(store)
    with pytest.raises(EvidenceError):
        decision(store,**change)


def test_labels_and_unknown_historical_availability_are_not_features(archive):
    store,_=archive
    capture(store,"label",kind="LABEL")
    capture(store,"history",evidence_class="HISTORICAL_AVAILABILITY_UNKNOWN")
    assert store.causal_inputs("e1",200)==[]
    for key in ("label","history"):
        with pytest.raises(EvidenceError,match="NONCAUSAL"):
            decision(store,evidence_ids=(key,))


def test_causal_pagination_ignores_non_input_records(archive):
    store,clock=archive
    capture(store)
    decision(store)
    clock[0]=101
    capture(store,"second")
    first=store.causal_inputs("e1",200,limit=1)
    second=store.causal_inputs("e1",200,limit=1,after_seq=first[0]["seq"])
    assert [x["id"] for x in first+second]==["weather","second"]


def test_funnel_separates_no_opportunity_missing_data_and_failure(archive):
    store,_=archive
    for i,state in enumerate(("NO_OPPORTUNITY","NO_DATA","FAULT")):
        row=store.funnel(str(i),event_id="e1",strategy="future",stage="EVALUATED",
                         state=state,reason=state,cycle_id="one")
        assert row["body"]["state"]==state
    assert store.causal_inputs("e1",200)==[]


def book(store,key,*,units="20",fee=False,**overrides):
    kwargs=dict(kind="BOOK",provider="clob",observed_at=105,
                payload={"token_id":"yes","stream_healthy":True,
                         "bids":[{"price":"0.9","size":units}],
                         "asks":[{"price":"0.95","size":"20"}]})
    kwargs.update(overrides)
    return capture(store,key,**kwargs)


def test_confirmation_does_not_prove_exit_or_profit(archive):
    store,clock=archive
    capture(store)
    decision(store)
    clock[0]=105
    book(store,"exit",units="2")
    result=measure_markout(store,"decision",token_id="yes",units=10,
                           entry_cost_per_share=.5,horizon_seconds=5,
                           tolerance_seconds=1,as_of=105,fee_per_share=.01)
    assert result["reason"]=="INSUFFICIENT_EXIT_DEPTH"
    assert result["markout_per_share"] is None and result["trading_pnl"] is None


def test_size_dependent_bid_markout_and_missing_fees(archive):
    store,clock=archive
    capture(store)
    decision(store)
    clock[0]=105
    book(store,"exit")
    args=dict(token_id="yes",units=10,entry_cost_per_share=.5,horizon_seconds=5,
              tolerance_seconds=1,as_of=105)
    assert measure_markout(store,"decision",**args)["reason"]=="EXIT_FEES_UNKNOWN"
    measured=measure_markout(store,"decision",fee_per_share=.01,**args)
    assert measured["markout_per_share"]=="0.39"
    assert measured["evidence_class"]=="SYNTHETIC"


def test_stale_late_book_is_not_horizon_markout(archive):
    store,clock=archive
    capture(store)
    decision(store)
    clock[0]=105
    book(store,"stale",observed_at=80)
    assert measure_markout(store,"decision",token_id="yes",units=10,
                           entry_cost_per_share=.5,horizon_seconds=5,
                           tolerance_seconds=1,as_of=105)["status"]=="UNKNOWN"


def test_depth_cost_and_scoring_do_not_invent_real_execution():
    levels=[{"price":".6","size":"3"},{"price":".5","size":"2"}]
    depth=executable_depth(levels,4,direction="ACQUIRE",fee_per_share=".01")
    assert str(depth.gross_value)=="2.2" and str(depth.net_value)=="2.24"
    score=score_binary([.8,.8],[1,0],groups=["event","event"])
    assert score["n_declared_groups"]==1
    assert score["brier"]==pytest.approx(.34)
    assert score_binary([0.0],[1],groups=["e"])["log_loss_infinite"] is True
