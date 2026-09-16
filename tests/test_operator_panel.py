import asyncio
from copy import deepcopy
from dataclasses import replace
import json
import time

import pytest

from polymarket_scanner.production.config import digest
from polymarket_scanner.production.control import ControlStore
from polymarket_scanner.production.controller import Controller
from polymarket_scanner.production.io import atomic_json
from polymarket_scanner.production.panel import OperatorPanel, UNITS, STRATEGIES
from polymarket_scanner.production.service import SignalService
from polymarket_scanner.production.signals import SignalStore
from polymarket_scanner.production.telegram import Telegram
from test_operator_executor import op, activate, request
from test_operator_control_protocol import operator_config


class Bot:
    principal=Telegram.principal
    def __init__(self):
        self.delivery_identity={"bot_id":"123","chat_id":"42"}
        self.operators={"42"}; self.messages=[]; self.answers=[]; self.incoming=[]
    async def send_result(self,text,markup=None):
        mid=len(self.messages)+1
        self.messages.append({"text":text,"markup":markup,"message_id":mid})
        return {"state":"SENT","message_id":mid}
    async def send(self,text): return (await self.send_result(text))["message_id"]
    async def answer(self,cid,text): self.answers.append((cid,text))
    async def invalidate(self,row): return "EDITED"
    async def updates(self,offset):
        rows=self.incoming; self.incoming=[]; return rows


@pytest.fixture
def panel(op):
    atomic_json(op[0].execution_status_path,op[-1].status())
    bot=Bot(); signals=SignalStore(op[0].signal_db)
    service=SignalService(op[0],signals,bot,None)
    result=OperatorPanel(op[0],op[2],service)
    yield result,bot
    signals.close()


def flush(panel):
    while not panel.ui_queue.empty(): asyncio.run(panel.flush())


def command(text="/start",uid=1):
    return {"update_id":uid,"message":{"message_id":1000+uid,"date":int(time.time()),"text":text,"chat":{"id":42,"type":"private"},"from":{"id":42,"is_bot":False}}}


def callback(bot,label,uid=2):
    message=bot.messages[-1]
    button=next(b for row in message["markup"]["inline_keyboard"] for b in row if b["text"]==label)
    return {"update_id":uid,"callback_query":{"id":"query"+str(uid),"from":{"id":42,"is_bot":False},"data":button["callback_data"],"message":{"message_id":message["message_id"],"date":int(time.time()),"chat":{"id":42,"type":"private"},"from":{"id":123,"is_bot":True}}}}


@pytest.mark.parametrize("screen",["HOME","STRATEGIES","RISK","MODE","ORDERS","POSITIONS","PERFORMANCE","SIGNALS","COVERAGE",*STRATEGIES])
def test_every_required_screen_is_bounded_and_has_server_side_buttons(panel,screen):
    ui,bot=panel
    asyncio.run(ui.screen(42,screen)); flush(ui)
    message=bot.messages[-1]
    assert len(message["text"].encode("utf-16-le"))//2<=3900
    for row in message["markup"]["inline_keyboard"]:
        for button in row:
            assert button["callback_data"].startswith("ctl1:") and len(button["callback_data"].encode())<=64


@pytest.mark.parametrize("tamper",["user","user_bool","group","chat","bot","forward","inline","callback_id","stale"])
def test_forged_forwarded_stale_callbacks_cannot_change_state(panel,op,tamper):
    ui,bot=panel; asyncio.run(ui.screen(42,"HOME")); flush(ui)
    update=callback(bot,"Pause openings")
    q=update["callback_query"]
    if tamper=="user": q["from"]["id"]=43
    elif tamper=="user_bool": q["from"]["id"]=True
    elif tamper=="group": q["message"]["chat"]["type"]="supergroup"
    elif tamper=="chat": q["message"]["chat"]["id"]=43
    elif tamper=="bot": q["message"]["from"]["id"]=124
    elif tamper=="forward": q["message"]["forward_origin"]={"type":"user"}
    elif tamper=="inline": q["inline_message_id"]="forged"
    elif tamper=="callback_id": q["data"]="ctl1:forged"
    else:
        with op[2].connect() as db:
            rows=db.execute("SELECT id,body FROM operator_actions").fetchall()
            for row in rows:
                body=json.loads(row["body"]); body["expires"]=time.time()-1
                db.execute("UPDATE operator_actions SET body=?,body_hash=? WHERE id=?",(json.dumps(body),digest(body),row["id"]))
    asyncio.run(ui.handle(update))
    assert op[2].state("safety_epoch")=="0"


def test_double_click_pause_is_idempotent_and_does_not_wait_for_telegram(panel,op):
    ui,bot=panel; activate(op); atomic_json(op[0].execution_status_path,op[-1].status())
    asyncio.run(ui.screen(42,"HOME")); flush(ui)
    update=callback(bot,"Pause openings",uid=3)
    async def unreachable(*args,**kwargs): raise AssertionError("safety path awaited Telegram")
    bot.send_result=unreachable; bot.answer=unreachable
    asyncio.run(ui.handle(update)); asyncio.run(ui.handle(update))
    assert not op[-1].authority() and op[2].state("safety_epoch")=="1"


@pytest.mark.parametrize("key,value",[("per_order","4"),("max_open_orders","5"),("max_price",".8"),("max_maker_rest_seconds","200"),("schedule_utc","[[0,60,120]]")])
def test_typed_setting_requires_bound_reply_and_separate_confirmation(panel,op,key,value):
    ui,bot=panel; asyncio.run(ui.input_prompt(42,key)); flush(ui)
    prompt=bot.messages[-1]
    update=command(value,5); update["message"]["reply_to_message"]={"message_id":prompt["message_id"],"from":{"id":123,"is_bot":True}}
    asyncio.run(ui.handle(update)); flush(ui)
    with op[2].connect() as db: assert db.execute("SELECT COUNT(*) FROM operator_requests").fetchone()[0]==0
    confirm=callback(bot,"Confirm",6)
    asyncio.run(ui.handle(confirm)); op[-1].control.process()
    assert op[-1].control.settings["revision"]==1
    actual=op[-1].control.settings["schedule_utc"] if key=="schedule_utc" else op[-1].control.settings["risk"][key]
    assert actual==(json.loads(value) if key in {"max_open_orders","max_maker_rest_seconds","schedule_utc"} else str(__import__("decimal").Decimal(value)))


def test_mode_preview_does_not_activate_and_resume_is_separate(panel,op):
    ui,bot=panel; asyncio.run(ui.screen(42,"MODE:AUTOMATIC")); flush(ui)
    assert not op[-1].authority()
    asyncio.run(ui.handle(callback(bot,"Confirm",7))); op[-1].control.process()
    assert op[-1].control.settings["experience"]=="AUTOMATIC" and not op[-1].authority()


def test_unknown_order_and_claimable_cash_distinction_visible(panel,op):
    ui,bot=panel; activate(op); op[4].outcome=TimeoutError()
    asyncio.run(op[-1].tick()); atomic_json(op[0].execution_status_path,op[-1].status())
    asyncio.run(ui.screen(42,"ORDERS")); flush(ui)
    assert "UNKNOWN" in bot.messages[-1]["text"] and "not confirmed" in bot.messages[-1]["text"]
    asyncio.run(ui.screen(42,"POSITIONS")); flush(ui)
    assert "NOT SPENDABLE CASH" in bot.messages[-1]["text"]


def test_signals_only_panel_can_select_strategies_without_execution_or_credentials(tmp_path):
    cfg=operator_config(tmp_path,mode="LIVE_SIGNALS")
    store=SignalStore(cfg.signal_db); controls=ControlStore(cfg); bot=Bot()
    try:
        ui=OperatorPanel(cfg,controls,SignalService(cfg,store,bot,None))
        asyncio.run(ui.preview(42,"SET",{"key":"strategy:MAKER","value":False})); flush(ui)
        asyncio.run(ui.handle(callback(bot,"Confirm",4)))
        assert "MAKER" not in ui.settings()["strategies"]
        assert not cfg.credentials_file
    finally: store.close(); controls.close()


def test_old_operator_message_and_forwarded_reply_rejected(panel):
    ui,bot=panel
    old=command(); old["message"]["date"]-=121
    asyncio.run(ui.handle(old))
    assert ui.ui_queue.empty()
    forwarded=command(); forwarded["message"]["forward_from"]={"id":42}
    asyncio.run(ui.handle(forwarded)); assert ui.ui_queue.empty()


def test_reconciliation_fault_not_chat_clearable(panel,op):
    ui,bot=panel
    with pytest.raises(Exception): asyncio.run(ui.preview(42,"SET",{"key":"fault","value":None}))
    assert op[2].state("safety_epoch")=="0"
