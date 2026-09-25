"""Run-bound NOAA GEFS paths with explicit temporal approximation.

Every control/perturbed member must bracket the entire local day on one grid.
The resulting member extremes describe a piecewise-linear *forecast* path, not
unobserved intrastep extrema, calibrated probabilities or settlement evidence.
"""
import base64
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import math
import re
import time as monotonic_time
from zoneinfo import ZoneInfo

from .evidence import EvidenceError, digest, finite
from .forecast_sources import ForecastPlan
from .grib_fields import MAX_BYTES, VERSION as DECODER_VERSION, decode_gefs_field
from .probability import FINAL_EXTREME, target_identity
from .pws_quality import geometry
from .strategy_pipeline import INPUT_VERSION
from ..weather_only_contracts import DAILY_HIGH


PROVIDER = 'NOAA_GEFS_0P50'
ENDPOINT = 'https://nomads.ncep.noaa.gov/cgi-bin/filter_gefs_atmos_0p50a.pl'
FIELD_VERSION = 'alpha_v11_gefs_field_v1'
VERSION = 'alpha_v11_gefs_linear_day_v1'
MODEL_ID = 'NOAA_GEFS_0P50_LINEAR_DAY_V1'
MAX_FIELDS = 341


def linear_extreme(points, values, intervals, *, high):
    """Extreme of a declared piecewise-linear path over every unresolved interval."""
    def interpolate(at):
        if at == points[-1]: return values[-1]
        i = next(i for i in range(len(points)-1) if points[i] <= at <= points[i+1])
        return values[i]+(values[i+1]-values[i])*(at-points[i])/(points[i+1]-points[i])
    samples = []
    for start, end in intervals:
        samples.extend((interpolate(start), interpolate(end)))
        samples.extend(v for t, v in zip(points, values) if start < t < end)
    return (max if high else min)(samples)


@dataclass(frozen=True)
class GEFSPlan:
    forecast: ForecastPlan
    initialized_at: float
    maximum_run_age_seconds: float = 86400.

    def __post_init__(self):
        if not isinstance(self.forecast,ForecastPlan): raise EvidenceError('GEFS_TYPED_FORECAST_PLAN_REQUIRED')
        stamp=finite(self.initialized_at)
        if not 0<finite(self.maximum_run_age_seconds)<=86400: raise EvidenceError('GEFS_RUN_AGE_POLICY_BOUND')
        try: run=datetime.fromtimestamp(stamp,timezone.utc)
        except (ValueError,OverflowError,OSError): raise EvidenceError('GEFS_RUN_TIMESTAMP_INVALID') from None
        if run.hour not in (0,6,12,18) or run.minute or run.second or run.microsecond:
            raise EvidenceError('GEFS_EXACT_INITIALIZATION_REQUIRED')
        # The immutable plan requests a run; only matching bytes prove it.
        start,end=self.window
        if start<stamp or end-stamp>240*3600: raise EvidenceError('GEFS_RUN_CANNOT_COVER_LOCAL_DAY')
        if abs(self.forecast.metadata.latitude)>88: raise EvidenceError('GEFS_POLAR_TILE_UNSUPPORTED')

    @property
    def event_id(self): return self.forecast.event_id

    @property
    def rule(self): return self.forecast.rule

    @property
    def window(self):
        p=self.rule.payload; day=date.fromisoformat(p['target_date']); tz=ZoneInfo(p['timezone'])
        return (datetime.combine(day,time(),tz).timestamp(),datetime.combine(day+timedelta(days=1),time(),tz).timestamp())

    @property
    def hours(self):
        start,end=self.window
        return tuple(range(math.floor((start-self.initialized_at)/10800)*3,
                           math.ceil((end-self.initialized_at)/10800)*3+1,3))

    @property
    def source_identity(self): return 'GEFS_LINEAR:'+self.rule.payload['station']+':'+self.rule.payload['target_date']+':'+self.rule.payload['family']

    def field_identity(self,member,hour):
        return 'GEFS_FIELD:'+digest([self.source_identity,self.initialized_at,member,hour])


def validate_params(params):
    keys={'file','dir','lev_2_m_above_ground','var_TMP','subregion','leftlon','rightlon','toplat','bottomlat'}
    if (type(params) is not dict or set(params)!=keys or params['lev_2_m_above_ground']!='on'
            or params['var_TMP']!='on' or params['subregion']!=''):
        raise EvidenceError('GEFS_EXACT_PUBLIC_QUERY_REQUIRED')
    file=re.fullmatch(r'ge(c00|p(?:0[1-9]|[12][0-9]|30))\.t(00|06|12|18)z\.pgrb2a\.0p50\.f([0-9]{3})',params['file'])
    directory=re.fullmatch(r'/gefs\.([0-9]{8})/(00|06|12|18)/atmos/pgrb2ap5',params['dir'])
    if not file or not directory or file[2]!=directory[2] or int(file[3])>240 or int(file[3])%3:
        raise EvidenceError('GEFS_RUN_FILE_PATH_INVALID')
    try:
        datetime.strptime(directory[1],'%Y%m%d')
        left,right,top,bottom=(finite(float(params[k]),nonnegative=False) for k in ('leftlon','rightlon','toplat','bottomlat'))
    except (TypeError,ValueError): raise EvidenceError('GEFS_SUBREGION_INVALID') from None
    if not (0<=left<360 and 0<right-left<=1 and -90<=bottom<top<=90 and top-bottom<=1):
        raise EvidenceError('GEFS_SUBREGION_BOUND')


def field_request(plan,member,hour):
    from .collection import SourceRequest
    if (not isinstance(plan,GEFSPlan) or type(member) is not int or not 0<=member<=30
            or type(hour) is not int or hour not in plan.hours): raise EvidenceError('GEFS_MEMBER_HOUR_PLAN_BOUND')
    run=datetime.fromtimestamp(plan.initialized_at,timezone.utc); m=plan.forecast.metadata
    lon=m.longitude%360; left=math.floor(lon*2)/2; bottom=math.floor(m.latitude*2)/2
    # Include both nearest candidates. Boundary wrap is intentional (right>360).
    params=dict(file=f'ge{"c00" if member==0 else "p"+str(member).zfill(2)}.t{run:%H}z.pgrb2a.0p50.f{hour:03}',
        dir=f'/gefs.{run:%Y%m%d}/{run:%H}/atmos/pgrb2ap5',lev_2_m_above_ground='on',var_TMP='on',subregion='',
        leftlon=str(left),rightlon=str(left+.5),bottomlat=str(bottom),toplat=str(bottom+.5))
    return SourceRequest(PROVIDER,ENDPOINT,plan.event_id,'MODEL',plan.field_identity(member,hour),
        f'gefs-{run:%Y%m%d%H}-{member:02}-{hour:03}',tuple(sorted(params.items())),'GEFS_GRIB2')


def normalize_field(store,raw_id,*,plan,member,hour,record_id):
    raw=store.get(raw_id); b=raw['body']; p=b.get('payload',{}); now=finite(store.clock())
    request=field_request(plan,member,hour)
    fingerprint=digest(dict(plan=asdict(plan),member=member,hour=hour,raw_sha256=raw['sha256'],decoder=DECODER_VERSION))
    try: prior=store.get(record_id)
    except EvidenceError as exc:
        if str(exc)!='EVIDENCE_MISSING': raise
    else:
        if prior['body'].get('payload',{}).get('normalization_sha256')!=fingerprint:
            raise EvidenceError('GEFS_FIELD_REPLAY_CONFLICT')
        return prior
    tip=store.latest(kind='MODEL',event_id=plan.event_id)
    head=store.latest_source(kind='MODEL',event_id=plan.event_id,provider=PROVIDER,source_identity=request.source_identity)
    if (raw['kind']!='MODEL' or raw['event_id']!=plan.event_id or b['provider']!=PROVIDER
            or b['source_identity']!=request.source_identity or head is None or head['id']!=raw_id
            or p.get('endpoint')!=ENDPOINT or p.get('request_params')!=dict(request.params)
            or p.get('response_format')!='GEFS_GRIB2' or p.get('http_status')!=200):
        raise EvidenceError('GEFS_CURRENT_EXACT_RAW_REQUIRED')
    if (b['evidence_class']=='HISTORICAL_AVAILABILITY_UNKNOWN'
            or not plan.initialized_at<=b['received_at']<=b['available_at']<=b['recorded_at']<=now
            or now-b['received_at']>=plan.forecast.maximum_receipt_age_seconds):
        raise EvidenceError('GEFS_RAW_NOT_CAUSAL_OR_FRESH')
    encoded=p.get('response_base64')
    if type(encoded) is not str or len(encoded)>4*((MAX_BYTES+2)//3): raise EvidenceError('GEFS_RAW_BYTES_BOUND')
    try: data=base64.b64decode(encoded,validate=True)
    except (ValueError,TypeError): raise EvidenceError('GEFS_RAW_ENCODING_INVALID') from None
    if hashlib.sha256(data).hexdigest()!=p.get('response_sha256'): raise EvidenceError('GEFS_RAW_HASH_MISMATCH')
    field=decode_gefs_field(data)
    if (field.initialized_at,field.member,field.forecast_hour)!=(plan.initialized_at,member,hour):
        raise EvidenceError('GEFS_REQUEST_BYTES_RUN_MEMBER_HOUR_MISMATCH')
    bounds=dict(request.params);left,right,top,bottom=(float(bounds[k]) for k in ('leftlon','rightlon','toplat','bottomlat'))
    if any(not (bottom<=lat<=top and left<=((lon-left)%360)+left<=right) for lat,lon in field.grid):
        raise EvidenceError('GEFS_RESPONSE_OUTSIDE_REQUESTED_TILE')
    m=plan.forecast.metadata; distances=[geometry(m.latitude,m.longitude,*point)[0] for point in field.grid]
    chosen=min(range(len(distances)),key=lambda i:(distances[i],field.grid[i]))
    if distances[chosen]>plan.forecast.maximum_grid_distance_km: raise EvidenceError('GEFS_GRID_DISTANCE_BOUND')
    payload=dict(version=FIELD_VERSION,normalization_sha256=fingerprint,raw_evidence_id=raw_id,
        raw_evidence_sha256=raw['sha256'],field=asdict(field),chosen_point=field.grid[chosen],
        value_kelvin=field.kelvin[chosen],grid_distance_km=distances[chosen],
        source_time_status='FORECAST_PATH_ASSEMBLY_REQUIRED',financial_authority=False)
    return store._append(record_id,'MODEL',plan.event_id,dict(provider=PROVIDER,source_identity=request.source_identity,
        revision=b['revision'],payload=payload,observed_at=None,issued_at=plan.initialized_at,published_at=None,
        received_at=b['received_at'],evidence_class=b['evidence_class'],source_kind='MODEL'),now,now,
        expected_previous_seq=tip['seq'])


def assemble_path(store,*,plan,field_ids,record_id,deadline=None):
    if not isinstance(plan,GEFSPlan) or type(field_ids) is not tuple or not 1<=len(field_ids)<=MAX_FIELDS or len(set(field_ids))!=len(field_ids):
        raise EvidenceError('GEFS_PATH_INPUT_BOUND')
    fingerprint=digest(dict(plan=asdict(plan),field_ids=field_ids,version=VERSION))
    try: prior=store.get(record_id)
    except EvidenceError as exc:
        if str(exc)!='EVIDENCE_MISSING': raise
    else:
        if prior['body'].get('payload',{}).get('path_sha256')!=fingerprint: raise EvidenceError('GEFS_PATH_REPLAY_CONFLICT')
        return prior
    now=finite(store.clock())
    try:
        view=store.source_batch(kind='MODEL',event_id=plan.event_id,provider=PROVIDER,record_ids=field_ids,raw_lineage=True,
            source_identities=(plan.source_identity,*(plan.field_identity(m,h) for m in range(31) for h in plan.hours)),deadline=deadline)
    except EvidenceError as exc:
        if str(exc)=='SOURCE_VIEW_TIME_BOUND':raise EvidenceError('GEFS_ASSEMBLY_TIME_BOUND') from None
        raise
    tip=view['head'];old=view['channels'].get(plan.source_identity)
    if old and (old['body']['issued_at'] is None or old['body']['issued_at']>plan.initialized_at):
        raise EvidenceError('GEFS_RUN_REPLACEMENT_REQUIRES_NEWER_INITIALIZATION')
    rows=[view['records'][key] for key in field_ids]; fields={}; grid=None; receipts=[]; references=[]
    if old and old['body']['issued_at']==plan.initialized_at and any(r['seq']<=old['seq'] for r in rows):
        raise EvidenceError('GEFS_SAME_RUN_REPLACEMENT_REQUIRES_ALL_NEW_FIELDS')
    for row in rows:
        if deadline is not None and monotonic_time.monotonic()>=deadline: raise EvidenceError('GEFS_ASSEMBLY_TIME_BOUND')
        b=row['body'];p=b.get('payload',{});f=p.get('field',{});member=f.get('member');hour=f.get('forecast_hour')
        request=field_request(plan,member,hour)
        if (row['kind']!='MODEL' or row['event_id']!=plan.event_id or b['provider']!=PROVIDER
                or b['source_identity']!=request.source_identity or p.get('version')!=FIELD_VERSION
                or f.get('initialized_at')!=plan.initialized_at or b['issued_at']!=plan.initialized_at
                or b['evidence_class']=='HISTORICAL_AVAILABILITY_UNKNOWN'
                or not b['received_at']<=b['available_at']<=b['recorded_at']<=now
                or now-b['received_at']>=plan.forecast.maximum_receipt_age_seconds
                or (member,hour) in fields): raise EvidenceError('GEFS_COMPLETE_CURRENT_PATH_REQUIRED')
        head=view['channels'].get(request.source_identity)
        if head is None or head['id']!=row['id']:
            raise EvidenceError('GEFS_FIELD_SUPERSEDED')
        # Same immutable decoder/request fingerprint as normalize_field replay,
        # read consistently instead of opening/scanning the DB for every member.
        raw=view['records'].get(p.get('raw_evidence_id'))
        if raw is None or raw['sha256']!=p.get('raw_evidence_sha256') or p.get('normalization_sha256')!=digest(dict(
                plan=asdict(plan),member=member,hour=hour,raw_sha256=raw['sha256'],decoder=DECODER_VERSION)):
            raise EvidenceError('GEFS_FIELD_REPLAY_CONFLICT')
        current_grid=digest([f['grid'],p['chosen_point']])
        if grid is not None and grid!=current_grid: raise EvidenceError('GEFS_MIXED_GRID_PATH')
        grid=current_grid; fields[member,hour]=finite(p['value_kelvin']); receipts.append(b['received_at'])
        references.append(dict(id=row['id'],sha256=row['sha256'],source_identity=b['source_identity']))
    required={(m,h) for m in range(31) for h in plan.hours}
    if set(fields)!=required: raise EvidenceError('GEFS_ALL_MEMBERS_AND_LOCAL_DAY_BRACKETS_REQUIRED')
    start,end=plan.window; points=[plan.initialized_at+h*3600 for h in plan.hours]; members=[]
    for member in range(31):
        values=[fields[member,h] for h in plan.hours]
        kelvin=linear_extreme(points,values,((start,end),),high=plan.rule.payload['family']==DAILY_HIGH)
        celsius=kelvin-273.15; members.append(celsius*1.8+32 if plan.rule.payload['unit']=='F' else celsius)
    p=plan.rule.payload
    payload=dict(version=VERSION,path_sha256=fingerprint,rule_fingerprint=plan.rule.sha256,
        metadata_fingerprint=plan.forecast.metadata.fingerprint,**{k:p[k] for k in ('station','target_date','family','unit')},
        temperature_input=dict(version=INPUT_VERSION,model_id=MODEL_ID,target_sha256=target_identity(plan.rule,FINAL_EXTREME),members=members),
        field_references=references,coverage=dict(local_day_start=start,local_day_end=end,forecast_hours=list(plan.hours),
            all_31_members=True,method='PIECEWISE_LINEAR_POINT_TEMPERATURE_PATH',unobserved_intrastep_extrema_known=False,
            interpolation_uncertainty_calibrated=False,chosen_point=rows[0]['body']['payload']['chosen_point']),
        run_provenance=dict(status='INITIALIZATION_BOUND_TO_GRIB_BYTES',initialization_at=plan.initialized_at,publication_at=None,
            earliest_alpha_receipt=min(receipts),complete_input_receipt=max(receipts),publication_is_not_initialization=True),
        settlement_authority=False,calibration_label_authority=False,calibrated_probability=False,financial_authority=False)
    if deadline is not None and monotonic_time.monotonic()>=deadline: raise EvidenceError('GEFS_ASSEMBLY_TIME_BOUND')
    return store._append(record_id,'MODEL',plan.event_id,dict(provider=PROVIDER,source_identity=plan.source_identity,
        revision=digest([plan.initialized_at,field_ids]),payload=payload,observed_at=None,issued_at=plan.initialized_at,published_at=None,
        received_at=max(receipts),evidence_class='SYNTHETIC' if any(r['body']['evidence_class']=='SYNTHETIC' for r in rows) else 'PUBLIC_OBSERVED',
        source_kind='MODEL'),now,now,expected_previous_seq=tip['seq'])


def current_path_heads(store,row):
    """One aggregate MODEL CAS guard covers all bounded constituent channels."""
    p=row['body'].get('payload',{})
    from .remaining_forecast import VERSION as REMAINING_VERSION, current_remaining_heads
    if p.get('version') == REMAINING_VERSION:
        return current_remaining_heads(store,row)
    if p.get('version')!=VERSION: return ()
    refs=p.get('field_references')
    if type(refs) is not list or not 1<=len(refs)<=MAX_FIELDS: raise EvidenceError('GEFS_PATH_LINEAGE_INVALID')
    view=store.source_batch(kind='MODEL',event_id=row['event_id'],provider=PROVIDER,
        record_ids=tuple(r['id'] for r in refs),source_identities=tuple(r['source_identity'] for r in refs))
    for ref in refs:
        current=view['channels'].get(ref['source_identity'])
        if current is None or current['id']!=ref['id'] or current['sha256']!=ref['sha256']:
            raise EvidenceError('GEFS_PATH_CONSTITUENT_CHANGED')
    return (('MODEL',row['event_id'],view['head']['seq']),)
