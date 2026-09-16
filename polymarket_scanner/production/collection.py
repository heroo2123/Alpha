"""Public-source scanner and nonsecret durable handoff to the controller.

This process has neither Telegram credentials nor execution credentials. Its
proposals still require the existing executor's independent fresh validation.
"""
import asyncio
from contextlib import contextmanager
import json
import os
import sqlite3
import time
from decimal import Decimal

from .config import canonical, digest
from .control import ControlError, ControlReader
from .io import atomic_json, release_identity
from .signals import SignalReader


class ScanStore:
    def __init__(self, config):
        self.path = config.operator_control.scanner_db
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.is_symlink():
            raise ControlError("SCANNER_DB_SYMLINK")
        self._anchor = sqlite3.connect(self.path, isolation_level=None)
        self._anchor.execute("PRAGMA journal_mode=WAL")
        self._anchor.executescript("""
            CREATE TABLE IF NOT EXISTS scan_state(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS scan_events(seq INTEGER PRIMARY KEY AUTOINCREMENT,
                id TEXT NOT NULL UNIQUE,kind TEXT NOT NULL,data TEXT NOT NULL,created REAL NOT NULL);
        """)
        self._anchor.close()
        os.chmod(self.path,0o640)
        self._anchor = sqlite3.connect(self.path, isolation_level=None)
        self._anchor.execute("PRAGMA journal_mode=WAL")
        self._anchor.execute("SELECT COUNT(*) FROM scan_state")
        identity = canonical({"config_sha256":config.config_sha256,"protocol":1})
        old = self.state("identity",identity)
        if old != identity:
            self.close()
            raise ControlError("SCANNER_CONFIGURATION_CHANGED_REVIEWED_MIGRATION_REQUIRED")
        self.set_state("identity",identity)

    def close(self):
        self._anchor.close()

    @contextmanager
    def connect(self):
        db=sqlite3.connect(self.path,timeout=5)
        try:
            db.row_factory=sqlite3.Row
            db.execute("PRAGMA synchronous=FULL")
            with db:
                yield db
        finally:
            db.close()

    def state(self,key,default=""):
        with self.connect() as db:
            row=db.execute("SELECT value FROM scan_state WHERE key=?",(key,)).fetchone()
            return row[0] if row else default

    def set_state(self,key,value):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO scan_state VALUES(?,?)",(key,str(value)))

    def publish(self,kind,data,ack):
        if kind not in {"SIGNAL","TERMINAL","OUTCOME"} or len(canonical(data))>500_000:
            raise ControlError("SCANNER_EVENT_INVALID")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            high=db.execute("SELECT COALESCE(MAX(seq),0) FROM scan_events").fetchone()[0]
            if type(ack) is not int or not 0 <= ack <= max(high,int(self.state("last_ack","0"))):
                raise ControlError("SCANNER_ACK_INVALID")
            db.execute("DELETE FROM scan_events WHERE seq<=?",(ack,))
            db.execute("INSERT OR REPLACE INTO scan_state VALUES('last_ack',?)",(str(ack),))
            if db.execute("SELECT COUNT(*) FROM scan_events").fetchone()[0]>=1000:
                raise ControlError("SCANNER_HANDOFF_BACKLOG_FULL")
            db.execute("INSERT OR IGNORE INTO scan_events(id,kind,data,created) VALUES(?,?,?,?)",(digest({"kind":kind,"data":data}),kind,canonical(data),time.time()))


class Scanner:
    def __init__(self,config,store,weather):
        self.config,self.store,self.weather=config,store,weather
        self.signals=SignalReader(config.signal_db)
        self.controls=ControlReader(config.operator_control)
        self.census,self.last_error,self.last_cycle={},None,0

    def publish(self,kind,data):
        self.store.publish(kind,data,int(self.controls.state("scanner_cursor")))

    async def cycle(self):
        # Terminal revisions take priority over discovering new opportunities.
        for row in self.signals._query("SELECT id,expires FROM live_signals WHERE status='ACTIVE' ORDER BY expires LIMIT 500"):
            reason="QUOTE_VALIDITY_ENDED" if time.time()>=row["expires"] else None
            if not reason:
                try:
                    await self.weather.revalidate(self.signals.candidate(row["id"]))
                except Exception:
                    reason="EVIDENCE_OR_MARKET_NO_LONGER_VALID"
            if reason:
                self.publish("TERMINAL",{"id":row["id"],"status":"EXPIRED" if time.time()>=row["expires"] else "INVALIDATED","reason":reason})
        discovered=await self.weather.discover()
        self.census=discovered["status"]
        events=sorted(discovered["events"],key=lambda x:str(x.get("id","")))
        cursor=self.store.state("event_cursor")
        selected=[x for x in events if str(x.get("id",""))>cursor] or events
        settings=json.loads(self.controls.state("collection_settings"))
        strategies=set(settings["strategies"]) & self.config.strategies
        gap=max(Decimal(settings["min_model_gap"]),self.config.min_model_gap)
        edge=max(Decimal(settings["min_structural_edge"]),self.config.min_structural_edge)
        for event in selected[:6]:
            self.store.set_state("event_cursor",str(event.get("id","")))
            try:
                candidates=await self.weather.evaluate(event,strategies,gap,edge)
                for candidate in candidates:
                    self.publish("SIGNAL",candidate)
            except Exception:
                self.last_error="EVENT_EVIDENCE_REJECTED_OR_HANDOFF_UNAVAILABLE"
        rows=self.signals._query("SELECT id FROM live_signals WHERE outcome IS NULL AND outcome_checked<? ORDER BY outcome_checked,id LIMIT 20",(time.time()-300,))
        for row in rows:
            try:
                outcome=await self.weather.outcome(self.signals.candidate(row["id"]))
                self.publish("OUTCOME",{"id":row["id"],"outcome":outcome,"checked_at":time.time()})
            except Exception:
                self.publish("OUTCOME",{"id":row["id"],"outcome":None,"checked_at":time.time()})
        self.last_cycle=time.time()

    def status(self):
        with self.store.connect() as db:
            pending=db.execute("SELECT COUNT(*) FROM scan_events").fetchone()[0]
        return {"config_sha256":self.config.config_sha256,"release":release_identity(),"updated_at":time.time(),"last_cycle":self.last_cycle,
                "last_error":self.last_error,"discovery":self.census,"handoff_pending":pending,
                "supported_scope":"STRICT_SUPPORTED_WEATHER_SUBSET","unsupported_policy":"SKIP_AND_COUNT"}

    async def run(self,once=False):
        while True:
            try:
                await self.cycle()
            except Exception:
                self.last_error="SOURCE_OR_HANDOFF_UNAVAILABLE"
            atomic_json(self.config.operator_control.scanner_status,self.status(),mode=0o640)
            if once:
                return
            await asyncio.sleep(60)


async def drain_scanner(config,controls,service):
    policy=config.operator_control
    with sqlite3.connect(policy.scanner_db.as_uri()+"?mode=ro",uri=True) as db:
        db.row_factory=sqlite3.Row
        identity=db.execute("SELECT value FROM scan_state WHERE key='identity'").fetchone()
        if not identity or json.loads(identity[0])!={"config_sha256":config.config_sha256,"protocol":1}:
            raise ControlError("SCANNER_IDENTITY_MISMATCH")
        cursor=int(controls.state("scanner_cursor"))
        ack=db.execute("SELECT value FROM scan_state WHERE key='last_ack'").fetchone()
        if ack and int(ack[0])>cursor:
            raise ControlError("SCANNER_HANDOFF_CURSOR_REGRESSED")
        rows=[dict(row) for row in db.execute("SELECT * FROM scan_events WHERE seq>? ORDER BY seq LIMIT 100",(cursor,))]
    for row in rows:
        data=json.loads(row["data"])
        if digest({"kind":row["kind"],"data":data})!=row["id"]:
            raise ControlError("SCANNER_EVIDENCE_CORRUPTED")
        if row["kind"]=="SIGNAL":
            if data["strategy"] in config.strategies:
                await service.deliver(data)
        elif row["kind"]=="TERMINAL":
            await service.terminal(data["id"],data["status"],data["reason"])
        elif row["kind"]=="OUTCOME":
            service.store.outcome(data["id"],data["outcome"])
            if data["outcome"]:
                await service.terminal(data["id"],"SETTLED","OBSERVED_MARKET_OUTCOME")
        else:
            raise ControlError("SCANNER_EVENT_UNSUPPORTED")
        # Replays after a cross-DB crash are idempotent at SignalStore.
        controls.set_state("scanner_cursor",row["seq"])
