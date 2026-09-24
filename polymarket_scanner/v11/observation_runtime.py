"""Nonfinancial collection/normalization cycles with durable provider scheduling.

This is the source plane, not a paper trading acceptance shortcut. Fresh receipt
and schema success do not certify station representativeness or a strategy.
"""
from __future__ import annotations

from collections import defaultdict
import fcntl
import os
from urllib.parse import urlsplit

from .collection import PublicCollector, SourceRequest
from .evidence import EvidenceError, EvidenceStore, finite, identity
from .rules import history
from .weather_sources import normalize_weather_capture


# Conservative engineering cadence, not fitted economic or model thresholds.
MIN_INTERVAL_SECONDS = {"madis-data.ncep.noaa.gov":300, "aviationweather.gov":60,
                        "gamma-api.polymarket.com":60, "clob.polymarket.com":1,
                        "nomads.ncep.noaa.gov":1}


class ScheduledCollector:
    def __init__(self, collector: PublicCollector):
        self.collector, self.store = collector,collector.store

    async def cycle(self, cycle_id: str, requests: tuple[SourceRequest, ...]) -> dict:
        identity(cycle_id, maximum=80)
        if not 1 <= len(requests) <= 16:
            raise EvidenceError("COLLECTOR_CYCLE_LIMIT")
        lock = self.store.path.with_name(self.store.path.name+".collector.lock")
        fd = os.open(lock, os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(fd,fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise EvidenceError("COLLECTOR_ALREADY_RUNNING") from None
            return await self._cycle(cycle_id,requests)
        finally:
            os.close(fd)

    async def _cycle(self, cycle_id, requests):
        grouped = defaultdict(list)
        for request in requests:
            if not isinstance(request,SourceRequest):
                raise EvidenceError("SOURCE_REQUEST_REQUIRED")
            grouped[urlsplit(request.url).hostname].append(request)
        if len(grouped.get('nomads.ncep.noaa.gov',()))>1:
            raise EvidenceError('GEFS_ONE_REQUEST_PER_SCHEDULED_STEP')
        eligible, reservations, omitted = [],{},[]
        now = finite(self.store.clock())
        for index,(host,members) in enumerate(sorted(grouped.items())):
            event = "source-host:"+host
            past = history(self.store,"SOURCE_SCHEDULE",event)
            previous = past[-1]["body"]["details"] if past else {}
            if previous.get("next_allowed_at",0)>now:
                for member in members:
                    omitted.append({"event_id":member.event_id,"provider":member.provider,
                                    "state":"COOLDOWN","next_allowed_at":previous["next_allowed_at"]})
                continue
            reserved = self.store.audit(f"{cycle_id}:schedule:{index}:reserve",event_id=event,
                kind="SOURCE_SCHEDULE",details={"state":"IN_PROGRESS","host":host,
                    "cycle_id":cycle_id,"requests":len(members),
                    "failure_count":previous.get("failure_count",0),
                    "next_allowed_at":now+max(MIN_INTERVAL_SECONDS[host],self.collector.cycle_seconds+2)},
                expected_previous_seq=past[-1]["seq"] if past else 0)
            reservations[host]=(index,reserved)
            eligible.extend(members)
        if not eligible:
            return {"cycle_id":cycle_id,"sources":[],"omitted":omitted,"partial_success":False,
                    "financial_authority":False,"automatic_order_placement":False}
        collected = await self.collector.cycle(cycle_id,tuple(eligible))
        outputs = defaultdict(list)
        for request,result in zip(eligible,collected["sources"]):
            outputs[urlsplit(request.url).hostname].append(result)
            result["event_id"] = request.event_id
            result["source_identity"] = request.source_identity
        for host,(index,reservation) in reservations.items():
            results=outputs[host]
            successful = all(r["state"]=="SUCCESS" for r in results)
            failures=0 if successful else min(16,reservation["body"]["details"]["failure_count"]+1)
            base=MIN_INTERVAL_SECONDS[host]
            delay=base if successful else min(3600,max(base,5*2**failures))
            if host=='nomads.ncep.noaa.gov' and not successful:
                delay=max(delay,60)  # NWS unavailable-service guidance; no immediate retry.
            if any(r["state"]=="RATE_LIMIT" for r in results):
                delay=max(delay,300)
            due=max(finite(self.store.clock())+delay,
                    *(r["retry_not_before"] or 0 for r in results))
            self.store.audit(f"{cycle_id}:schedule:{index}:complete",event_id="source-host:"+host,
                kind="SOURCE_SCHEDULE",details={"state":"RAW_RESPONSE_PERSISTED" if successful else "TRANSPORT_DEGRADED",
                    "host":host,"cycle_id":cycle_id,"failure_count":failures,"next_allowed_at":due},
                evidence_ids=tuple(r["record_id"] for r in results),expected_previous_seq=reservation["seq"])
        return dict(collected,omitted=omitted)


class ObservationRuntime:
    def __init__(self, scheduled: ScheduledCollector):
        self.scheduled,self.store=scheduled,scheduled.store

    async def cycle(self, cycle_id: str, requests: tuple[SourceRequest, ...], *,
                    station_by_event: dict[str,str], strategies: tuple[str,...],
                    required_providers_by_strategy: dict[str,tuple[str,...]] | None=None) -> dict:
        if not strategies or len(strategies)>16 or len(set(strategies)) != len(strategies):
            raise EvidenceError("OBSERVATION_STRATEGY_BOUND")
        for strategy in strategies:
            identity(strategy)
        events=sorted({r.event_id for r in requests})
        if not set(events)<=station_by_event.keys():
            raise EvidenceError("EVENT_STATION_CONTEXT_MISSING")
        requirements=None
        if required_providers_by_strategy is not None:
            planned={r.provider for r in requests}
            if set(required_providers_by_strategy)!=set(strategies):
                raise EvidenceError('STRATEGY_SOURCE_REQUIREMENTS_MISMATCH')
            requirements={}
            for strategy,providers in required_providers_by_strategy.items():
                if (not isinstance(providers,tuple) or not providers or len(set(providers))!=len(providers)
                        or not set(providers)<=planned):
                    raise EvidenceError('STRATEGY_SOURCE_REQUIREMENTS_INVALID')
                requirements[strategy]=providers
        collected=await self.scheduled.cycle(cycle_id,requests)
        normalized=[]
        for index,source in enumerate(collected["sources"]):
            if source["state"]!="SUCCESS":
                continue
            raw_id=source["capture_ids"][0]
            try:
                row=normalize_weather_capture(self.store,raw_id,record_id=f"{cycle_id}:{index}:normalized",
                                              station=station_by_event[source["event_id"]])
                content=row["body"]["payload"]
                state="SUCCESS" if content["observations"] else "ABSENT"
                reason="NORMALIZED_AUXILIARY_NOT_CERTIFIED" if content["observations"] else "NO_USABLE_OBSERVATIONS"
                references=(row["id"],) if content["observations"] else ()
            except (EvidenceError, KeyError, TypeError, ValueError):
                state,reason,references="SEMANTIC_FAILURE","NORMALIZATION_FAILED",()
            health=self.store.source_result(f"{cycle_id}:{index}:normalized-health",event_id=source["event_id"],
                provider=source["provider"],cycle_id=cycle_id,state=state,reason=reason,elapsed_ms=0,
                capture_ids=references)
            normalized.append({"event_id":source["event_id"],"provider":source["provider"],
                               "state":state,"capture_ids":list(references),"health_id":health["id"]})
        # A complete raw response is not a calibrated or financially admissible
        # strategy. Record source coverage plus the remaining certification gate.
        for event_index,event in enumerate(events):
            planned={r.provider for r in requests if r.event_id==event}
            ready={provider for provider in planned if
                   sum(x['event_id']==event and x['provider']==provider and x['state']=='SUCCESS' for x in normalized)
                   ==sum(r.event_id==event and r.provider==provider for r in requests)}
            for strategy_index,strategy in enumerate(strategies):
                required=set(requirements[strategy]) if requirements is not None else planned
                covered=bool(required) and required<=ready
                self.store.funnel(f"{cycle_id}:funnel:{event_index}:{strategy_index}",event_id=event,
                    strategy=strategy,stage="SOURCE_READY",state="PASS" if covered else "NO_DATA",
                    reason="NORMALIZED_REQUIRED_SOURCE_COVERAGE_ONLY" if covered else "INCOMPLETE_OR_COOLDOWN_REQUIRED_SOURCE_COVERAGE",
                    cycle_id=cycle_id)
        status={"cycle_id":cycle_id,"collection":collected,"normalization":normalized,
                "required_providers_by_strategy":requirements,
                "calibrated_probability":False,"strategy_admission":"REQUIRES_SCOPED_CERTIFICATION_AND_VALUATION",
                "financial_authority":False,"automatic_order_placement":False}
        self.store.audit(cycle_id+":status",event_id="observation-runtime",kind="RUNTIME_STATUS",details=status,
                         evidence_ids=tuple(x["health_id"] for x in normalized))
        return status
