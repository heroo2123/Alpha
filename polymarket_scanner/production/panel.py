"""Private operator screens; opaque buttons reference validated server actions."""
import asyncio
import json
import sqlite3
import time

from .config import RiskLimits, canonical, digest
from .control import ControlError, INTEGER_LIMITS, changed_settings, initial_settings, validate_request
from .notifications import notification_summary
from .service import bounded_reply, units

UNITS = {"capital":"collateral USD", "per_order":"USD per order including reserved fees",
    "per_station_day":"USD per station/local weather date", "max_loss":"USD worst-case loss",
    "daily_loss":"USD; UTC day realized settlement losses plus unsettled costs/reserves",
    "max_price":"USD per share", "max_slippage":"USD/share above original reference",
    "max_fee_per_share":"USD/share local allowance", "legging_loss":"USD full basket cost at risk",
    "max_open_orders":"orders", "max_positions":"token positions", "max_maker_rest_seconds":"seconds (181–3600)",
    "min_model_gap":"uncalibrated model-support minus cost; fraction, not win probability",
    "min_structural_edge":"USD/share after fee allowance", "schedule_utc":"UTC [weekday 0=Mon, start minute, end minute] windows"}
STRATEGIES = {
    "DIRECTIONAL":"Strict station/date/unit/bucket binding; fresh GEFS/NWS evidence. Model support is uncalibrated, not win probability. FAK BUY, then hold.",
    "SAME_DAY":"Directional model support combined with fresh same-local-day observations. Revalidate revisions, timezone/date and bucket support immediately before FAK BUY. Uncalibrated; hold actual fills.",
    "SOURCE_SHOCK":"Fresh same-day official observation exclusion; source revisions and local midnight invalidate. FAK BUY, then hold. Finality is not established.",
    "MAKER":"Same strict fresh weather evidence; noncrossing GTD post-only BUY. Only authenticated fills count; touch/public prints do not. Cancel on expiry or invalidation.",
    "STRUCTURAL":"Complete reviewed mutually exclusive/exhaustive buckets and executable quotes. Sequential FAK legs; full cost reserved. Stop on known rejection/partial/UNKNOWN. No automatic unwind or locked-profit promise.",
    "RESULT_LAG":"GATED: supported sources do not currently prove publication finality. Specific opportunity rejected; no manufactured proof."}


class OperatorPanel:
    def __init__(self,config,store,service):
        self.config,self.store,self.service=config,store,service
        self.policy,self.telegram=config.operator_control,service.telegram
        self.ui_queue = asyncio.Queue(maxsize=100)
        if (self.telegram.delivery_identity!={"bot_id":str(self.policy.bot_id),"chat_id":str(self.policy.chat_id)}
            or self.telegram.operators!={str(x) for x in self.policy.operators}):
            raise ControlError("TELEGRAM_CONTROL_IDENTITY_MISMATCH")

    def settings(self):
        if self.config.mode=="LIVE_SIGNALS":
            return json.loads(self.store.state("signals_settings"))
        current=self.service.execution_status().get("control")
        if current:
            self.store.set_state("last_execution_settings",canonical(current))
        return current or json.loads(self.store.state("last_execution_settings",canonical(initial_settings(self.config))))

    def source_status(self):
        try:
            data=json.loads(self.policy.scanner_status.read_text())
            if data["config_sha256"]!=self.config.config_sha256 or not 0<=time.time()-data["updated_at"]<180:
                raise ValueError
            return data
        except (OSError,ValueError,KeyError,TypeError):
            return {"last_error":"SCANNER_STATUS_UNAVAILABLE_OR_STALE"}

    async def message(self,actor,text,buttons=(),*,expires=None,revision=None):
        ids,rows=[],[]
        if revision is None and buttons:
            revision=self.settings()["revision"]
        for label,op,data in buttons:
            identity=self.store.action(actor,op,data,revision,expires=expires)
            ids.append(identity)
            rows.append({"text":label,"callback_data":"ctl1:"+identity})
        markup={"inline_keyboard":[rows[i:i+2] for i in range(0,len(rows),2)]} if rows else None
        self.enqueue({"text":bounded_reply(text),"markup":markup,"ids":ids})
        return {"state":"QUEUED"}

    def enqueue(self,item):
        try:
            self.ui_queue.put_nowait(item)
        except asyncio.QueueFull:
            raise ControlError("PANEL_REPLY_BACKLOG_FULL") from None

    async def flush(self):
        if self.ui_queue.empty():
            return
        item=self.ui_queue.get_nowait()
        try:
            if "answer" in item:
                await self.telegram.answer(item["answer"],item["text"])
            else:
                result=await self.telegram.send_result(item["text"],item["markup"])
                self.store.bind_message(item["ids"],result.get("message_id"))
        finally:
            self.ui_queue.task_done()

    async def preview(self,actor,operation,data):
        settings=self.settings()
        if operation=="SET":
            new,grows=changed_settings(self.config,settings,data["key"],data["value"])
            key=data["key"]
            before=(settings.get("risk") or {}).get(key,settings.get(key,settings.get("strategies") if key.startswith("strategy:") else None))
            text=f"Review setting: {key}\nCurrent: {canonical(before)}\nRequested: {canonical(data['value'])}\nUnits: {UNITS.get(key,'explicit policy')}\n"
            if self.config.risk and key in RiskLimits.__dataclass_fields__:
                text+=f"Independent ceiling: {getattr(self.config.risk,key)}\n"
            text+=("Existing valid activation and reconciliation required.\n" if grows else "Risk reduction or narrower scope.\n")
            text+="Resting orders will be requested for cancellation; held inventory remains. A mode change leaves openings paused."
        elif operation=="RESUME":
            text="Resume openings in "+settings["experience"]+" within the current approved limits? Existing configuration-bound activation, fresh reconciliation, unexpired grant and no fault are required. This cannot grant initial authority."
        elif operation=="CONFIRM_TRADE":
            signal=self.service.store.candidate(data["signal_id"])
            if digest(signal)!=data["signal_hash"] or time.time()>=signal["expires"]:
                raise ControlError("SIGNAL_CHANGED_OR_EXPIRED")
            risk=settings.get("risk") or {}
            text=(f"Authorize ONE fresh attempt?\n{signal['title']}\n{signal['strategy']} | {signal['station_day']}\n"
                  f"Per-order ceiling ${risk.get('per_order')}; max price ${risk.get('max_price')}/share; slippage ${risk.get('max_slippage')}/share; fee allowance ${risk.get('max_fee_per_share')}/share.\n"
                  "All weather, semantics, liquidity and prices are revalidated. An old quote is not a purchase instruction. Confirmation may produce no order. Claiming it consumes one attempt, even after failure/restart.")
            if signal["strategy"]=="STRUCTURAL":
                text+=f"\nBasket cost-at-risk ceiling ${risk.get('legging_loss')}. Separate legs can leave directional inventory; no automatic unwind."
            return await self.message(actor,text,[("Confirm one attempt",operation,data),("Back","NAV",{"screen":"SIGNALS"})],expires=signal["expires"],revision=settings["revision"])
        else:
            raise ControlError("PREVIEW_OPERATION_INVALID")
        text+=f"\nConfiguration {self.config.config_sha256[:12]} · revision {settings['revision']} · button valid at most 120 seconds."
        return await self.message(actor,text,[("Confirm",operation,data),("Back","NAV",{"screen":"HOME"})],revision=settings["revision"])

    async def screen(self,actor,name):
        s=self.settings(); execution=self.service.execution_status(); account=execution.get("account",{})
        buttons=[]
        nav=lambda label,screen:(label,"NAV",{"screen":screen})
        if name=="HOME":
            source=self.source_status(); balance=execution.get("balance_micros"); reserved=account.get("reserved_micros",0)
            available=units(max(0,balance-reserved)) if type(balance) is int else "unavailable"
            text=(f"WEATHER OPERATOR\nMode: {self.config.mode} / {s['experience']}\nOpening authority: {execution.get('financial_authority',False)}\n"
                  f"Reason: {execution.get('opening_disabled_reason') or execution.get('reason') or 'ready'}\nAccount: {self.config.wallet or 'none; signals only'}\n"
                  f"Observed collateral: ${units(balance) if balance is not None else 'unavailable'}; reserved ${units(reserved)}; free estimate ${available}\n"
                  "Free estimate excludes claimable winnings and is rechecked before each submission.\n"
                  f"Outstanding/unknown orders: {len(account.get('outstanding_orders',[]))}; held positions: {len(account.get('positions',[]))}\n"
                  f"Execution reconciled: {execution.get('reconciled',False)}; fault: {account.get('reconciliation_fault') or execution.get('last_error') or 'none'}\n"
                  f"Sources: {source.get('last_error') or 'collecting'}; last cycle {source.get('last_cycle','unavailable')}\n"
                  f"Release: {execution.get('release',source.get('release','see approved release manifest'))}\nConfiguration: {self.config.config_sha256}\n"
                  f"Authorization: {self.policy.authorization_version}; expires {self.policy.expires}\nNotifications: {canonical(notification_summary(self.store))}")
            buttons=[nav("Strategies","STRATEGIES"),nav("Risk/settings","RISK"),nav("Mode","MODE"),nav("Orders","ORDERS"),nav("Positions","POSITIONS"),nav("Performance","PERFORMANCE"),nav("Signals / confirm","SIGNALS"),nav("Coverage","COVERAGE"),nav("Settlement / cash","CLAIMS"),nav("Wallet security","WALLET"),
                ("Pause openings","PAUSE",{}),("Request cancel all","CANCEL",{}),nav("Review resume","RESUME")]
        elif name=="STRATEGIES":
            text="STRATEGIES\nEnabled: "+", ".join(s["strategies"])+"\nAll require fresh supported semantics and explicit risk capacity. Select for evidence and eligibility."
            buttons=[nav(key,key) for key in STRATEGIES]
        elif name in STRATEGIES:
            text=name+"\n"+STRATEGIES[name]+f"\nEnabled: {name in s['strategies']}; allowed by protected configuration: {name in self.config.strategies}\nModel support gap: {s['min_model_gap']} (uncalibrated)\nStructural edge: ${s['min_structural_edge']}/share\nCurrent opening eligibility: {execution.get('financial_authority',False)}; each opportunity is independently checked."
            if name in self.config.strategies and name!="RESULT_LAG":
                buttons=[nav("Disable" if name in s["strategies"] else "Enable","TOGGLE:"+name)]
        elif name.startswith("TOGGLE:"):
            strategy=name.split(":",1)[1]
            return await self.preview(actor,"SET",{"key":"strategy:"+strategy,"value":strategy not in s["strategies"]})
        elif name=="MODE":
            text="EXPERIENCE\nSignals: no orders.\nConfirm: one fresh bounded attempt per authorized button.\nAutomatic: enabled strategies within explicit limits.\nEvery change pauses openings; separate resume is required. Initial financial grant is external."
            buttons=[nav(value,"MODE:"+value) for value in self.policy.experiences]
        elif name=="WALLET":
            if self.config.mode != "LIVE_EXECUTION":
                text=("WALLET SECURITY\nCurrent mode: LIVE_SIGNALS. No execution adapter, signer, "
                      "trading credentials, or financial authority is active in this runtime.\n"
                      "The preferred future execution route is a separately reviewed Deposit Wallet "
                      "with a CLOB-only Session Key; there is no silent EOA/owner-key fallback.\n"
                      "Keep account recovery, Session authorization/revocation, and owner custody "
                      "independent of Telegram and this host. See ACCOUNT_SECURITY_ONBOARDING.md.")
            elif self.config.wallet_type=="DEPOSIT_WALLET":
                text=("WALLET SECURITY\nCurrent adapter: Deposit Wallet with a CLOB-only Session Key. The executor holds the session EOA key and that session's CLOB credentials; the Deposit Wallet Owner key and Builder credentials stay off this host. The protected config permits only CLOB scope and this runtime has no owner or withdrawal action; owner-device acceptance must separately verify that the venue authorization matches that scope.\n"
                      f"Session signer: {self.config.signer}; venue authorization expires {self.config.session_valid_until}; dedicated-wallet exclusivity expires {self.config.session_exclusive_until}.\n"
                      "Authenticated order/trade visibility is session-scoped, so this route requires a dedicated wallet and the external exclusivity assertion bound into the activated configuration. Do not place manual owner orders or authorize another trading session while that assertion is active. Revocation is an owner-side emergency action independent of Telegram.\n"
                      "Signals work without any trading key. Account setup, session authorization/revocation and emergency owner access remain outside this host. See ACCOUNT_SECURITY_ONBOARDING.md.")
            else:
                text=("WALLET SECURITY\nCurrent adapter: dedicated direct EOA, wallet = signer. Its key can authorize transfers outside this application; BUY-only application policy is not a withdrawal restriction. A compromised executor/host can misuse that key.\n"
                      "Deposit Wallet CLOB-only Session Key is the preferred restricted adapter when separately provisioned and authorized; there is no silent owner-key fallback.\n"
                      "Signals work without any trading key. Account setup, revocation and emergency owner access must work independently of Telegram. See ACCOUNT_SECURITY_ONBOARDING.md.")
        elif name.startswith("MODE:"):
            return await self.preview(actor,"SET",{"key":"experience","value":name.split(":",1)[1]})
        elif name=="RISK":
            lines=["RISK / EXECUTION SETTINGS"]
            if not s["risk"]:
                lines.append("Financial limits unset. Signals only; external account/risk configuration required.")
            for key,value in (s["risk"] or {}).items():
                lines.append(f"{key}: {value} {UNITS[key]}")
                buttons.append((key,"INPUT",{"key":key}))
            for key in ("min_model_gap","min_structural_edge","schedule_utc"):
                lines.append(f"{key}: {canonical(s[key])} {UNITS[key]}")
                buttons.append((key,"INPUT",{"key":key}))
            lines.extend([f"Fee policy: {s['fee_policy']}","Local caps/reservations cannot guarantee external fees or fills. Exchange enforces signed limit price, order quantity/type and GTD/post-only rules; authenticated reconciliation detects discrepancies.","Structural: full basket reservation, sequential FAK, stop on known failure; no automatic SELL/unwind."])
            buttons += [nav(fee,"FEE:"+fee) for fee in self.policy.fee_policies]
            text="\n".join(lines)
        elif name.startswith("FEE:"):
            return await self.preview(actor,"SET",{"key":"fee_policy","value":name.split(":",1)[1]})
        elif name=="RESUME":
            return await self.preview(actor,"RESUME",{})
        elif name=="ORDERS":
            lines=["ORDERS — acknowledged is not filled; cancellation requested is not confirmed."]
            for order in account.get("recent_orders",[]):
                state=order.get('exchange_terminal_status') or order['status']
                lines.append(f"{order['id']} {state} {units(order['matched'])}/{units(order['quantity'])} shares; limit ${order['limit_price']}")
            lines.append("ACTUAL FILLS (confirmed):")
            for fill in account.get("recent_fills",[])[:8]:
                price=units(fill["cost"]*1_000_000//fill["quantity"]) if fill["quantity"] else "unavailable"
                lines.append(f"{fill['order_id']}: {units(fill['quantity'])} shares @ ${price}; fee ${units(fill['fee'])}")
            lines.append("Recent skip/reject reasons appear in execution notifications; complete history in local export. UNKNOWN retains reservation. Cancel does not sell inventory.")
            text="\n".join(lines)
        elif name=="POSITIONS":
            text="Actual held inventory survives pause/cancel. Claimable value is NOT SPENDABLE CASH; open Settlement / cash for redemption state.\n"+self.service.operator_report("/positions")
            buttons=[nav("Settlement / cash","CLAIMS")]
        elif name=="CLAIMS":
            text="CLAIMABLE, NOT SPENDABLE CASH\n"
            for claim in account.get("claimable_positions",[])[:10]:
                text+=claim["token"]+": "+("UNVERIFIED" if claim["unredeemed_value_micros"] is None else "$"+units(claim["unredeemed_value_micros"]))+"\n"
            text+="Verified redeemed proceeds by asset (not an available balance): "+canonical({k:units(v) for k,v in account.get("verified_redemption_proceeds_by_asset_micros",{}).items()})+"\n"
            text+="Owner redeems externally. Record verified receipt locally. No automatic redemption or early selling. Collateral conversion/allowances may still be required."
        elif name=="PERFORMANCE":
            text=self.service.operator_report("/stats")+"\nSignal outcomes are observations, not manual trades. Claim P&L is distinct from redeemed cash. Native gas is reported separately, not invented as USD profit."
        elif name=="COVERAGE":
            source=self.source_status()
            text="SUPPORTED WEATHER SCOPE\nStrict reviewed station/date/timezone/unit/high-or-low/bucket grammar only. Every weather-looking rejection is counted; completed enumeration is not full semantic coverage. Unsupported templates are skipped. Result-lag is gated on missing source finality.\nCensus: "+canonical(source.get("discovery",{}))+"\nLast collection epoch: "+str(source.get("last_cycle","unavailable"))
        elif name=="SIGNALS":
            text=self.service.operator_report("/recent")
            if s["experience"]=="CONFIRM" and not s["paused"]:
                for row in self.service.store.recent():
                    if row["status"]=="ACTIVE" and row["delivery"]=="DELIVERED" and row["expires"]>time.time():
                        buttons.append(nav("Review "+row["id"][:12],"TRADE:"+row["id"]))
        elif name.startswith("TRADE:"):
            signal=self.service.store.candidate(name.split(":",1)[1])
            return await self.preview(actor,"CONFIRM_TRADE",{"signal_id":signal["id"],"signal_hash":digest(signal)})
        else:
            raise ControlError("SCREEN_UNKNOWN")
        if name!="HOME": buttons.append(nav("Home","HOME"))
        return await self.message(actor,text,buttons,revision=s["revision"])

    async def input_prompt(self,actor,key):
        identity=self.store.action(actor,"INPUT",{"key":key},self.settings()["revision"])
        self.enqueue({"text":bounded_reply(f"Reply with {key}. Units: {UNITS[key]}. No credentials. No change until the next confirmation. Reply expires in 120 seconds."),"markup":{"force_reply":True,"selective":True},"ids":[identity]})

    async def handle(self,update):
        actor=self.telegram.principal(update,self.policy)
        if actor is None:
            return
        callback=update.get("callback_query")
        try:
            if callback:
                token=callback.get("data","")
                if not isinstance(token,str) or not token.startswith("ctl1:") or len(token)>64:
                    raise ControlError("BUTTON_UNKNOWN")
                body=self.store.click(token[5:],actor=actor,message_id=callback["message"]["message_id"],update_id=update["update_id"],revision=self.settings()["revision"])
                op,data=body["operation"],body["data"]
                if op=="NAV": await self.screen(actor,data["screen"])
                elif op=="INPUT": await self.input_prompt(actor,data["key"])
                else:
                    if self.config.mode=="LIVE_SIGNALS": self.process_signals_requests()
                    await self.message(actor,"Request recorded. Opening pause takes effect immediately; the executor confirms other effects in durable notifications. Cancellation does not erase fills or sell inventory.",[("Refresh","NAV",{"screen":"HOME"})])
                self.enqueue({"answer":callback.get("id"),"text":"Request handled; refresh for current state."})
            else:
                message=update["message"]; text=str(message.get("text",""))
                reply=message.get("reply_to_message")
                if reply:
                    if reply.get("from",{}).get("id")!=self.policy.bot_id or reply.get("from",{}).get("is_bot") is not True:
                        raise ControlError("INPUT_REPLY_BOT_MISMATCH")
                    with self.store.connect() as db:
                        row=db.execute("SELECT id FROM operator_actions WHERE message_id=? AND json_extract(body,'$.operation')='INPUT' AND state='PREVIEW'",(reply.get("message_id"),)).fetchone()
                    if not row or len(text)>1000: raise ControlError("INPUT_UNKNOWN_OR_TOO_LARGE")
                    body=self.store.click(row[0],actor=actor,message_id=reply["message_id"],update_id=update["update_id"],revision=self.settings()["revision"])
                    key=body["data"]["key"]
                    value=json.loads(text) if key in INTEGER_LIMITS or key=="schedule_utc" else text.strip()
                    await self.preview(actor,"SET",{"key":key,"value":value})
                elif text.split(" ")[0] in {"/start","/status","/recent","/positions","/stats","/orders","/settings","/stop","/cancel_open"}:
                    command=text.split(" ")[0]
                    if command in {"/stop","/cancel_open"}:
                        revision=self.settings()["revision"]
                        identity=self.store.action(actor,"PAUSE" if command=="/stop" else "CANCEL",{},revision)
                        self.store.bind_message([identity],message["message_id"])
                        self.store.click(identity,actor=actor,message_id=message["message_id"],update_id=update["update_id"],revision=revision)
                        if self.config.mode=="LIVE_SIGNALS": self.process_signals_requests()
                        await self.message(actor,"Safety request durably recorded. New openings paused; cancellation is only requested, never presumed confirmed. Held positions remain. Use /status for current state.")
                        return
                    await self.screen(actor,{"/recent":"SIGNALS","/positions":"POSITIONS","/stats":"PERFORMANCE","/orders":"ORDERS","/settings":"RISK"}.get(command,"HOME"))
        except (ControlError,ValueError,KeyError,TypeError) as exc:
            from .executor_control import safe_reason
            reason=safe_reason(exc)
            if callback: self.enqueue({"answer":callback.get("id"),"text":reason+". Open /start for fresh buttons."})
            else: await self.message(actor,reason+". Open /start for fresh controls.")

    def process_signals_requests(self):
        from .control import ControlReader
        from .executor_control import safe_reason
        reader=ControlReader(self.policy)
        if reader.state("identity", "") != canonical(self.policy.identity(self.config)):
            raise ControlError("CONTROL_AUTHORIZATION_MISMATCH")
        for row in reader.requests(int(self.store.state("signals_request_cursor"))):
            settings=self.settings(); body=None
            epoch=int(self.store.state("signals_control_epoch"))
            update=int(self.store.state("signals_control_update","-1"))
            try:
                body=validate_request(row,self.config,settings,epoch,update)
                op,data=body["operation"],body["data"]
                if op=="SET":
                    if time.time()>=self.policy.expires or set(data)!={"key","value"} or not isinstance(data["key"],str):
                        raise ControlError("CONTROL_SETTING_OR_AUTHORIZATION_EXPIRED")
                    settings,_=changed_settings(self.config,settings,data["key"],data["value"])
                elif op in {"PAUSE","CANCEL"} and not data:
                    settings=dict(settings,paused=True,revision=settings["revision"]+1)
                    epoch=max(epoch,body["safety_epoch"]+1)
                else:
                    raise ControlError("FINANCIAL_CONTROL_REQUIRES_EXTERNAL_EXECUTION_CONFIGURATION")
                result="APPLIED"
            except (ValueError,TypeError,KeyError) as exc:
                result=safe_reason(exc)
                settings=dict(settings,paused=True,revision=settings["revision"]+1)
                epoch=int(reader.state("safety_epoch"))
            with self.store.transaction() as db:
                db.execute("INSERT INTO operator_request_results VALUES(?,?,?,?)",(row["action_id"],row["seq"],result,time.time()))
                for key,value in {"signals_settings":canonical(settings),"signals_request_cursor":str(row["seq"]),
                    "signals_control_epoch":str(epoch),"signals_control_update":str(body["update_id"] if body else update),
                    "signals_control_result":result}.items():
                    db.execute("INSERT OR REPLACE INTO operator_state VALUES(?,?)",(key,value))
