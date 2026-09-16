"""Telegram controller; reads public handoff and sanitized execution status only."""
import asyncio
import json
import sqlite3
import time

from .collection import drain_scanner
from .config import canonical, decimal
from .control import ControlError
from .io import atomic_json, release_identity
from .notifications import import_events, notification_summary, send_pending
from .panel import OperatorPanel
from .service import SignalService


class Controller:
    def __init__(self,config,signals,controls,telegram):
        self.config,self.store,self.controls=config,signals,controls
        self.telegram=telegram
        self.service=SignalService(config,signals,telegram,None)
        self.panel=OperatorPanel(config,controls,self.service)
        self.last_error=None

    async def deliver(self,candidate):
        settings=self.panel.settings()
        if candidate["strategy"] not in settings["strategies"]:
            return
        # Apply the current threshold again to queued pre-change observations.
        # This is a signal filter; fresh financial validation remains in executor.
        cost=sum(decimal(x["price"],"signal_price")+decimal(x["fee"],"signal_fee",positive=False) for x in candidate["legs"])
        if "model_frequency" in candidate and decimal(candidate["model_frequency"],"model_support",positive=False)-cost<decimal(settings["min_model_gap"],"min_model_gap"):
            return
        if candidate["strategy"] in {"STRUCTURAL","RESULT_LAG"} and decimal(candidate["theoretical_payout"],"payout")-cost<=decimal(settings["min_structural_edge"],"min_structural_edge"):
            return
        await self.service.deliver(candidate)

    async def terminal(self,*args):
        await self.service.terminal(*args)

    async def commands(self):
        offset=int(self.controls.state("telegram_offset"))
        for update in await self.telegram.updates(offset):
            uid=update.get("update_id")
            if type(uid) is not int or uid<offset:
                continue
            try:
                await self.panel.handle(update)
            except ControlError:
                # UI pressure must not drop later safety commands in this poll.
                # Any accepted financial/control request already has durable custody.
                self.last_error="CONTROLLER_PANEL_REPLY_UNAVAILABLE"
            self.controls.set_state("telegram_offset",uid+1)

    async def collect(self):
        if self.config.mode=="LIVE_SIGNALS":
            self.panel.process_signals_requests()
        selected=canonical(self.panel.settings())
        if self.controls.state("collection_settings")!=selected:
            self.controls.set_state("collection_settings",selected)
        await drain_scanner(self.config,self.controls,self)
        source=self.panel.source_status()
        self.service.census=source.get("discovery",{})
        self.service.last_cycle=source.get("last_cycle",0)

    async def notifications(self):
        status=self.service.execution_status()
        if status.get("notification_batch"):
            import_events(self.controls,status["notification_batch"])
        await send_pending(self.controls,self.telegram)
        self.controls.compact_notifications()

    def status(self):
        return dict(self.service.status(),release=release_identity(),controller_error=self.last_error,
            operator_control=self.panel.settings(),notifications=notification_summary(self.controls))

    async def run(self,once=False):
        self.store.recover()
        async def repeated(fn,interval):
            while True:
                try:
                    await fn()
                except Exception:
                    self.last_error="CONTROLLER_"+fn.__name__.upper()+"_FAILED"
                if once:
                    return
                await asyncio.sleep(interval)
        async def publish_status():
            atomic_json(self.config.status_path,self.status(),mode=0o640)
        # Telegram notification latency cannot block command polling or expiry.
        await asyncio.gather(repeated(self.collect,1),repeated(self.commands,.5),
            repeated(self.notifications,1),repeated(self.service.expiry,1),repeated(self.panel.flush,.1),repeated(publish_status,1))
