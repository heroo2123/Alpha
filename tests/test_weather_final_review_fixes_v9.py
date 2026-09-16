from __future__ import annotations

import asyncio
from pathlib import Path

from polymarket_scanner import weather_only_discovery as discovery_module
from polymarket_scanner.weather_only_discovery import GLOBAL_CENSUS_TTL_SECONDS, WeatherOnlyDiscovery
from polymarket_scanner.weather_only_live_paper_all_signals_final_v8 import FinalAllPaperWeatherLiveServiceV8
from polymarket_scanner.weather_only_live_paper_all_signals_final_v9 import (
    MAX_GLOBAL_RECALL_REUSE_SECONDS, TELEGRAM_EDIT_ABSENT, FinalAllPaperWeatherLiveServiceV9, FinalOperatorStateTelegram,
)

ROOT = Path(__file__).resolve().parents[1]

class _Response:
    def __init__(self,status_code:int,payload:dict): self.status_code=status_code; self._payload=payload
    def json(self): return self._payload
class _HTTP:
    def __init__(self,response): self.response=response; self.calls=0
    async def post(self,*_args,**_kwargs): self.calls+=1; return self.response
class _Telegram:
    async def edit_html(self,_message_id:int,_text:str)->str: return "APPLIED"
class _BacklogStore:
    def __init__(self,count:int):
        self.rows={i:{"signal_id":i,"telegram_message_id":10_000+i,"message_text":f"invalidate {i}"} for i in range(1,count+1)}; self.applied=[]
    def ensure_operator_sync_records(self): return 0
    def pending_operator_sync(self,limit:int=50,*,signal_id:int|None=None):
        keys=[k for k in sorted(self.rows) if signal_id is None or k==signal_id]
        return [self.rows[k] for k in keys[:limit]]
    def mark_operator_sync_applied(self,signal_id:int): self.applied.append(int(signal_id)); self.rows.pop(int(signal_id))
    def mark_operator_sync_absent(self,signal_id:int,_message_id:int): self.mark_operator_sync_applied(signal_id)
    def mark_operator_sync_failed(self,_signal_id:int,_error:str): raise AssertionError("no sync failure expected")
    def operator_sync_summary(self):
        total=len(self.rows)+len(self.applied); return {"total":total,"applied":len(self.applied),"unconfirmed":len(self.rows),"failed":0,"healthy":not self.rows}

def test_explicit_deleted_telegram_message_is_terminal_absence_not_startup_poison():
    telegram=object.__new__(FinalOperatorStateTelegram); telegram.token="fixture-token"; telegram.chat_id="fixture-chat"; telegram.http=_HTTP(_Response(400,{"ok":False,"description":"Bad Request: message to edit not found"}))
    assert asyncio.run(telegram.edit_html(123,"invalidated"))==TELEGRAM_EDIT_ABSENT; assert telegram.http.calls==1

def test_operator_sync_drains_more_than_three_restart_sized_batches_in_one_process():
    service=object.__new__(FinalAllPaperWeatherLiveServiceV9); service.positions=_BacklogStore(475); service.telegram=_Telegram(); summary=asyncio.run(service._sync_operator_messages())
    assert summary["healthy"] is True and summary["unconfirmed"]==0 and summary["processed_this_pass"]==475 and summary["batches_this_pass"]>=3 and summary["restart_pagination_required"] is False and len(service.positions.applied)==475

def test_global_recall_hard_ttl_is_five_minutes_and_status_exposes_real_age(monkeypatch):
    assert GLOBAL_CENSUS_TTL_SECONDS==300.0 and MAX_GLOBAL_RECALL_REUSE_SECONDS==GLOBAL_CENSUS_TTL_SECONDS
    discovery=object.__new__(WeatherOnlyDiscovery); discovery._last_global_recall={"complete":True,"cache_hit":True,"pages":3,"scanned_events":250,"retained_events":4,"census_completed_at":1000.0,"age_seconds":0.0,"max_reuse_seconds":GLOBAL_CENSUS_TTL_SECONDS}
    monkeypatch.setattr(discovery_module.time,"time",lambda:1299.5); status=discovery.global_recall_status(); assert status["age_seconds"]==299.5 and status["max_reuse_seconds"]==300.0

def test_exhaustive_global_page_budget_covers_event_cap_without_raising_byte_cap():
    assert discovery_module.GLOBAL_PAGE_SIZE==50 and discovery_module.MAX_PAGE_BYTES==16*1024*1024 and discovery_module.GLOBAL_PAGE_SIZE*discovery_module.MAX_GLOBAL_PAGES>=discovery_module.MAX_GLOBAL_EVENT_HITS

def test_stale_global_cache_is_not_reused(monkeypatch):
    discovery=object.__new__(WeatherOnlyDiscovery); discovery._global_cache_at=1000.0; discovery._global_cache_events=({"id":"old"},); discovery._global_cache_pages=1; discovery._global_cache_scanned=1; monkeypatch.setattr(discovery_module.time,"time",lambda:1301.0); calls=0
    async def page(_tag,cursor,*,page_size):
        nonlocal calls; calls+=1; assert cursor is None; assert page_size==discovery_module.GLOBAL_PAGE_SIZE; return [],None
    discovery._keyset_page=page; events,pages,scanned,cache_hit=asyncio.run(discovery._global_weather_census()); assert calls==1 and events==() and pages==1 and scanned==0 and cache_hit is False

def test_fresh_completed_global_census_evidence_is_healthy(monkeypatch,tmp_path:Path):
    async def parent_cycle(_self): return {"cycle_ok":True,"operator_all_lanes_healthy":True,"global_weather_recall":{"complete":True,"cache_hit":True,"census_completed_at":1000.0,"age_seconds":120.0,"max_reuse_seconds":300.0},"errors":[]}
    monkeypatch.setattr(FinalAllPaperWeatherLiveServiceV8,"run_cycle",parent_cycle); service=object.__new__(FinalAllPaperWeatherLiveServiceV9); service.status_path=tmp_path/"status.json"; status=asyncio.run(service.run_cycle()); assert status["global_weather_recall_fresh"] is True and status["global_weather_recall_certified_at"]==1000.0 and status["global_weather_recall_age_seconds"]==120.0 and status["operator_all_lanes_healthy"] is True

def test_stale_or_missing_global_census_evidence_fails_closed(monkeypatch,tmp_path:Path):
    async def parent_cycle(_self): return {"cycle_ok":True,"operator_all_lanes_healthy":True,"global_weather_recall":{"complete":True,"cache_hit":True,"census_completed_at":1000.0,"age_seconds":301.0,"max_reuse_seconds":300.0},"errors":[]}
    monkeypatch.setattr(FinalAllPaperWeatherLiveServiceV8,"run_cycle",parent_cycle); service=object.__new__(FinalAllPaperWeatherLiveServiceV9); service.status_path=tmp_path/"status.json"; status=asyncio.run(service.run_cycle()); assert status["global_weather_recall_fresh"] is False and status["cycle_ok"] is False and status["operator_all_lanes_healthy"] is False and "GLOBAL_WEATHER_RECALL_STALE" in status["errors"]

def test_current_host_reference_refuses_candidate_supplied_privileged_authority():
    from test_host_authority_production_boundary import load
    import subprocess
    import pytest
    module = load()
    with pytest.raises(module.AuthorityError, match="REFERENCE_AUTHORITY_IS_NOT_HOST_AUTHORITY"):
        module.verify_self()
    result = subprocess.run(["/bin/bash", str(ROOT / "deploy/install-weather-paper-host-trust.sh"), "--bootstrap"], capture_output=True, text=True)
    assert result.returncode == 40 and "REFUSED" in result.stderr

def test_legacy_user_writable_rollback_path_is_not_authoritative_anymore():
    for path in (ROOT/"deploy/weather-paper-host-snapshot.sh",ROOT/"deploy/weather-paper-host-recovery.sh",ROOT/"deploy/prepare-all-paper-candidate-v3.sh"):
        assert '${CONFIG_DIR}/all-paper-rollback' not in path.read_text()
