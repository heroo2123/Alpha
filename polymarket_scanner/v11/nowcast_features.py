"""Causal physical-feature candidates with explicit missingness and ablation.

These features do not assign probabilities, remaining heating/cooling potential,
or settlement authority. Trading influence requires separately reviewed evidence.
"""
from __future__ import annotations

from datetime import datetime
import math
from statistics import median
from zoneinfo import ZoneInfo

from .certification import StationMetadata
from .datasets import FeatureDefinition, FeatureSchema, archive_features
from .evidence import EvidenceError, EvidenceStore, digest, finite
from .weather_sources import parse_awc_metar


def _f(name,unit,family,low,high):
    return FeatureDefinition(name,unit,family,low,high,True)


PHYSICAL_SCHEMA=FeatureSchema('alpha_v11_physical_candidates_v1',(
    _f('official_proxy_temperature_c','C','OFFICIAL_PROXY',-100,100),
    _f('official_observation_age_seconds','seconds','OFFICIAL_PROXY',0,86400),
    _f('dewpoint_spread_c','C','MOISTURE',0,200),
    _f('wind_speed_mps','m/s','WIND',0,150),_f('wind_gust_mps','m/s','WIND',0,150),
    _f('wind_direction_sin','ratio','WIND',-1,1),_f('wind_direction_cos','ratio','WIND',-1,1),
    _f('reported_broken_cloud','flag','CLOUD',0,1),_f('reported_overcast','flag','CLOUD',0,1),
    _f('reported_rain','flag','PRESENT_WEATHER',0,1),
    _f('temperature_slope_c_per_hour','C/hour','TRAJECTORY',-1000,1000),
    _f('temperature_acceleration_c_per_hour2','C/hour2','TRAJECTORY',-10000,10000),
    _f('trajectory_span_seconds','seconds','TRAJECTORY',0,86400),
    _f('pws_weighted_median_c','C','PWS',-100,100),_f('pws_iqr_c','C','PWS',0,200),
    _f('pws_change_15m_c','C','PWS',-50,50),_f('official_minus_pws_c','C','PWS',-100,100),
    _f('daylight_remaining_seconds','seconds','DAYLIGHT',0,86400),
))


def physical_feature_identity(official, ablated_families, maximum_age, window, gap):
    return 'physical:'+digest([official.fingerprint,sorted(ablated_families),maximum_age,window,gap])


def _awc_response(store, row):
    """Decode the exact raw response behind a normalized receipt, when present."""
    b=row['body'];p=b['payload']
    if 'raw_evidence_id' not in p:
        return p['response']
    raw=store.get(p['raw_evidence_id']);rb=raw['body']
    if (raw['sha256']!=p.get('raw_evidence_sha256') or raw['seq']>=row['seq']
            or raw['kind']!=row['kind'] or raw['event_id']!=row['event_id']
            or any(rb.get(k)!=b.get(k) for k in ('provider','source_identity','revision','received_at','evidence_class'))
            or rb['available_at']>b['available_at']
            or p.get('adapter_version')!='alpha_v11_awc_metar_v2_physical_body'):
        raise EvidenceError('PHYSICAL_AWC_RAW_LINEAGE_MISMATCH')
    return rb['payload']['response']


def archive_nowcast_features(store: EvidenceStore, record_id: str, *, event_id: str,
        official: StationMetadata, awc_capture_ids: tuple[str,...],
        max_observation_age_seconds: float, trajectory_window_seconds: float,
        maximum_gap_seconds: float, pws_capture_id: str | None=None,
        daylight_capture_id: str | None=None, ablated_families: frozenset[str]=frozenset(),
        as_of: float | None=None) -> dict:
    ready=finite(store.clock());now=ready if as_of is None else finite(as_of)
    if now>ready:raise EvidenceError('PHYSICAL_FEATURE_CUTOFF_IN_FUTURE')
    if (not isinstance(awc_capture_ids,tuple) or len(awc_capture_ids)>32
            or len(set(awc_capture_ids))!=len(awc_capture_ids)):
        raise EvidenceError('PHYSICAL_CAPTURE_BOUND')
    for v in (max_observation_age_seconds,trajectory_window_seconds,maximum_gap_seconds):
        if not 0<finite(v)<=86400:
            raise EvidenceError('PHYSICAL_TIME_POLICY_BOUND')
    families={f.family for f in PHYSICAL_SCHEMA.features}
    if not isinstance(ablated_families,frozenset) or not ablated_families<=families:
        raise EvidenceError('UNKNOWN_PHYSICAL_ABLATION_FAMILY')
    values={f.name:None for f in PHYSICAL_SCHEMA.features}
    reasons=[]
    inputs=[]
    expiries=[]
    observations=[]
    for key in awc_capture_ids:
        r=store.get(key)
        b=r['body']
        if (r['kind']!='OFFICIAL_OBSERVATION' or r['event_id']!=event_id or b['provider']!='NOAA_AWC'
                or b['available_at']>now or b['evidence_class']=='HISTORICAL_AVAILABILITY_UNKNOWN'):
            raise EvidenceError('PHYSICAL_OFFICIAL_INPUT_NOT_CAUSAL')
        # Consume the archived response itself, not a mutable latest report or a
        # caller's claimed normalized physical fields.
        parsed=parse_awc_metar(_awc_response(store,r),station=official.station,received_at=b['received_at'],
                               max_age_seconds=max(max_observation_age_seconds,trajectory_window_seconds))
        for obs in parsed['observations']:
            if now-obs['observed_at']<=trajectory_window_seconds:
                observations.append(obs)
        if len(observations)>1024:
            raise EvidenceError('PHYSICAL_OBSERVATION_BOUND')
        inputs.append(key)
    # Receipt-ordered revisions replace values only in this new feature version.
    # Earlier archived decisions retain their own exact evidence sets.
    by_time={}
    for obs in sorted(observations,key=lambda x:(x['local_received_at'],x['observed_at'])):
        by_time[obs['observed_at']]=obs
    ordered=sorted(by_time.values(),key=lambda x:x['observed_at'])
    latest=ordered[-1] if ordered else None
    if latest and now-latest['observed_at']<=max_observation_age_seconds:
        expiries.append(latest['observed_at']+max_observation_age_seconds)
        values['official_proxy_temperature_c']=latest['temperature_c']
        values['official_observation_age_seconds']=now-latest['observed_at']
        physical=latest['physical_context']
        if physical['body_dewpoint_c'] is not None and latest['temperature_c']>=physical['body_dewpoint_c']:
            values['dewpoint_spread_c']=latest['temperature_c']-physical['body_dewpoint_c']
        values['wind_speed_mps']=physical['wind_speed_mps']
        values['wind_gust_mps']=physical['wind_gust_mps']
        if physical['wind_direction_degrees'] is not None:
            angle=math.radians(physical['wind_direction_degrees'])
            values['wind_direction_sin'],values['wind_direction_cos']=math.sin(angle),math.cos(angle)
        if physical['cloud_layers'] is not None:
            values['reported_broken_cloud']=float(any(c['cover']=='BKN' for c in physical['cloud_layers']))
            values['reported_overcast']=float(any(c['cover']=='OVC' for c in physical['cloud_layers']))
        if physical['reported_weather_codes'] is not None:
            values['reported_rain']=float(any('RA' in token for token in physical['reported_weather_codes']))
        reasons.extend(physical['reasons'])
        continuous=[]
        for obs in ordered:
            if continuous and obs['observed_at']-continuous[-1]['observed_at']>maximum_gap_seconds:
                continuous=[]
            continuous.append(obs)
        if len(continuous)>=2:
            slopes=[]
            for a,b in zip(continuous,continuous[1:]):
                dt=(b['observed_at']-a['observed_at'])/3600
                slopes.append(((a['observed_at']+b['observed_at'])/2,(b['temperature_c']-a['temperature_c'])/dt))
            values['temperature_slope_c_per_hour']=median(s for _,s in slopes)
            values['trajectory_span_seconds']=continuous[-1]['observed_at']-continuous[0]['observed_at']
            if len(slopes)>=2:
                accelerations=[(b[1]-a[1])*3600/(b[0]-a[0]) for a,b in zip(slopes,slopes[1:])]
                values['temperature_acceleration_c_per_hour2']=median(accelerations)
    else:
        reasons.append('NO_FRESH_OFFICIAL_PROXY')
    if pws_capture_id and 'PWS' not in ablated_families:
        r=store.get(pws_capture_id)
        b,p=r['body'],r['body']['payload']
        if (r['kind']!='PWS_OBSERVATION' or r['event_id']!=event_id or b['provider']!='ALPHA_PWS_QC'
                or p.get('station')!=official.station or p.get('official_metadata_fingerprint')!=official.fingerprint
                or b['available_at']>now or p['feature_ready_at']>now or p['as_of']>now):
            raise EvidenceError('PHYSICAL_PWS_CONTEXT_OR_TIME')
        fresh=[s for s in p['stations'] if s['weight']>0 and 0<=now-s['observed_at']<=max_observation_age_seconds]
        if p['health']=='HEALTHY' and len(fresh)==p['usable_station_count'] and fresh:
            expiries.append(min(s['observed_at'] for s in fresh)+max_observation_age_seconds)
            values['pws_weighted_median_c']=p['weighted_median_c']
            values['pws_iqr_c']=p['iqr_c']
            values['pws_change_15m_c']=p['temperature_changes']['900']['change_c']
            if values['official_proxy_temperature_c'] is not None:
                values['official_minus_pws_c']=values['official_proxy_temperature_c']-p['weighted_median_c']
        else:
            reasons.append('PWS_UNAVAILABLE_DEGRADED_OR_STALE')
        inputs.append(pws_capture_id)
    else:
        reasons.append('PWS_ABLATED' if 'PWS' in ablated_families else 'PWS_NOT_SUPPLIED')
    if daylight_capture_id:
        r=store.get(daylight_capture_id)
        b,p=r['body'],r['body']['payload']
        local_day=datetime.fromtimestamp(now,ZoneInfo(official.timezone)).date().isoformat()
        if (r['kind']!='MODEL' or r['event_id']!=event_id or b['available_at']>now or b['issued_at'] is None
                or p.get('feature_type')!='DAYLIGHT_WINDOW' or p.get('station')!=official.station
                or p.get('local_date')!=local_day or p.get('source_role')!='ASTRONOMICAL_FORECAST_NOT_SETTLEMENT'):
            raise EvidenceError('DAYLIGHT_CAUSAL_CONTEXT_REQUIRED')
        sunrise,sunset=finite(p['sunrise_at']),finite(p['sunset_at'])
        if (not sunrise<sunset or sunset-sunrise>86400
                or any(datetime.fromtimestamp(t,ZoneInfo(official.timezone)).date().isoformat()!=local_day
                       for t in (sunrise,sunset))):
            raise EvidenceError('DAYLIGHT_WINDOW_INVALID')
        values['daylight_remaining_seconds']=max(0.,sunset-max(now,sunrise))
        expiries.append(sunset if now < sunset else now+max_observation_age_seconds)
        inputs.append(daylight_capture_id)
    # Physical outliers make that optional feature missing, never a fabricated
    # probability or a reason to drop unrelated healthy source values.
    for f in PHYSICAL_SCHEMA.features:
        v=values[f.name]
        if v is not None and not f.minimum<=v<=f.maximum:
            values[f.name]=None
            reasons.append('FEATURE_BOUND:'+f.name)
        if f.family in ablated_families:
            values[f.name]=None
    details={'version':'alpha_v11_physical_candidates_v1','station':official.station,'as_of':now,
             'feature_schema_sha256':PHYSICAL_SCHEMA.sha256,'values':values,'reasons':sorted(set(reasons)),
             'ablated_families':sorted(ablated_families),'use':'RESEARCH_CANDIDATES_REQUIRE_OOS_ABLATION',
             'calibrated_probability':False,'settlement_authority':False,'financial_authority':False}
    if not inputs:
        return store.audit(record_id,event_id=event_id,kind='MEASUREMENT',details={**details,'state':'NO_SOURCE_INPUTS'})
    feature=archive_features(store,record_id,event_id=event_id,schema=PHYSICAL_SCHEMA,values=values,
        evidence_ids=tuple(inputs),source_versions={'physical':'alpha_v11_physical_candidates_v1','AWC':'alpha_v11_awc_metar_v2_physical_body'},
        context=dict(version='alpha_v11_physical_context_v1',station=official.station,metadata_fingerprint=official.fingerprint,
            as_of=now,valid_until=min(expiries) if expiries else now+max_observation_age_seconds,
            ablated_families=sorted(ablated_families)),
        source_identity=physical_feature_identity(official,ablated_families,max_observation_age_seconds,
                                                 trajectory_window_seconds,maximum_gap_seconds))
    store.audit(record_id+':explanation',event_id=event_id,kind='MEASUREMENT',details=details,evidence_ids=(feature['id'],))
    return feature
