"""Causal defensive PWS QC and informational neighborhood features.

Policy thresholds are explicit versioned inputs, not economic or calibration
constants. A healthy neighborhood does not establish settlement truth or lead.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime
import math
from statistics import median
from zoneinfo import ZoneInfo

from .certification import StationMetadata
from .evidence import EvidenceError, EvidenceStore, digest, finite, identity, sha
from .rules import history


def geometry(lat1,lon1,lat2,lon2):
    for lat,lon in ((lat1,lon1),(lat2,lon2)):
        if not -90<=finite(lat,nonnegative=False)<=90 or not -180<=finite(lon,nonnegative=False)<=180:
            raise EvidenceError('PWS_COORDINATE_INVALID')
    a,b=math.radians(lat1),math.radians(lat2)
    dl=math.radians(lon2-lon1)
    h=math.sin((b-a)/2)**2+math.cos(a)*math.cos(b)*math.sin(dl/2)**2
    distance=6371.0088*2*math.asin(min(1.,math.sqrt(max(0.,h))))
    bearing=(math.degrees(math.atan2(math.sin(dl)*math.cos(b),math.cos(a)*math.sin(b)-math.sin(a)*math.cos(b)*math.cos(dl)))+360)%360
    return distance,bearing


@dataclass(frozen=True)
class PWSPolicy:
    version: str
    distance_bands: tuple[tuple[float,float], ...]
    fresh_seconds: float
    history_seconds: float
    minimum_samples: int
    minimum_history_span: float
    maximum_gap_seconds: float
    maximum_jump_c: float
    maximum_rate_c_per_hour: float
    minimum_c: float
    maximum_c: float
    stuck_window_seconds: float
    stuck_epsilon_c: float
    neighbor_disagreement_c: float
    minimum_independent_stations: int
    relocation_km: float
    elevation_drift_m: float
    maximum_elevation_difference_m: float
    elevation_weight_scale_m: float
    colocated_km: float

    def __post_init__(self):
        identity(self.version)
        if not isinstance(self.distance_bands,tuple) or not 1<=len(self.distance_bands)<=8:
            raise EvidenceError('PWS_DISTANCE_BANDS_REQUIRED')
        previous,weight=0.,1.
        for distance,value in self.distance_bands:
            if not previous<finite(distance)<=200 or not 0<finite(value)<=weight:
                raise EvidenceError('PWS_DISTANCE_BAND_ORDER')
            previous,weight=distance,value
        if (not 1<=finite(self.fresh_seconds)<=7200 or not self.fresh_seconds<=finite(self.history_seconds)<=86400
                or not 0<finite(self.minimum_history_span)<=self.history_seconds
                or not 1<=finite(self.maximum_gap_seconds)<=self.history_seconds
                or not 60<=finite(self.stuck_window_seconds)<=self.history_seconds):
            raise EvidenceError('PWS_TIME_POLICY_BOUND')
        if (type(self.minimum_samples) is not int or not 2<=self.minimum_samples<=256
                or type(self.minimum_independent_stations) is not int or not 3<=self.minimum_independent_stations<=128):
            raise EvidenceError('PWS_SUPPORT_POLICY_BOUND')
        if not -100<=finite(self.minimum_c,nonnegative=False)<finite(self.maximum_c,nonnegative=False)<=80:
            raise EvidenceError('PWS_PHYSICAL_POLICY_BOUND')
        for v,limit in ((self.maximum_jump_c,50),(self.maximum_rate_c_per_hour,100),
                        (self.neighbor_disagreement_c,30),(self.relocation_km,10),
                        (self.elevation_drift_m,1000),(self.maximum_elevation_difference_m,3000),
                        (self.elevation_weight_scale_m,3000),(self.colocated_km,1)):
            if not 0<finite(v)<=limit:
                raise EvidenceError('PWS_QC_POLICY_BOUND')
        if not 0<=finite(self.stuck_epsilon_c)<=1:
            raise EvidenceError('PWS_STUCK_POLICY_BOUND')

    @property
    def sha256(self):
        return digest(asdict(self))


@dataclass(frozen=True)
class PWSSample:
    station: str
    provider: str
    latitude: float
    longitude: float
    elevation_m: float
    observed_at: float
    received_at: float
    temperature_c: float
    qc_applied: int
    qc_results: int
    evidence_sha256: str
    observation_identity: str

    def __post_init__(self):
        identity(self.station,maximum=32)
        if self.provider!='APRSWXNET':
            raise EvidenceError('PWS_PROVIDER_NOT_REVIEWED')
        geometry(self.latitude,self.longitude,self.latitude,self.longitude)
        if not -500<=finite(self.elevation_m,nonnegative=False)<=9000:
            raise EvidenceError('PWS_ELEVATION_INVALID')
        finite(self.observed_at)
        finite(self.received_at)
        finite(self.temperature_c,nonnegative=False)
        for value in (self.qc_applied,self.qc_results):
            if type(value) is not int or not 0<=value<2**32:
                raise EvidenceError('PWS_PROVIDER_QC_INVALID')
        sha(self.evidence_sha256)
        sha(self.observation_identity)

    @property
    def key(self):
        return self.provider+':'+self.station

    @property
    def metadata(self):
        return {'station':self.station,'provider':self.provider,'latitude':self.latitude,
                'longitude':self.longitude,'elevation_m':self.elevation_m}


def _weighted_quantile(values,quantile):
    ordered=sorted(values)
    total=math.fsum(w for _,w in ordered)
    running=0.
    for value,weight in ordered:
        running+=weight
        if running>=quantile*total:
            return value
    return ordered[-1][0]


def _trend(samples, seconds):
    latest=samples[-1]
    target=latest.observed_at-seconds
    options=[s for s in samples[:-1] if abs(s.observed_at-target)<=60]
    if not options:
        return None
    past=min(options,key=lambda s:(abs(s.observed_at-target),s.observed_at))
    return {'change_c':latest.temperature_c-past.temperature_c,
            'interval_seconds':latest.observed_at-past.observed_at,
            'start_observed_at':past.observed_at,'end_observed_at':latest.observed_at}


def _spatial_gradient(rows):
    if len(rows)<3:
        return None
    points=[(r['distance_km']*math.sin(math.radians(r['bearing_degrees'])),
             r['distance_km']*math.cos(math.radians(r['bearing_degrees'])),r['temperature_c'],r['weight']) for r in rows]
    total=math.fsum(w for _,_,_,w in points)
    means=[math.fsum(row[i]*row[3] for row in points)/total for i in range(3)]
    xx=yy=xy=xz=yz=0.
    for x,y,z,w in points:
        x,y,z=x-means[0],y-means[1],z-means[2]
        xx+=w*x*x
        yy+=w*y*y
        xy+=w*x*y
        xz+=w*x*z
        yz+=w*y*z
    det=xx*yy-xy*xy
    if det<=1e-9*max(xx*yy,1e-12):
        return None
    return {'east_c_per_km':(xz*yy-yz*xy)/det,'north_c_per_km':(yz*xx-xz*xy)/det,
            'supporting_stations':len(rows),'method':'WEIGHTED_LOCAL_PLANE_INFORMATIONAL'}


def neighborhood(samples: tuple[PWSSample,...], *, official: StationMetadata, as_of: float,
                 policy: PWSPolicy, quarantined: frozenset[str]=frozenset(),
                 official_temperature_c: float | None=None, official_received_at: float | None=None,
                 official_observed_at: float | None=None,
                 official_source_role: str | None=None) -> dict:
    as_of=finite(as_of)
    if not isinstance(samples,tuple) or len(samples)>16_384 or not isinstance(official,StationMetadata):
        raise EvidenceError('PWS_INPUT_BOUND_OR_CONTEXT')
    buckets=defaultdict(list)
    dropped=Counter()
    local_day=datetime.fromtimestamp(as_of,ZoneInfo(official.timezone)).date()
    for sample in samples:
        if not isinstance(sample,PWSSample):
            raise EvidenceError('PWS_SAMPLE_REQUIRED')
        if sample.received_at>as_of:
            dropped['NOT_YET_RECEIVED']+=1
            continue
        if sample.observed_at>sample.received_at:
            dropped['SOURCE_CLOCK_ERROR']+=1
            continue
        if as_of-sample.observed_at>policy.history_seconds:
            dropped['OUTSIDE_HISTORY_WINDOW']+=1
            continue
        buckets[sample.key].append(sample)
    if len(buckets)>128:
        raise EvidenceError('PWS_STATION_FANOUT_BOUND')
    rows=[]
    for key,records in sorted(buckets.items()):
        if len(records)>256:
            raise EvidenceError('PWS_STATION_HISTORY_BOUND')
        by_time=defaultdict(list)
        for sample in records:
            by_time[sample.observed_at].append(sample)
        chosen=[]
        first_receipts={}
        for at,versions in sorted(by_time.items()):
            # Repeated cached observations do not fabricate additional support.
            unique={}
            for sample in sorted(versions,key=lambda s:s.received_at):
                if sample.observation_identity in unique:
                    dropped['DUPLICATE_OBSERVATION_VERSION']+=1
                else:
                    unique[sample.observation_identity]=sample
            if len(unique)>1:
                dropped['REVISED_OBSERVATION_VERSION']+=len(unique)-1
            chosen.append(max(unique.values(),key=lambda s:(s.received_at,s.observation_identity)))
            first_receipts[at]=min(s.received_at for s in versions)
        latest=chosen[-1]
        distance,bearing=geometry(official.latitude,official.longitude,latest.latitude,latest.longitude)
        elevation=latest.elevation_m-official.elevation_m
        reasons=[]
        age=as_of-latest.observed_at
        if key in quarantined:
            reasons.append('PERSISTENT_METADATA_QUARANTINE')
        if age>policy.fresh_seconds:
            reasons.append('STALE')
        if not policy.minimum_c<=latest.temperature_c<=policy.maximum_c:
            reasons.append('PHYSICAL_RANGE')
        if latest.qc_applied==0 or latest.qc_results!=0:
            reasons.append('PROVIDER_QC_UNKNOWN_OR_FAILED')
        if distance>policy.distance_bands[-1][0]:
            reasons.append('OUTSIDE_DISTANCE_BANDS')
        if abs(elevation)>policy.maximum_elevation_difference_m:
            reasons.append('ELEVATION_MISMATCH')
        first=chosen[0]
        metadata_origin=min(records,key=lambda s:(s.received_at,s.observed_at))
        if any(geometry(metadata_origin.latitude,metadata_origin.longitude,s.latitude,s.longitude)[0]>policy.relocation_km
               or abs(metadata_origin.elevation_m-s.elevation_m)>policy.elevation_drift_m for s in records):
            reasons.append('METADATA_DRIFT')
        # Continuity starts again after a communication gap or rejected source
        # value; a large old history cannot legitimize the first returning sample.
        continuous=[]
        for sample in chosen:
            if (not policy.minimum_c<=sample.temperature_c<=policy.maximum_c
                    or sample.qc_applied==0 or sample.qc_results!=0):
                continuous=[]
                continue
            if continuous and sample.observed_at-continuous[-1].observed_at>policy.maximum_gap_seconds:
                continuous=[]
            if continuous:
                dt=sample.observed_at-continuous[-1].observed_at
                change=abs(sample.temperature_c-continuous[-1].temperature_c)
                if dt<=0 or change>policy.maximum_jump_c or change*3600/dt>policy.maximum_rate_c_per_hour:
                    continuous=[]
                    continue
            continuous.append(sample)
        if len(chosen)>1:
            dt=latest.observed_at-chosen[-2].observed_at
            change=abs(latest.temperature_c-chosen[-2].temperature_c)
            if dt>policy.maximum_gap_seconds:
                reasons.append('COMMUNICATION_GAP')
            elif dt>0 and (change>policy.maximum_jump_c or change*3600/dt>policy.maximum_rate_c_per_hour):
                reasons.append('JUMP_OR_IMPLAUSIBLE_RATE')
        if len(continuous)<policy.minimum_samples or (continuous and latest.observed_at-continuous[0].observed_at<policy.minimum_history_span):
            reasons.append('INSUFFICIENT_RECENT_HISTORY')
        band_weight=next((w for radius,w in policy.distance_bands if distance<=radius),0.)
        availability=len(continuous)/len(chosen) if chosen else 0.
        freshness=max(0.,1-age/(policy.fresh_seconds+1))
        weight=band_weight*math.exp(-abs(elevation)/policy.elevation_weight_scale_m)*availability*freshness
        window=[s for s in continuous if latest.observed_at-s.observed_at<=policy.stuck_window_seconds]
        flat=(len(window)>=policy.minimum_samples and window[-1].observed_at-window[0].observed_at>=policy.stuck_window_seconds-60
              and max(s.temperature_c for s in window)-min(s.temperature_c for s in window)<=policy.stuck_epsilon_c)
        # A flat trace can be real calm weather. Downweight it and report suspicion
        # without declaring a sensor indoors from temperature alone.
        if flat:
            weight*=.5
        daily=[s.temperature_c for s in continuous if datetime.fromtimestamp(s.observed_at,ZoneInfo(official.timezone)).date()==local_day]
        rows.append({'station_key':key,'metadata':latest.metadata,'metadata_fingerprint':digest(latest.metadata),
            'distance_km':distance,'bearing_degrees':bearing,'elevation_difference_m':elevation,
            'temperature_c':latest.temperature_c,'observed_at':latest.observed_at,'age_seconds':age,
            'first_received_at':first_receipts[latest.observed_at],'version_received_at':latest.received_at,
            'evidence_sha256':latest.evidence_sha256,'observation_identity':latest.observation_identity,
            'provider_qc_applied':latest.qc_applied,'provider_qc_results':latest.qc_results,
            'sample_count':len(chosen),'continuous_sample_count':len(continuous),'availability_fraction':availability,
            'history_span_seconds':latest.observed_at-first.observed_at,'flat_sensor_suspected':flat,
            'indoor_exposure_status':'NOT_INFERRED_WITHOUT_VALIDATED_HISTORY',
            'historical_official_residual_c':None,'relationship_reliability':'UNVALIDATED',
            'local_day_observed_high_c':max(daily) if daily else None,
            'local_day_observed_low_c':min(daily) if daily else None,
            'local_day_coverage_complete':False,
            'weight':0. if reasons else weight,'rejection_reasons':reasons,
            'trends':{str(n):_trend(continuous,n) if continuous and continuous[-1]==latest else None for n in (300,900,1800,3600)}})
    # Co-located feeds are one support unit, not multiple independent votes.
    selected=[]
    for row in sorted(rows,key=lambda r:(-r['weight'],r['station_key'])):
        if row['weight']<=0:
            continue
        duplicate=next((other for other in selected if geometry(row['metadata']['latitude'],row['metadata']['longitude'],
                      other['metadata']['latitude'],other['metadata']['longitude'])[0]<=policy.colocated_km),None)
        if duplicate:
            row['weight']=0.
            row['rejection_reasons'].append('COLOCATED_DEPENDENT_FEED')
            row['dependent_station_key']=duplicate['station_key']
        else:
            selected.append(row)
    # Neighbor comparison is simultaneous: rejecting one sensor cannot let a
    # second outlier escape by changing the reference set during the loop.
    outliers=[]
    for row in selected:
        peers=[r for r in selected if r is not row]
        if len(peers)>=policy.minimum_independent_stations-1 and abs(row['temperature_c']-median(r['temperature_c'] for r in peers))>policy.neighbor_disagreement_c:
            outliers.append(row)
    for row in outliers:
        row['weight']=0.
        row['rejection_reasons'].append('INDEPENDENT_NEIGHBOR_DISAGREEMENT')
    usable=[r for r in rows if r['weight']>0]
    vals=[(r['temperature_c'],r['weight']) for r in usable]
    central=_weighted_quantile(vals,.5) if vals else None
    trends={}
    for horizon in (300,900,1800,3600):
        changes=[(r['trends'][str(horizon)]['change_c'],r['weight']) for r in usable if r['trends'][str(horizon)] is not None]
        trends[str(horizon)]={'change_c':_weighted_quantile(changes,.5) if len(changes)>=policy.minimum_independent_stations else None,
                             'supporting_stations':len(changes),'interpolated':False}
    residual=None
    if official_temperature_c is not None:
        finite(official_temperature_c,nonnegative=False)
        identity(official_source_role)
        if (official_received_at is None or official_observed_at is None
                or not 0<=as_of-finite(official_observed_at)<=policy.fresh_seconds
                or not official_observed_at<=finite(official_received_at)<=as_of):
            raise EvidenceError('OFFICIAL_RESIDUAL_ANCHOR_NOT_CAUSAL_OR_FRESH')
        residual=official_temperature_c-central if central is not None else None
    status='HEALTHY' if len(usable)>=policy.minimum_independent_stations else 'DEGRADED' if usable else 'UNAVAILABLE'
    return {'version':'alpha_v11_pws_defensive_qc_v1','station':official.station,'official_metadata_fingerprint':official.fingerprint,
            'as_of':as_of,'policy_sha256':policy.sha256,'health':status,'station_count':len(rows),'usable_station_count':len(usable),
            'independence_status':'SPATIAL_DEDUPLICATION_NOT_STATISTICAL_CERTIFICATION','stations':rows,'dropped':dict(dropped),
            'weighted_median_c':central,'iqr_c':_weighted_quantile(vals,.75)-_weighted_quantile(vals,.25) if vals else None,
            'temperature_spread_c':max(x for x,_ in vals)-min(x for x,_ in vals) if vals else None,
            'observation_age_seconds':[r['age_seconds'] for r in usable],'temperature_changes':trends,
            'official_minus_neighborhood_c':residual,'official_anchor_source_role':official_source_role,
            'official_anchor_observed_at':official_observed_at,'official_anchor_received_at':official_received_at,
            'spatial_gradient':_spatial_gradient(usable),
            'uncertainty_action':'PWS_DEPENDENT_MODEL_REQUIRES_FALLBACK_OR_GATING' if status!='HEALTHY' else 'RELATIONSHIP_VALIDATION_STILL_REQUIRED',
            'trading_influence_permitted':False,'lead_advantage_verified':False,
            'settlement_authority':False,'calibration_label_authority':False,'financial_authority':False}


class PWSIdentityTracker:
    """Persist relocation quarantine beyond the numerical history window."""
    def __init__(self,store: EvidenceStore,policy: PWSPolicy):
        self.store,self.policy=store,policy

    def observe(self,record_id: str,sample: PWSSample,*,raw_evidence_id: str) -> dict:
        raw=self.store.get(raw_evidence_id)
        if raw['kind']!='PWS_OBSERVATION' or raw['sha256']!=sample.evidence_sha256 or sample.received_at>self.store.clock():
            raise EvidenceError('PWS_IDENTITY_EVIDENCE_BINDING')
        observations=raw['body']['payload'].get('observations',[])
        matches=[r for r in observations if r.get('observation_identity')==sample.observation_identity]
        if len(matches)!=1 or any(matches[0].get(k)!=v for k,v in sample.metadata.items()):
            raise EvidenceError('PWS_IDENTITY_RAW_PREIMAGE')
        event='pws:'+sample.key
        past=history(self.store,'REGISTRY',event)
        prior=past[-1]['body']['details'] if past else {}
        original=prior.get('original_metadata',sample.metadata)
        movement,_=geometry(original['latitude'],original['longitude'],sample.latitude,sample.longitude)
        changed=(movement>self.policy.relocation_km or abs(original['elevation_m']-sample.elevation_m)>self.policy.elevation_drift_m)
        quarantined=prior.get('quarantined',False) or changed
        return self.store.audit(record_id,event_id=event,kind='REGISTRY',details={
            'action':'PWS_METADATA','original_metadata':original,'latest_metadata':sample.metadata,
            'policy_sha256':self.policy.sha256,'quarantined':quarantined,
            'reason':'METADATA_DRIFT_REQUIRES_REVIEW' if quarantined else 'OBSERVED_AUXILIARY_IDENTITY',
            'restoration_automatic':False},evidence_ids=(raw_evidence_id,),expected_previous_seq=past[-1]['seq'] if past else 0)

    def quarantined(self,station_keys: tuple[str,...]) -> frozenset[str]:
        if len(station_keys)>128:
            raise EvidenceError('PWS_STATION_FANOUT_BOUND')
        result=set()
        for key in station_keys:
            past=history(self.store,'REGISTRY','pws:'+identity(key))
            if past and past[-1]['body']['details'].get('quarantined'):
                result.add(key)
        return frozenset(result)


def samples_from_capture(store: EvidenceStore, capture_id: str, *, as_of: float) -> tuple[PWSSample,...]:
    """Recompile archived raw XML before trusting normalized observation fields."""
    from .weather_sources import parse_madis_xml
    record=store.get(capture_id)
    body=record['body']
    if (record['kind']!='PWS_OBSERVATION' or body['provider']!='NOAA_MADIS_CWOP'
            or body['available_at']>finite(as_of) or body['evidence_class']=='HISTORICAL_AVAILABILITY_UNKNOWN'):
        raise EvidenceError('PWS_NORMALIZED_CAPTURE_NOT_CAUSAL')
    payload=body['payload']
    raw=store.get(payload['raw_evidence_id'])
    if (raw['sha256']!=payload['raw_evidence_sha256'] or raw['event_id']!=record['event_id']
            or raw['kind']!='PWS_OBSERVATION' or raw['body']['provider']!='NOAA_MADIS_CWOP'
            or raw['body']['available_at']>body['available_at']
            or raw['body']['evidence_class']!=body['evidence_class']):
        raise EvidenceError('PWS_RAW_CAPTURE_BINDING')
    source=raw['body']['payload']
    parsed=parse_madis_xml(source['response'],received_at=raw['body']['received_at'],params=source['request_params'])
    if parsed['observations']!=payload['observations'] or payload['feature_ready_at']>body['available_at']:
        raise EvidenceError('PWS_NORMALIZED_RAW_MISMATCH')
    return tuple(PWSSample(r['station'],r['provider'],r['latitude'],r['longitude'],r['elevation_m'],
        r['observed_at'],r['local_received_at'],r['temperature_c'],r['provider_qc_applied'],r['provider_qc_results'],
        record['sha256'],r['observation_identity']) for r in parsed['observations'])


def archive_neighborhood(store: EvidenceStore, record_id: str, *, event_id: str, capture_ids: tuple[str,...],
                         official: StationMetadata, policy: PWSPolicy) -> dict:
    if len(capture_ids)>64 or len(set(capture_ids))!=len(capture_ids):
        raise EvidenceError('PWS_CAPTURE_SET_BOUND')
    as_of=finite(store.clock())
    identity(event_id)
    inputs=[]
    latest={}
    station_history=defaultdict(list)
    events=set()
    classes=set()
    for key in capture_ids:
        record=store.get(key)
        if record['body']['payload'].get('settlement_station_context')!=official.station:
            raise EvidenceError('PWS_OFFICIAL_STATION_CONTEXT_MISMATCH')
        events.add(record['event_id'])
        classes.add(record['body']['evidence_class'])
        for sample in samples_from_capture(store,key,as_of=as_of):
            inputs.append(sample)
            station_history[sample.key].append((sample,key))
            if len(inputs)>16_384:
                raise EvidenceError('PWS_INPUT_BOUND_OR_CONTEXT')
            previous=latest.get(sample.key)
            if previous is None or (sample.received_at,sample.observed_at)>(previous[0].received_at,previous[0].observed_at):
                latest[sample.key]=(sample,key)
    if events and events!={event_id}:
        raise EvidenceError('PWS_EVENT_CONTEXT_REQUIRED')
    if len(latest)>128:
        raise EvidenceError('PWS_STATION_FANOUT_BOUND')
    tracker=PWSIdentityTracker(store,policy)
    for i,(station_key,(sample,raw_id)) in enumerate(sorted(latest.items())):
        ordered=sorted(station_history[station_key],key=lambda pair:(pair[0].received_at,pair[0].observed_at))
        origin,origin_id=ordered[0]
        tracker.observe(record_id+':metadata:'+str(i)+':origin',origin,raw_evidence_id=origin_id)
        drift=next(((s,key) for s,key in ordered if geometry(origin.latitude,origin.longitude,s.latitude,s.longitude)[0]>policy.relocation_km
                    or abs(origin.elevation_m-s.elevation_m)>policy.elevation_drift_m),None)
        if drift:
            tracker.observe(record_id+':metadata:'+str(i)+':drift',drift[0],raw_evidence_id=drift[1])
        if sample.metadata!=origin.metadata:
            tracker.observe(record_id+':metadata:'+str(i)+':latest',sample,raw_evidence_id=raw_id)
    result=neighborhood(tuple(inputs),official=official,as_of=as_of,policy=policy,
                         quarantined=tracker.quarantined(tuple(latest)))
    result.update(feature_ready_at=store.clock(),source_captures=[{'id':key,'sha256':store.get(key)['sha256']} for key in capture_ids])
    return store.capture(record_id,event_id=event_id,kind='PWS_OBSERVATION',provider='ALPHA_PWS_QC',
        source_identity=official.station,revision=record_id,payload=result,
        evidence_class='SYNTHETIC' if 'SYNTHETIC' in classes else 'PUBLIC_OBSERVED')
