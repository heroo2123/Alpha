from copy import deepcopy
from dataclasses import asdict
import time

import pytest

from polymarket_scanner.production.config import ConfigurationError
from polymarket_scanner.production.control import ControlPolicy
from test_operator_control_protocol import operator_config
from test_production_lifecycle import config


def raw_policy(cfg):
    p=cfg.operator_control
    return {"version":1,"db":str(p.db),"scanner_db":str(p.scanner_db),"scanner_status":str(p.scanner_status),
        "authorization_version":p.authorization_version,"authorization_expires_at":p.expires,
        "bot_id":p.bot_id,"chat_id":p.chat_id,"operator_user_ids":list(p.operators),
        "allowed_experiences":list(p.experiences),"allowed_fee_policies":list(p.fee_policies),
        "trading_schedule_utc":p.schedule}


@pytest.mark.parametrize("mode",["LIVE_SIGNALS","LIVE_EXECUTION"])
def test_real_configuration_parses_control_identity_and_paths(tmp_path,mode):
    original=operator_config(tmp_path,mode=mode)
    parsed=config(tmp_path,mode=mode,operator_control=raw_policy(original))
    assert parsed.operator_control==original.operator_control
    assert not parsed.activation_requested()


@pytest.mark.parametrize("key,value",[
    ("version",True),("version",2),("bot_id","123"),("chat_id",-42),("bot_id",True),
    ("operator_user_ids",[True]),("operator_user_ids",[42,42]),("operator_user_ids",[43]),
    ("authorization_version","short"),("authorization_expires_at",float("nan")),
    ("authorization_expires_at",float("inf")),("authorization_expires_at",True),
    ("authorization_expires_at",0),("db","relative.sqlite"),("scanner_db","/tmp/../alias.sqlite"),
    ("allowed_experiences",["AUTOMATIC"]),("allowed_fee_policies",[]),
    ("trading_schedule_utc",[[0,0,60],[0,59,120]]),("trading_schedule_utc",[[True,0,60]])])
def test_malformed_or_ambiguous_host_grant_is_rejected_before_start(tmp_path,key,value):
    raw=raw_policy(operator_config(tmp_path)); raw[key]=value
    with pytest.raises(ConfigurationError): config(tmp_path,operator_control=raw)


@pytest.mark.parametrize("kind",["db_alias","scanner_alias","state_directory","mode","unknown_field"])
def test_grant_cannot_alias_state_expand_signal_mode_or_smuggle_fields(tmp_path,kind):
    cfg=operator_config(tmp_path); raw=raw_policy(cfg); mode="LIVE_EXECUTION"
    if kind=="db_alias": raw["db"]=str(cfg.signal_db)
    elif kind=="scanner_alias": raw["scanner_status"]=raw["scanner_db"]
    elif kind=="state_directory": raw["scanner_db"]=str(cfg.signal_db.parent/"scanner.db");raw["scanner_status"]=str(cfg.signal_db.parent/"scanner-status.json")
    elif kind=="mode": mode="LIVE_SIGNALS"
    else: raw["shell_command"]="never executed"
    with pytest.raises(ConfigurationError): config(tmp_path,mode=mode,operator_control=raw)
