"""Source-only MADIS/AWC/book commissioning, independent of candidate/model gates.

Reviewed local inputs are declarations, not signed provider entitlements. An
operator must carry restriction history into the review; a new ledger is not
permission to retry a restricted provider. No account or evaluator is created.
"""
import asyncio
from dataclasses import asdict, dataclass
import fcntl
import os

from .book_inputs import BookPolicy, PROVIDER as BOOK_PROVIDER, book_request, normalize_book_capture
from .certification import StationMetadata
from .collection import SourceRequest
from .evidence import EvidenceError, digest, finite, identity, sha
from .pws_quality import PWSPolicy, current_neighborhood_heads
from .pws_runtime import PWSQualityPlan, PWSQualitySettings, PWSQualityWorker
from .rules import RuleFingerprint
from .weather_sources import madis_request, normalize_weather_capture

KEY = 'v11-real-input-capture'
PURPOSE = 'NONFINANCIAL_CWOP_AWC_BOOK_CAPTURE'
# Only a failure of the rights-ambiguous provider latches collection shut
# until manual review. An ordinary transient failure on any other provider
# already has its own expiring per-host cooldown in ScheduledCollector's
# SOURCE_SCHEDULE and must not block unrelated, currently-healthy sources.
RIGHTS_SENSITIVE_PROVIDERS = frozenset({'NOAA_MADIS_CWOP'})
# This collector has exactly three reviewed transport providers. New transport
# families need code/review changes, not implicit trust in persisted strings.
KNOWN_CAPTURE_PROVIDERS = RIGHTS_SENSITIVE_PROVIDERS | frozenset({
    'NOAA_AWC', 'POLYMARKET_PUBLIC_CLOB',
})


def _held_providers(details):
    """Recover held providers conservatively, including legacy persisted records.

    An unclassifiable provider is NOT evidence of a harmless transport error.
    Collection rows take precedence for authentic old-format records; if both
    fields are present and disagree, the record is corrupt and stays held.
    """
    if type(details) is not dict:
        return RIGHTS_SENSITIVE_PROVIDERS
    collection = details.get('collection')
    failed = None
    if type(collection) is dict and type(collection.get('sources')) is list:
        sources = collection['sources']
        if len(sources) > 63:
            return RIGHTS_SENSITIVE_PROVIDERS
        failed_rows = set()
        for source in sources:
            if type(source) is not dict:
                return RIGHTS_SENSITIVE_PROVIDERS
            provider, state = source.get('provider'), source.get('state')
            if (type(provider) is not str or provider not in KNOWN_CAPTURE_PROVIDERS
                    or type(state) is not str or not state or len(state) > 128):
                return RIGHTS_SENSITIVE_PROVIDERS
            if state != 'SUCCESS':
                failed_rows.add(provider)
        if failed_rows:
            failed = frozenset(failed_rows)

    held_raw = details.get('held_providers')
    declared = None
    if held_raw is not None:
        if type(held_raw) not in (list, tuple, set, frozenset) or len(held_raw) > 63:
            return RIGHTS_SENSITIVE_PROVIDERS
        if any(type(p) is not str or p not in KNOWN_CAPTURE_PROVIDERS for p in held_raw):
            return RIGHTS_SENSITIVE_PROVIDERS
        if held_raw:
            declared = frozenset(held_raw)

    if failed is not None:
        if declared is not None and declared != failed:
            return RIGHTS_SENSITIVE_PROVIDERS
        return failed
    if declared is not None:
        return declared
    return RIGHTS_SENSITIVE_PROVIDERS


@dataclass(frozen=True)
class InputReview:
    """Exact request/terms/restriction review supplied by the installation owner."""
    requests_sha256: str
    terms_sha256: str
    restriction_lineage_sha256: str
    review_sha256: str
    purpose: str
    restriction_state: str
    valid_from: float
    valid_until: float

    def __post_init__(self):
        for value in (self.requests_sha256, self.terms_sha256,
                      self.restriction_lineage_sha256, self.review_sha256):
            sha(value)
        if (self.purpose != PURPOSE or self.restriction_state != 'CLEAR'
                or not finite(self.valid_from) < finite(self.valid_until)):
            raise EvidenceError('REAL_INPUT_REVIEW_REQUIRED')


@dataclass(frozen=True)
class RealInputPlan:
    rule: RuleFingerprint
    official: StationMetadata
    quality: PWSPolicy
    books: BookPolicy
    collateral_asset: str
    valid_until: float
    official_max_age_seconds: float
    review: InputReview

    def __post_init__(self):
        if (not isinstance(self.rule, RuleFingerprint) or not isinstance(self.official, StationMetadata)
                or not isinstance(self.quality, PWSPolicy) or not isinstance(self.books, BookPolicy)
                or not isinstance(self.review, InputReview)):
            raise EvidenceError('REAL_INPUT_TYPED_PLAN_REQUIRED')
        self.review.__post_init__()
        identity(self.collateral_asset)
        if (self.rule.payload['station'] != self.official.station
                or self.rule.payload['metadata_fingerprint'] != self.official.fingerprint
                or not 0 < finite(self.official_max_age_seconds) <= 3600
                or not 0 < finite(self.valid_until)
                or self.books.maximum_age_seconds > 120):
            raise EvidenceError('REAL_INPUT_SCOPE_OR_FRESHNESS')
        if len(self.requests()) > 63:
            raise EvidenceError('REAL_INPUT_REQUEST_BOUND')
        if digest([asdict(r) for r in self.requests()]) != self.review.requests_sha256:
            raise EvidenceError('REAL_INPUT_REQUEST_REVIEW_MISMATCH')

    def requests(self):
        event = self.rule.payload['event_id']
        # Slow weather first; price snapshots last. Never redate a provider stamp.
        return (madis_request(event_id=event, station=self.official.station,
                    latitude=self.official.latitude, longitude=self.official.longitude),
                SourceRequest('NOAA_AWC', 'https://aviationweather.gov/api/data/metar', event,
                    'OFFICIAL_OBSERVATION', self.official.station, 'LOCAL_RECEIPT',
                    (('ids', self.official.station), ('format', 'json'))),
                *(book_request(event_id=event, token_id=b[side+'_token'], revision='LOCAL_RECEIPT')
                  for b in self.rule.payload['partition'] for side in ('yes', 'no')))

    def preflight(self, now):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        self.__post_init__()
        if (not self.review.valid_from <= finite(now) < min(self.valid_until, self.review.valid_until)
                or datetime.fromtimestamp(now, ZoneInfo(self.official.timezone)).date().isoformat()
                != self.rule.payload['target_date']):
            raise EvidenceError('REAL_INPUT_EVENT_OR_REVIEW_EXPIRED')
        return dict(config_sha256=digest(asdict(self)), requests=len(self.requests()),
                    financial_authority=False, acceptance_granted=False)


class RealInputCapture:
    def __init__(self, scheduled, health, plan, *, sleeper=asyncio.sleep):
        if (not isinstance(plan, RealInputPlan) or scheduled.store is not health.store
                or scheduled.collector.attempts != 1):
            raise EvidenceError('REAL_INPUT_COMPONENT_SCOPE')
        self.scheduled, self.health, self.plan = scheduled, health, plan
        self.sleeper = sleeper
        self.store = scheduled.store
        self.config = digest(asdict(plan))
        self.quality = PWSQualityWorker(self.store, health, PWSQualitySettings((
            PWSQualityPlan(plan.rule.payload['event_id'], plan.official, plan.quality),)))

    def _save(self, key, outcome, *, evidence_ids=(), expected_heads=(), **details):
        return self.store.audit(key, event_id=KEY, kind='RUNTIME_STATUS', details=dict(
            config_sha256=self.config, outcome=outcome, **details, financial_authority=False,
            real_orders_sent=False, settlement_authority=False, acceptance_granted=False),
            evidence_ids=evidence_ids, expected_heads=expected_heads)

    def _transient_deferral_history(self):
        """Bounded chronological scan of this KEY's prior MADIS transient deferrals.

        Only a record this code itself marked transient_madis_deferral=True with
        outcome GATED counts; every other (including legacy/forged) record is
        either ignored (no marker) or treated as a hard failure, never silently
        trusted as "no deferral happened".
        """
        deferrals = []
        rows = self.store.records(kind='RUNTIME_STATUS', event_id=KEY, limit=1000)
        if len(rows) >= 1000:
            # Oldest-first bounded read: a full page could hide newer deferrals.
            raise EvidenceError('REAL_INPUT_MADIS_TRANSIENT_HISTORY_BOUND')
        for row in rows:
            details = row['body']['details']
            marker = details.get('transient_madis_deferral')
            if marker is None:
                continue
            if marker is not True or details.get('outcome') != 'GATED':
                raise EvidenceError('REAL_INPUT_MADIS_TRANSIENT_HISTORY_MALFORMED')
            deferrals.append(row)
        return deferrals

    async def step(self, command_id):
        identity(command_id, maximum=80)
        key = 'real-input:'+digest(command_id)
        fd = os.open(self.store.path.with_name(self.store.path.name+'.real-input.lock'),
                     os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise EvidenceError('REAL_INPUT_ALREADY_RUNNING') from None
            async with asyncio.timeout(60):
                return await self._step(key)
        finally:
            os.close(fd)

    async def _step(self, key):
        head = self.store.latest(kind='RUNTIME_STATUS', event_id=KEY)
        if head and head['body']['details']['config_sha256'] != self.config:
            raise EvidenceError('REAL_INPUT_CONFIG_CHANGED_REVIEW_REQUIRED')
        if head and head['body']['details']['outcome'] == 'STARTED':
            # A crash after sending a request is ambiguous: no unattended retry.
            raise EvidenceError('REAL_INPUT_PROVIDER_OR_INTERRUPTED_HOLD')
        if head and head['body']['details']['outcome'] == 'PROVIDER_HELD' and (
                RIGHTS_SENSITIVE_PROVIDERS & _held_providers(head['body']['details'])):
            raise EvidenceError('REAL_INPUT_PROVIDER_OR_INTERRUPTED_HOLD')
        try:
            previous = self.store.get(key)
        except EvidenceError as exc:
            if str(exc) != 'EVIDENCE_MISSING':
                raise
        else:
            return previous  # Historical result, never a new freshness claim.
        deferrals = self._transient_deferral_history()
        if deferrals and self.store.clock() - deferrals[-1]['body']['recorded_at'] < 1200:
            return self._save(key, 'GATED', errors=['REAL_INPUT_MADIS_TRANSIENT_COOLDOWN'])
        self.plan.preflight(self.store.clock())
        health = self.health.sample(key+':clock')
        if health['body']['details']['clock_reasons']:
            return self._save(key, 'CLOCK_GATED')
        self._save(key+':start', 'STARTED')
        weather = await self.scheduled.cycle('weather:'+digest(key), self.plan.requests()[:2])
        # ScheduledCollector groups hosts; separate phases keep all weather
        # transport ahead of the price snapshots, without changing its scheduler.
        self.plan.preflight(self.store.clock())
        collected = dict(sources=list(weather['sources']), omitted=list(weather['omitted']))
        requests = self.plan.requests()[2:]
        for index in range(0, len(requests), 16):
            if index:
                await self.sleeper(1.05)  # Existing CLOB scheduler's minimum, never a quota override.
            self.plan.preflight(self.store.clock())
            books = await self.scheduled.cycle('books:'+digest([key,index]), requests[index:index+16])
            collected['sources'].extend(books['sources'])
            collected['omitted'].extend(books['omitted'])
        raw_ids, normalized_ids, book_ids, errors = [], [], [], []
        held = False
        failed_sources = []
        for source in collected['sources']:
            if source['state'] != 'SUCCESS':
                held = True
                reason = self.store.get(source['record_id'])['body']['reason']
                errors.append(reason)
                failed_sources.append((source['provider'], source['state'], reason))
                continue
            raw_id = source['capture_ids'][0]
            raw_ids.append(raw_id)
            raw = self.store.get(raw_id)
            try:
                if raw['kind'] == 'BOOK':
                    row = normalize_book_capture(self.store, raw_id, rule=self.plan.rule,
                        token_id=raw['body']['source_identity'], collateral_asset=self.plan.collateral_asset,
                        policy=self.plan.books)
                    book_ids.append(row['id'])
                else:
                    row = normalize_weather_capture(self.store, raw_id, record_id='input-normal:'+digest(raw_id),
                        station=self.plan.official.station, official_max_age_seconds=self.plan.official_max_age_seconds)
                    if not row['body']['payload']['observations']:
                        errors.append('REAL_INPUT_EMPTY_'+raw['kind'])
                normalized_ids.append(row['id'])
            except EvidenceError as exc:
                errors.append(str(exc))
        qc_id = None
        if any(self.store.get(r)['kind'] == 'PWS_OBSERVATION' for r in normalized_ids):
            qc = self.quality.step('qc:'+digest(key))['body']['details']
            qc_id = qc.get('qc_id')
            if not qc_id or self.store.get(qc_id)['body']['payload']['health'] != 'HEALTHY':
                errors.append('REAL_INPUT_PWS_QC_GATED')
        else:
            errors.append('REAL_INPUT_FRESH_PWS_ABSENT')
        if collected['omitted']:
            errors.append('REAL_INPUT_PROVIDER_CADENCE')
        finish_health = self.health.sample(key+':finish-clock')
        event = self.plan.rule.payload['event_id']
        expected_tokens = {b[side+'_token'] for b in self.plan.rule.payload['partition']
                           for side in ('yes', 'no')}
        view = self.store.pin_read_view(tuple((kind,event) for kind in
                                             ('BOOK','OFFICIAL_OBSERVATION','PWS_OBSERVATION')))
        heads = [(p['kind'],p['event_id'],self.store.get(p['record_id'])['seq'] if p['record_id'] else 0)
                 for p in view['heads']]
        try:
            self.plan.preflight(self.store.clock())
            if finish_health['body']['details']['clock_reasons']:
                raise EvidenceError('REAL_INPUT_CLOCK_CHANGED')
            if qc_id:
                qc_row = self.store.get(qc_id)
                metadata_heads = current_neighborhood_heads(self.store, qc_row)
                if len(heads)+len(metadata_heads) > 64:
                    raise EvidenceError('REAL_INPUT_METADATA_GUARD_BOUND')
                heads.extend(metadata_heads)
                payload = qc_row['body']['payload']
                ages = payload['observation_age_seconds']
                if not ages or self.store.clock()-(payload['as_of']-max(ages)) >= self.plan.quality.fresh_seconds:
                    raise EvidenceError('REAL_INPUT_PWS_EXPIRED_BEFORE_PUBLICATION')
            for record_id in normalized_ids:
                row = self.store.get(record_id); body = row['body']
                if row['kind'] == 'BOOK':
                    # A REST /book receipt observes the current snapshot even when
                    # the exchange last generated that snapshot much earlier.
                    # The generation time remains in the normalized audit payload.
                    if (row['event_id'] != event or body['provider'] != BOOK_PROVIDER
                            or body['source_identity'] not in expected_tokens
                            or body['observed_at'] != body['received_at']):
                        raise EvidenceError('REAL_INPUT_BOOK_IDENTITY_CHANGED')
                current = self.store.latest_source(kind=row['kind'], event_id=row['event_id'],
                    provider=body['provider'], source_identity=body['source_identity'])
                if current is None or current['id'] != record_id:
                    raise EvidenceError('REAL_INPUT_SOURCE_CHANGED')
                bound = (self.plan.books.maximum_age_seconds if row['kind'] == 'BOOK' else
                         self.plan.quality.fresh_seconds if row['kind'] == 'PWS_OBSERVATION' else
                         self.plan.official_max_age_seconds)
                age = self.store.clock()-body['observed_at'] if body['observed_at'] is not None else None
                fresh = (age is not None and 0 <= age <= bound if row['kind'] == 'BOOK' else
                         age is not None and 0 <= age < bound)
                if not fresh:
                    raise EvidenceError('REAL_INPUT_SOURCE_EXPIRED_BEFORE_PUBLICATION')
        except EvidenceError as exc:
            errors.append(str(exc))
        if len(normalized_ids) != len(self.plan.requests()):
            errors.append('REAL_INPUT_INCOMPLETE_COVERAGE')
        held_providers = sorted({source['provider'] for source in collected['sources']
                                  if source['state'] != 'SUCCESS'})
        # A bare transport timeout carries no provider refusal or rate signal.
        # Defer it (never a permanent hold) up to twice; a 3rd occurrence, or any
        # mix with a genuine refusal/rate-limit/other provider, holds as today.
        transient_madis_only = held and all(
            provider == 'NOAA_MADIS_CWOP' and state == 'TRANSPORT_FAILURE' and reason == 'REQUEST_FAILED'
            for provider, state, reason in failed_sources)
        if transient_madis_only and len(self._transient_deferral_history()) < 2:
            return self._save(key, 'GATED', raw_ids=raw_ids, normalized_ids=normalized_ids, book_ids=book_ids,
                              qc_id=qc_id, errors=errors+['REAL_INPUT_MADIS_TRANSIENT_TRANSPORT_DEFERRED'],
                              collection=collected, held_providers=[], transient_madis_deferral=True,
                              evidence_ids=tuple(normalized_ids)+((qc_id,) if qc_id else ()),
                              expected_heads=tuple(heads))
        return self._save(key, 'PROVIDER_HELD' if held else 'GATED' if errors else 'FRESH_SOURCE_EVIDENCE_ONLY',
                          raw_ids=raw_ids, normalized_ids=normalized_ids, book_ids=book_ids,
                          qc_id=qc_id, errors=errors, collection=collected, held_providers=held_providers,
                          evidence_ids=tuple(normalized_ids)+((qc_id,) if qc_id else ()),
                          expected_heads=tuple(heads))
