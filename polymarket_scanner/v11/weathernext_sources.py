"""WeatherNext 3 archive slice schema. Access and cloud decoding stay external.

No GCS/BigQuery/Earth Engine client, credentials, billing setup or inference.
The connector seam accepts a bounded archived extraction, with parent hashes;
statistics remain statistics and can never masquerade as ensemble members.
"""
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Protocol

from .evidence import EvidenceError, finite, identity, sha
from .model_panel import (SourceIdentity, StationTarget, ForecastSlice, MemberObservation,
                          archive_raw, read_raw, strict_json, temperature)

SCHEMA = 'alpha_v11_weathernext3_station_slice_v1'
MAX_SLICE_BYTES = 64 * 1024
ENSEMBLE_DATASET = 'gs://weathernext3_spatial/weathernext_3_0_0/zarr/'
STATISTICS_DATASET = 'gs://weathernext3_statistics_spatial/weathernext_3_0_0_statistics/zarr/'


class AccessState(str, Enum):
    EXTERNAL_ACCESS_REQUIRED = 'EXTERNAL_ACCESS_REQUIRED'
    DENIED = 'DENIED'
    NOT_AVAILABLE = 'NOT_AVAILABLE'
    REVIEWED = 'REVIEWED'


@dataclass(frozen=True)
class WeatherNextRequest:
    source: SourceIdentity
    initialized_at: float
    lead_hour: int
    variable: str = 'station_head_temperature_2m'
    representation: str = 'MEMBERS'
    surface: str = 'GCS_ZARR'

    def __post_init__(self):
        if not isinstance(self.source, SourceIdentity) or self.source.provider != 'GOOGLE_WEATHERNEXT3' or self.source.model_version != '3.0.0':
            raise EvidenceError('WEATHERNEXT_SOURCE_VERSION_CHANGED')
        if self.variable not in {'station_head_temperature_2m', 'temperature_2m'}:
            raise EvidenceError('WEATHERNEXT_2M_TEMPERATURE_REQUIRED')
        if self.surface not in {'GCS_ZARR', 'BIGQUERY', 'EARTH_ENGINE'} or self.representation not in {'MEMBERS', 'STATISTICS'}:
            raise EvidenceError('WEATHERNEXT_SURFACE_SCHEMA')
        if self.representation == 'MEMBERS' and self.surface != 'GCS_ZARR':
            raise EvidenceError('WEATHERNEXT_STATISTICS_ARE_NOT_MEMBERS')
        dataset = ENSEMBLE_DATASET if self.representation == 'MEMBERS' else STATISTICS_DATASET
        if self.source.dataset != dataset:
            raise EvidenceError('WEATHERNEXT_DATASET_CHANGED')
        run = datetime.fromtimestamp(finite(self.initialized_at), timezone.utc)
        if run.minute or run.second or self.initialized_at % 1:
            raise EvidenceError('WEATHERNEXT_HOURLY_INITIALIZATION_REQUIRED')
        limit = 360 if run.hour % 6 == 0 else 48
        if type(self.lead_hour) is not int or not 0 <= self.lead_hour <= limit:
            raise EvidenceError('WEATHERNEXT_HORIZON_BOUND')

    @property
    def identity(self): return dict(schema=SCHEMA, **asdict(self))


class ReviewedSliceTransport(Protocol):
    """Future source-layer connector: bounded raw extraction, never learner I/O.

    Implementation/configuration requires separate review, including entitlement,
    requester-pays cost policy and raw-parent retention. No implementation here.
    """
    async def pull(self, request: WeatherNextRequest, target: StationTarget, *, max_bytes: int) -> bytes: ...


def access_gate(state=AccessState.EXTERNAL_ACCESS_REQUIRED, *, historical=False, transport=None):
    if not isinstance(state, AccessState):
        raise EvidenceError('WEATHERNEXT_ACCESS_STATE_INVALID')
    if historical: return 'EXTERNAL_ACCESS_REQUIRED'
    if state != AccessState.REVIEWED: return state.value
    if transport is None: return 'NOT_AVAILABLE'
    # No credentialed connector has been reviewed/shipped in this build. Merely
    # passing an arbitrary object or a bool must not manufacture live access.
    return 'CONNECTOR_REVIEW_REQUIRED'


def archive_fixture(store, record_id, *, request, target, raw):
    """Explicit synthetic fixture path; cannot upgrade fixtures to live evidence."""
    if type(raw) is not bytes or not 0 < len(raw) <= MAX_SLICE_BYTES:
        raise EvidenceError('WEATHERNEXT_SLICE_BYTES_BOUND')
    return archive_raw(store, record_id, source=request.source, target=target, request=request.identity,
                       raw=raw, initialized_at=request.initialized_at, evidence_class='SYNTHETIC')


def normalize_weathernext(store, raw_id, *, request, target):
    row, raw = read_raw(store, raw_id, source=request.source, target=target, request=request.identity)
    if row['body']['evidence_class'] == 'PUBLIC_OBSERVED':
        raise EvidenceError('WEATHERNEXT_CONNECTOR_REVIEW_REQUIRED')
    if row['body']['issued_at'] != request.initialized_at:
        raise EvidenceError('WEATHERNEXT_ARCHIVE_RUN_MISMATCH')
    d = strict_json(raw, MAX_SLICE_BYTES)
    keys = {'schema', 'dataset', 'model_version', 'generation', 'initialized_at', 'valid_at',
            'lead_time_hours', 'lead_subtime_hours', 'variable', 'unit', 'member_count',
            'latitude', 'longitude', 'representation', 'members', 'statistics', 'parent_sha256s'}
    if type(d) is not dict or set(d) != keys:
        raise EvidenceError('WEATHERNEXT_SLICE_SCHEMA_CHANGED')
    identity(d['generation']); parents = d['parent_sha256s']
    if type(parents) is not list or not 1 <= len(parents) <= 16:
        raise EvidenceError('WEATHERNEXT_PARENT_PROVENANCE_BOUND')
    for value in parents: sha(value)
    if len(set(parents)) != len(parents): raise EvidenceError('WEATHERNEXT_DUPLICATE_PARENT')
    if (d['schema'] != SCHEMA or d['dataset'] != request.source.dataset or d['model_version'] != request.source.model_version
            or d['variable'] != request.variable or d['representation'] != request.representation
            or d['initialized_at'] != request.initialized_at or d['unit'] != 'K'
            or type(d['member_count']) is not int or d['member_count'] != 64):
        raise EvidenceError('WEATHERNEXT_SOURCE_VERSION_OR_RUN_MISMATCH')
    lead, sub = d['lead_time_hours'], d['lead_subtime_hours']
    if type(lead) is not int or type(sub) is not int or lead < 0:
        raise EvidenceError('WEATHERNEXT_TIME_COORDINATES')
    if request.representation == 'MEMBERS':
        # Preserve signed subtime exactly; coordinates, not array position,
        # determine validity (provider layouts can use preceding hourly offsets).
        if lead % 6 or not -5 <= sub <= 5:
            raise EvidenceError('WEATHERNEXT_TIME_COORDINATES')
    elif sub != 0:
        raise EvidenceError('WEATHERNEXT_STATISTICS_TIME_COORDINATES')
    if lead+sub != request.lead_hour or finite(d['valid_at']) != request.initialized_at+request.lead_hour*3600:
        raise EvidenceError('WEATHERNEXT_VALID_TIME_MISMATCH')
    lat, lon = finite(d['latitude'], nonnegative=False), finite(d['longitude'], nonnegative=False)
    if not -90 <= lat <= 90 or not 0 <= lon < 360:
        raise EvidenceError('WEATHERNEXT_GRID_COORDINATES')
    spacing = .05 if request.variable == 'station_head_temperature_2m' else .1
    if abs(lat/spacing-round(lat/spacing)) > 1e-6 or abs(lon/spacing-round(lon/spacing)) > 1e-6:
        raise EvidenceError('WEATHERNEXT_GRID_RESOLUTION_CHANGED')
    if type(d['members']) is not list or type(d['statistics']) is not dict:
        raise EvidenceError('WEATHERNEXT_REPRESENTATION_SCHEMA')
    members = (); statistics = ()
    if request.representation == 'MEMBERS':
        if len(d['members']) != 64 or d['statistics']:
            raise EvidenceError('WEATHERNEXT_COMPLETE_64_MEMBERS_REQUIRED')
        if any(type(m) is not dict or set(m) != {'member', 'value'} for m in d['members']):
            raise EvidenceError('WEATHERNEXT_MEMBER_SCHEMA')
        members = tuple(MemberObservation(m['member'], temperature(m['value'], 'K', target.output_unit)) for m in d['members'])
        if {m.member_id for m in members} != set(range(64)):
            raise EvidenceError('WEATHERNEXT_MEMBER_IDENTITY_SET')
    else:
        if d['members'] or set(d['statistics']) != {'mean', 'p10', 'p25', 'p50', 'p75', 'p90'}:
            raise EvidenceError('WEATHERNEXT_STATISTICS_SCHEMA')
        quantiles = [finite(d['statistics'][k]) for k in ('p10', 'p25', 'p50', 'p75', 'p90')]
        if quantiles != sorted(quantiles): raise EvidenceError('WEATHERNEXT_QUANTILE_ORDER')
        statistics = tuple((k, temperature(v, 'K', target.output_unit)) for k, v in d['statistics'].items())
    b = row['body']
    from .evidence import digest
    grid_id = digest(dict(variable=request.variable, resolution=spacing, latitude=lat, longitude=lon,
                          generation=d['generation'], parents=parents))
    return ForecastSlice(request.source, target, request.initialized_at, d['valid_at'], b['received_at'],
        b['available_at'], b['published_at'], b['evidence_class'], lat, (lon+180)%360-180, grid_id, 'K',
        members, statistics, row['id'], row['sha256'], b['payload']['response_sha256'])
