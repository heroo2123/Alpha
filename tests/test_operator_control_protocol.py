"""No Telegram or exchange network calls; independent control-envelope boundaries."""
from dataclasses import asdict
import json
import sqlite3
import time

import pytest

from polymarket_scanner.production.config import ProductionConfig
from polymarket_scanner.production.control import ControlError, ControlStore, changed_settings, initial_settings
from test_production_lifecycle import config


def operator_config(tmp_path, mode="LIVE_EXECUTION", **changes):
    # Explicit isolated fixture choices; production templates supply no bankroll.
    old = config(tmp_path, mode=mode)
    from dataclasses import replace
    from polymarket_scanner.production.control import ControlPolicy
    policy = ControlPolicy(tmp_path / "signals" / "operator.db", tmp_path / "scanner" / "scanner.db",
        tmp_path / "scanner" / "status.json", "fixture-authorization-1", time.time()+3600,
        123, 42, (42,), ("SIGNALS", "CONFIRM", "AUTOMATIC") if mode=="LIVE_EXECUTION" else ("SIGNALS",),
        ("ONCHAIN_BOUND", "EXCHANGE_PUBLISHED_SCHEDULE") if mode=="LIVE_EXECUTION" else (),
        [[day,0,1440] for day in range(7)])
    return replace(old, operator_control=replace(policy, **changes))


@pytest.fixture
def control(tmp_path):
    cfg = operator_config(tmp_path)
    store = ControlStore(cfg)
    try:
        yield cfg, store
    finally:
        store.close()


def button(store, op="PAUSE", data=None, revision=0):
    action = store.action(42, op, data or {}, revision)
    store.bind_message([action], 7)
    return action


def click(store, action, **kwargs):
    return store.click(action, **dict({"actor":42,"message_id":7,"update_id":100,"revision":0}, **kwargs))


def test_confirmation_click_is_one_use_and_has_immutable_structured_body(control):
    cfg, store = control
    action = button(store, "CONFIRM_TRADE", {"signal_id":"s1","signal_hash":"abc"})
    result = click(store, action)
    assert result["data"] == {"signal_id":"s1","signal_hash":"abc"}
    assert result["config_sha256"] == cfg.config_sha256
    assert result["authorization_version"] == cfg.operator_control.authorization_version
    with pytest.raises(ControlError, match="ALREADY_USED"):
        click(store, action, update_id=101)
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM operator_requests").fetchone()[0] == 1
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE operator_requests SET body='{}'")


@pytest.mark.parametrize("override,reason", [({"actor":43},"ACTOR_OR_MESSAGE"),({"actor":True},"ACTOR_OR_MESSAGE"),
    ({"message_id":8},"ACTOR_OR_MESSAGE"),({"revision":1},"STATE_CHANGED"),({"update_id":True},"UPDATE_ID")])
def test_forged_actor_forwarded_message_and_stale_version_rejected(control, override, reason):
    cfg, store = control
    with pytest.raises(ControlError, match=reason):
        click(store, button(store), **override)
    assert store.state("safety_epoch") == "0"


def test_expired_button_and_new_pause_invalidate_resume(control, monkeypatch):
    cfg, store = control
    resume = button(store, "RESUME")
    click(store, button(store))
    with pytest.raises(ControlError, match="STATE_CHANGED"):
        click(store, resume, update_id=101)
    fresh = button(store)
    later=time.time()+121
    monkeypatch.setattr(time,"time",lambda:later)
    with pytest.raises(ControlError, match="EXPIRED"):
        click(store,fresh,update_id=102)


def test_out_of_order_update_cannot_be_reinterpreted_as_new_request(control):
    cfg, store = control
    click(store,button(store,"SET",{"key":"experience","value":"SIGNALS"}),update_id=101)
    with pytest.raises(ControlError,match="STALE_OR_REPLAYED"):
        click(store,button(store,"RESUME"),update_id=100)


def test_stop_and_request_commit_atomically(control):
    cfg, store=control
    action=button(store,"CANCEL")
    with store.connect() as db:
        db.execute("CREATE TRIGGER fail_control BEFORE INSERT ON operator_requests BEGIN SELECT RAISE(ABORT,'injected'); END")
    with pytest.raises(sqlite3.Error):
        click(store,action)
    assert store.state("safety_epoch")=="0"
    with store.connect() as db:
        assert db.execute("SELECT state FROM operator_actions WHERE id=?",(action,)).fetchone()[0]=="PREVIEW"


@pytest.mark.parametrize("key,value",[("capital","101"),("per_order","6"),("max_open_orders",11),
    ("activation_file","/tmp/evil"),("operator_user_ids",[43]),("mode","LIVE_EXECUTION"),
    ("credentials_file","bad"),("min_model_gap",".01")])
def test_mutable_settings_cannot_expand_protected_authority(control,key,value):
    cfg,store=control
    with pytest.raises(ControlError):
        changed_settings(cfg,initial_settings(cfg),key,value)


def test_risk_reductions_and_reincreases_never_exceed_original_ceiling(control):
    cfg,store=control
    old=initial_settings(cfg)
    reduced,grows=changed_settings(cfg,old,"per_order","2")
    assert not grows and reduced["revision"]==1 and old["risk"]["per_order"]=="5"
    restored,grows=changed_settings(cfg,reduced,"per_order","5")
    assert grows and restored["risk"]["per_order"]=="5"


def test_selecting_mode_always_leaves_openings_paused(control):
    cfg,store=control
    old=dict(initial_settings(cfg),paused=False)
    new,grows=changed_settings(cfg,old,"experience","AUTOMATIC")
    assert new["paused"] and grows


def test_signals_only_policy_does_not_gain_financial_mode(tmp_path):
    cfg=operator_config(tmp_path,mode="LIVE_SIGNALS")
    with pytest.raises(ControlError,match="PREAUTHORIZED"):
        changed_settings(cfg,initial_settings(cfg),"experience","AUTOMATIC")
    with pytest.raises(ControlError,match="EXTERNAL_RISK"):
        changed_settings(cfg,initial_settings(cfg),"capital","1")


def test_authorization_rotation_requires_control_db_migration(control):
    from dataclasses import replace
    cfg,store=control
    changed=replace(cfg,operator_control=replace(cfg.operator_control,authorization_version="other-auth"))
    with pytest.raises(ControlError,match="AUTHORIZATION_IDENTITY_CHANGED"):
        ControlStore(changed)
