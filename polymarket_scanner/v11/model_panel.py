"""Pull-only forecast contracts and causal archive boundary; no learner transport.

Point temperatures are inputs to a later, reviewed local-day path assembler.
They are never settlement observations, calibrated probabilities or votes.
"""
from dataclasses import asdict, dataclass
from datetime import date
import base64
import hashlib
import json
from zoneinfo import ZoneInfo

from .evidence import CLASSES, EvidenceError, canonical, digest, finite, identity, sha
from .pws_quality import geometry

VERSION = 'alpha_v11_model_panel_v1'
MAX_RAW_BYTES = 4 * 1024 * 1024
MAX_MEMBERS = 64
PROVIDERS = {
    'NOAA_GEFS': ('NOAA', 'gefs', 31),
    'ECMWF_IFS_ENS': ('ECMWF', 'ifs', 51),
    'ECMWF_AIFS_ENS': ('ECMWF', 'aifs-ens', 51),
    'GOOGLE_WEATHERNEXT3': ('GOOGLE', 'weathernext_3_0_0', 64),
    'ECMWF_AIFS_SINGLE': ('ECMWF', 'aifs-single', 1),
}


def strict_json(raw, maximum=MAX_RAW_BYTES):
    if type(raw) is not bytes or not 0 < len(raw) <= maximum:
        raise EvidenceError('PANEL_RAW_BYTES_BOUND')
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise EvidenceError('PANEL_DUPLICATE_JSON_KEY')
            result[key] = value
        return result
    try:
        value = json.loads(raw, object_pairs_hook=pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(EvidenceError('PANEL_NONFINITE_JSON')))
        canonical(value)
        return value
    except (ValueError, TypeError, RecursionError, UnicodeError):
        raise EvidenceError('PANEL_JSON_INVALID') from None


@dataclass(frozen=True)
class SourceIdentity:
    provider: str
    model_version: str
    dataset: str
    release_evidence_sha256: str

    def __post_init__(self):
        if self.provider not in PROVIDERS:
            raise EvidenceError('PANEL_PROVIDER_UNREVIEWED')
        for value in (self.model_version, self.dataset):
            identity(value)
        if self.model_version.lower() in {'latest', 'unknown', 'current'}:
            raise EvidenceError('PANEL_EXACT_MODEL_VERSION_REQUIRED')
        sha(self.release_evidence_sha256)

    @property
    def family(self): return PROVIDERS[self.provider][0]

    @property
    def model(self): return PROVIDERS[self.provider][1]

    @property
    def member_count(self): return PROVIDERS[self.provider][2]

    @property
    def key(self): return 'WEATHER_SOURCE:' + digest(asdict(self))

    @property
    def dependence(self):
        # One shared uncertainty domain, not a correlation estimate. NOAA also
        # remains dependent on the common atmosphere; family names are not votes.
        return dict(independence='NEVER_ASSUMED', combination='REQUIRES_HELD_OUT_DEPENDENCE_VALIDATION',
                    shared_domain='GLOBAL_ATMOSPHERE', provider_family=self.family,
                    ecmwf_analysis_or_training_lineage=self.provider != 'NOAA_GEFS',
                    vote_family='AIFS' if 'AIFS' in self.provider else self.model,
                    auxiliary_only=self.provider == 'ECMWF_AIFS_SINGLE', independent_vote=False)


@dataclass(frozen=True)
class StationTarget:
    event_id: str
    station: str
    target_date: str
    timezone: str
    latitude: float
    longitude: float
    metadata_fingerprint: str
    rule_fingerprint: str
    maximum_grid_distance_km: float = 50.
    output_unit: str = 'C'

    def __post_init__(self):
        identity(self.event_id); identity(self.station)
        try:
            if date.fromisoformat(self.target_date).isoformat() != self.target_date:
                raise ValueError()
            ZoneInfo(self.timezone)
        except (ValueError, KeyError, TypeError):
            raise EvidenceError('PANEL_DATE_OR_TIMEZONE_INVALID') from None
        coordinates(self.latitude, self.longitude)
        sha(self.metadata_fingerprint); sha(self.rule_fingerprint)
        if not 0 < finite(self.maximum_grid_distance_km) <= 200 or self.output_unit not in {'C', 'F', 'K'}:
            raise EvidenceError('PANEL_STATION_POLICY_INVALID')


def coordinates(lat, lon):
    if not -90 <= finite(lat, nonnegative=False) <= 90 or not -180 <= finite(lon, nonnegative=False) <= 180:
        raise EvidenceError('PANEL_COORDINATE_INVALID')


def temperature(value, source_unit, target_unit):
    value = finite(value, nonnegative=False)
    if source_unit not in {'K', 'C', 'F'} or target_unit not in {'K', 'C', 'F'}:
        raise EvidenceError('PANEL_TEMPERATURE_UNIT_INVALID')
    kelvin = value if source_unit == 'K' else value + 273.15 if source_unit == 'C' else (value-32)/1.8+273.15
    if not 150 <= kelvin <= 350:
        raise EvidenceError('PANEL_TEMPERATURE_BOUND')
    return kelvin if target_unit == 'K' else kelvin-273.15 if target_unit == 'C' else (kelvin-273.15)*1.8+32


@dataclass(frozen=True)
class MemberObservation:
    member_id: int
    value: float

    def __post_init__(self):
        if type(self.member_id) is not int or not 0 <= self.member_id < MAX_MEMBERS:
            raise EvidenceError('PANEL_MEMBER_BOUND')
        finite(self.value, nonnegative=False)


@dataclass(frozen=True)
class ForecastSlice:
    source: SourceIdentity
    target: StationTarget
    initialized_at: float
    valid_at: float
    received_at: float
    available_at: float
    published_at: float | None
    evidence_class: str
    grid_latitude: float
    grid_longitude: float
    grid_identity: str
    source_unit: str
    members: tuple[MemberObservation, ...]
    statistics: tuple[tuple[str, float], ...]
    raw_id: str
    raw_sha256: str
    response_sha256: str

    def __post_init__(self):
        if not isinstance(self.source, SourceIdentity) or not isinstance(self.target, StationTarget):
            raise EvidenceError('PANEL_TYPED_CONTEXT_REQUIRED')
        times = [finite(v) for v in (self.initialized_at, self.valid_at, self.received_at, self.available_at)]
        if not times[0] <= times[2] <= times[3] or not times[0] <= times[1] <= times[0]+360*3600:
            raise EvidenceError('PANEL_CAUSAL_TIME_INVALID')
        if self.published_at is not None and not times[0] <= finite(self.published_at) <= times[2]:
            raise EvidenceError('PANEL_PUBLICATION_TIME_INVALID')
        if self.evidence_class not in CLASSES:
            raise EvidenceError('PANEL_EVIDENCE_CLASS_INVALID')
        coordinates(self.grid_latitude, self.grid_longitude)
        if geometry(self.target.latitude, self.target.longitude, self.grid_latitude, self.grid_longitude)[0] > self.target.maximum_grid_distance_km:
            raise EvidenceError('PANEL_GRID_DISTANCE_BOUND')
        identity(self.grid_identity); identity(self.raw_id)
        sha(self.raw_sha256); sha(self.response_sha256)
        if self.source_unit not in {'C', 'F', 'K'}:
            raise EvidenceError('PANEL_TEMPERATURE_UNIT_INVALID')
        if (type(self.members) is not tuple or len(self.members) > self.source.member_count
                or any(not isinstance(m, MemberObservation) or m.member_id >= self.source.member_count for m in self.members)
                or len({m.member_id for m in self.members}) != len(self.members)):
            raise EvidenceError('PANEL_MEMBER_COUNT_OR_DUPLICATE')
        if type(self.statistics) is not tuple or len(self.statistics) > 6 or bool(self.members) == bool(self.statistics):
            raise EvidenceError('PANEL_MEMBERS_OR_STATISTICS_REQUIRED')
        if any(type(pair) is not tuple or len(pair) != 2 for pair in self.statistics):
            raise EvidenceError('PANEL_STATISTICS_SCHEMA')
        names = [k for k, _ in self.statistics]
        if len(set(names)) != len(names) or set(names)-{'mean', 'p10', 'p25', 'p50', 'p75', 'p90'}:
            raise EvidenceError('PANEL_STATISTICS_SCHEMA')
        for value in [m.value for m in self.members] + [v for _, v in self.statistics]:
            temperature(value, self.target.output_unit, 'K')
        object.__setattr__(self, 'members', tuple(sorted(self.members, key=lambda m: m.member_id)))
        object.__setattr__(self, 'statistics', tuple(sorted(self.statistics)))

    def require_causal(self, cutoff, *, allow_synthetic=False):
        if self.evidence_class == 'HISTORICAL_AVAILABILITY_UNKNOWN':
            raise EvidenceError('PANEL_HISTORICAL_AVAILABILITY_UNKNOWN')
        if self.evidence_class == 'SYNTHETIC' and not allow_synthetic:
            raise EvidenceError('PANEL_SYNTHETIC_NOT_LIVE_EVIDENCE')
        if self.available_at > finite(cutoff):
            raise EvidenceError('PANEL_NOT_AVAILABLE_AT_CUTOFF')
        return self

    @property
    def payload(self):
        return dict(version=VERSION, forecast=asdict(self), source_identity=self.source.key,
                    provider_family=self.source.family, model=self.source.model,
                    run_identity=digest([self.source.key, self.initialized_at]),
                    slice_identity=digest([self.source.key, asdict(self.target), self.initialized_at, self.valid_at,
                                           self.grid_identity, [m.member_id for m in self.members],
                                           [k for k, _ in self.statistics]]),
                    member_count=self.source.member_count, unit=self.target.output_unit,
                    dependence=self.source.dependence, target_semantics='POINT_2M_TEMPERATURE',
                    source_admission='GATED_DAY_EXTREME_ASSEMBLY_AND_CALIBRATION_REQUIRED',
                    complete_members=len(self.members) == self.source.member_count,
                    settlement_authority=False, calibration_label_authority=False,
                    calibrated_probability=False, financial_authority=False, order_authority=False)


def archive_raw(store, record_id, *, source, target, request, raw, initialized_at,
                evidence_class='HISTORICAL_AVAILABILITY_UNKNOWN'):
    """Imported data defaults to unknown availability; capture time is never backdated."""
    if type(raw) is not bytes or not 0 < len(raw) <= MAX_RAW_BYTES:
        raise EvidenceError('PANEL_RAW_BYTES_BOUND')
    payload = dict(version=VERSION, source=asdict(source), target=asdict(target), request=request,
                   response_base64=base64.b64encode(raw).decode(), response_sha256=hashlib.sha256(raw).hexdigest())
    return store.capture(record_id, event_id=target.event_id, kind='MODEL', provider=source.provider,
                         source_identity=source.key, revision=digest(payload), payload=payload,
                         issued_at=initialized_at, evidence_class=evidence_class)


def read_raw(store, raw_id, *, source, target, request):
    row = store.get(raw_id); body = row['body']; p = body.get('payload', {})
    if (row['kind'] != 'MODEL' or row['event_id'] != target.event_id or body.get('provider') != source.provider
            or body.get('source_identity') != source.key or p.get('version') != VERSION
            or p.get('source') != asdict(source) or p.get('target') != asdict(target)
            or canonical(p.get('request')) != canonical(request)):
        raise EvidenceError('PANEL_EXACT_SOURCE_VERSION_REQUEST_REQUIRED')
    times = [finite(body[k]) for k in ('issued_at', 'received_at', 'available_at', 'recorded_at')]
    if not 0 <= times[0] <= times[1] <= times[2] <= times[3] <= finite(store.clock()):
        raise EvidenceError('PANEL_RAW_CAUSAL_ORDER')
    encoded = p.get('response_base64')
    if type(encoded) is not str or len(encoded) > 4*((MAX_RAW_BYTES+2)//3):
        raise EvidenceError('PANEL_RAW_BYTES_BOUND')
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError):
        raise EvidenceError('PANEL_RAW_ENCODING') from None
    if not 0 < len(raw) <= MAX_RAW_BYTES or hashlib.sha256(raw).hexdigest() != p.get('response_sha256'):
        raise EvidenceError('PANEL_RAW_HASH_MISMATCH')
    return row, raw


def persist_slice(store, record_id, forecast):
    """Derived availability is normalization completion, never initialization."""
    if reproduce_slice(store, forecast) != forecast:
        raise EvidenceError('PANEL_DERIVED_VALUES_DO_NOT_REPLAY')
    payload = forecast.payload
    payload['normalization_sha256'] = digest(payload)
    try:
        prior = store.get(record_id)
    except EvidenceError as exc:
        if str(exc) != 'EVIDENCE_MISSING': raise
    else:
        if prior['body'].get('payload') != json.loads(canonical(payload)):
            raise EvidenceError('PANEL_NORMALIZATION_REPLAY_CONFLICT')
        return prior
    now = finite(store.clock())
    if forecast.available_at > now:
        raise EvidenceError('PANEL_NORMALIZATION_IN_FUTURE')
    return store._append(record_id, 'MODEL', forecast.target.event_id,
        dict(provider=forecast.source.provider, source_identity=forecast.source.key,
             revision=payload['normalization_sha256'], payload=payload, observed_at=None,
             issued_at=forecast.initialized_at, published_at=forecast.published_at,
             received_at=forecast.received_at, evidence_class=forecast.evidence_class, source_kind='MODEL'), now, now)


def normalize_gefs_panel(store, raw_id, *, source, plan, member, hour, record_id):
    """Compatibility bridge through the unchanged GEFS validation/decoder path."""
    from .gefs_sources import normalize_field
    if source.provider != 'NOAA_GEFS' or source.dataset != 'noaa-nomads:gefs:0p50':
        raise EvidenceError('PANEL_GEFS_SOURCE_REQUIRED')
    normalized = normalize_field(store, raw_id, plan=plan, member=member, hour=hour, record_id=record_id)
    b = normalized['body']; p = b['payload']; rule = plan.rule.payload; m = plan.forecast.metadata
    target = StationTarget(plan.event_id, m.station, rule['target_date'], m.timezone, m.latitude, m.longitude,
                           m.fingerprint, plan.rule.sha256, plan.forecast.maximum_grid_distance_km, rule['unit'])
    raw = store.get(raw_id)
    if raw['sha256'] != p['raw_evidence_sha256']:
        raise EvidenceError('PANEL_GEFS_RAW_LINEAGE_MISMATCH')
    return ForecastSlice(source, target, plan.initialized_at, plan.initialized_at+hour*3600,
        b['received_at'], b['available_at'], b['published_at'], b['evidence_class'],
        *p['chosen_point'], digest(p['field']['grid']), 'K',
        (MemberObservation(member, temperature(p['value_kelvin'], 'K', target.output_unit)),), (),
        raw_id, raw['sha256'], raw['body']['payload']['response_sha256'])


def reproduce_slice(store, forecast):
    """Recompute provider output from raw bytes; never trust a derived value."""
    raw = store.get(forecast.raw_id)
    if raw['sha256'] != forecast.raw_sha256:
        raise EvidenceError('PANEL_RAW_LINEAGE_MISMATCH')
    request = raw['body'].get('payload', {}).get('request', {})
    if forecast.source.provider in {'ECMWF_IFS_ENS', 'ECMWF_AIFS_ENS'}:
        from .ecmwf_sources import ECMWFRequest, normalize_ecmwf
        try:
            selectors = request['selectors']
            req = ECMWFRequest(forecast.source, forecast.initialized_at, int(selectors['step']),
                               int(selectors['number']), request['grib_signature_sha256'])
            index_id = request['index_id']
        except (KeyError, ValueError, TypeError):
            raise EvidenceError('PANEL_ECMWF_REPLAY_REQUEST') from None
        return normalize_ecmwf(store, forecast.raw_id, request=req, target=forecast.target, index_id=index_id)
    if forecast.source.provider == 'GOOGLE_WEATHERNEXT3':
        from .weathernext_sources import WeatherNextRequest, normalize_weathernext
        try:
            req = WeatherNextRequest(forecast.source, request['initialized_at'], request['lead_hour'],
                                     request['variable'], request['representation'], request['surface'])
        except (KeyError, ValueError, TypeError):
            raise EvidenceError('PANEL_WEATHERNEXT_REPLAY_REQUEST') from None
        return normalize_weathernext(store, forecast.raw_id, request=req, target=forecast.target)
    raise EvidenceError('PANEL_GEFS_USE_EXISTING_ARCHIVE')


def replay_normalized(store, record_id, *, source, target, cutoff, allow_synthetic=False):
    """Offline learning/assembly reader; honors original feature completion time.

    A new model version requires a new caller pin and source identity. Reading a
    point slice does not make it eligible for the existing daily-payout learner.
    """
    row = store.get(record_id); body = row['body']; p = body.get('payload', {})
    if (row['kind'] != 'MODEL' or row['event_id'] != target.event_id
            or body.get('source_identity') != source.key or body.get('provider') != source.provider
            or p.get('version') != VERSION or body['available_at'] > finite(cutoff)
            or body['recorded_at'] > cutoff or cutoff > finite(store.clock())):
        raise EvidenceError('PANEL_NORMALIZED_CONTEXT_OR_CUTOFF')
    try:
        d = dict(p['forecast'])
        if d.pop('source') != asdict(source) or d.pop('target') != asdict(target):
            raise EvidenceError('PANEL_NORMALIZED_SOURCE_CHANGED')
        d['members'] = tuple(MemberObservation(**m) for m in d['members'])
        d['statistics'] = tuple(tuple(pair) for pair in d['statistics'])
        forecast = ForecastSlice(source=source, target=target, **d)
    except (TypeError, KeyError, ValueError):
        raise EvidenceError('PANEL_NORMALIZED_SCHEMA') from None
    expected = forecast.payload
    expected['normalization_sha256'] = digest(expected)
    if (canonical(expected) != canonical(p) or reproduce_slice(store, forecast) != forecast
            or body['evidence_class'] != forecast.evidence_class
            or body['received_at'] != forecast.received_at or body['issued_at'] != forecast.initialized_at
            or body['available_at'] < forecast.available_at):
        raise EvidenceError('PANEL_NORMALIZATION_REPLAY_CONFLICT')
    return forecast.require_causal(cutoff, allow_synthetic=allow_synthetic)
