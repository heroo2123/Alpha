"""Exact archived coverage -> unresolved GEFS path -> same-day input pair.

The source adapter must explicitly supply coverage of the contract observation
population. A sequence of point reports, a METAR proxy or a PWS neighborhood does
not establish that coverage. This module neither acquires nor certifies it.
Forecast interpolation stays uncalibrated and conditional on the exact revision.
"""
from dataclasses import asdict
import time

from ..weather_only_contracts import DAILY_HIGH
from .evidence import EvidenceError, digest, finite, identity
from .probability import ObservedConstraint, UNRESOLVED_EXTREME, target_identity
from .strategy_pipeline import CONDITION_VERSION, COVERAGE_VERSION, INPUT_VERSION


VERSION = 'alpha_v11_gefs_remaining_path_v1'
OBSERVATION_COVERAGE_VERSION = 'alpha_v11_exact_observation_intervals_v1'
PROVIDER = 'ALPHA_GEFS_REMAINING'
MODEL_ID = 'NOAA_GEFS_0P50_LINEAR_UNRESOLVED_V1'


def _observation(row, plan, at):
    b = row['body']; p = b.get('payload', {}); r = plan.rule.payload
    value = p.get('exact_extreme', {})
    if (row['kind'] != 'OFFICIAL_OBSERVATION' or row['event_id'] != plan.event_id
            or b.get('evidence_class') not in {'PUBLIC_OBSERVED','SYNTHETIC'}
            or b['observed_at'] is None or b['observed_at'] > at
            or p.get('rule_fingerprint') != plan.rule.sha256
            or any(p.get(k) != r[k] for k in ('station','target_date','family','unit','observation_population','source_family'))
            or type(value) is not dict or set(value) != {'version','whole_degree_value','source_role'}
            or value['version'] != CONDITION_VERSION):
        raise EvidenceError('REMAINING_EXACT_OBSERVATION_REQUIRED')
    ObservedConstraint(plan.rule.sha256,p['station'],p['observation_population'],p['unit'],p['family'],
        value['whole_degree_value'],b['received_at'],b['available_at'],b['revision'],row['sha256'],
        value['source_role']).validate(plan.rule,at)
    coverage = p.get('observation_coverage')
    if (type(coverage) is not dict or set(coverage) != {'version','accepted_intervals'}
            or coverage['version'] != OBSERVATION_COVERAGE_VERSION):
        raise EvidenceError('EXACT_POPULATION_INTERVAL_COVERAGE_UNAVAILABLE')
    intervals = coverage['accepted_intervals']; start, end = plan.window
    if (type(intervals) is not list or not 1 <= len(intervals) <= 499
            or any(type(pair) is not list or len(pair) != 2 for pair in intervals)):
        raise EvidenceError('EXACT_POPULATION_INTERVAL_BOUND')
    accepted = sorted((finite(a),finite(b)) for a,b in intervals)
    cursor = start; unresolved = []
    for a,b in accepted:
        if not cursor <= a < b <= min(at,row['body']['observed_at'],end):
            raise EvidenceError('EXACT_POPULATION_INTERVAL_OVERLAP_OR_FUTURE')
        if cursor < a: unresolved.append((cursor,a))
        cursor = b
    if cursor < end: unresolved.append((cursor,end))
    if not start <= at < end or not unresolved:
        raise EvidenceError('REMAINING_SAME_DAY_WINDOW_REQUIRED')
    return accepted, unresolved


def current_remaining_heads(store, row):
    """Reject changed parent fields or any new official arrival before admission."""
    from .gefs_sources import VERSION as PATH_VERSION, current_path_heads
    p = row['body']['payload']; context = p.get('remaining_context', {})
    refs = p.get('dependencies')
    if (p.get('version') != VERSION or type(refs) is not list or len(refs) != 2
            or any(type(r) is not dict or set(r) != {'id','sha256'} for r in refs)):
        raise EvidenceError('REMAINING_PATH_DERIVATION_REQUIRED')
    official_head = store.latest(kind='OFFICIAL_OBSERVATION',event_id=row['event_id'])
    parent, observation = (store.get(ref['id']) for ref in refs)
    if (any(r['sha256'] != ref['sha256'] or r['seq'] >= row['seq']
            or r['event_id'] != row['event_id'] or r['body']['available_at'] > row['body']['available_at']
            for r,ref in zip((parent,observation),refs))
            or parent['body'].get('payload',{}).get('version') != PATH_VERSION
            or observation['kind'] != 'OFFICIAL_OBSERVATION' or not official_head
            or official_head['id'] != observation['id']
            or context.get('observation_id') != observation['id']
            or context.get('observation_sha256') != observation['sha256']):
        raise EvidenceError('REMAINING_PATH_OR_OFFICIAL_CHANGED')
    b = parent['body']
    head = store.latest_source(kind='MODEL',event_id=row['event_id'],provider=b['provider'],source_identity=b['source_identity'])
    if head is None or head['id'] != parent['id']:
        raise EvidenceError('REMAINING_PARENT_PATH_SUPERSEDED')
    return (*current_path_heads(store,parent),('OFFICIAL_OBSERVATION',row['event_id'],official_head['seq']))


def validate_condition_binding(store, model_ids, observation, coverage):
    """A new coverage record cannot relabel a previously derived remaining path."""
    for key in model_ids:
        p = store.get(key)['body']['payload']
        if p.get('version') not in {VERSION,'alpha_v11_physical_model_input_v1'}: continue
        c = p.get('remaining_context', {})
        expected = dict(observation_id=observation['id'],observation_sha256=observation['sha256'],
            accepted_intervals=coverage['accepted_intervals'],unresolved_intervals=coverage['unresolved_intervals'])
        if c != expected:
            raise EvidenceError('REMAINING_PATH_CONDITION_MISMATCH')


def archive_remaining_path(store, record_id, *, plan, path_id, observation_id, deadline=None):
    """Finite, replayable preparation; no HTTP, review grant, pointer or order.

    Returns MODEL and FEATURES IDs consumed by existing source selectors and
    same-day admission. Completed replay is historical; consumers revalidate.
    A partial append resumes only while its exact source versions remain current.
    """
    from .gefs_sources import (GEFSPlan, VERSION as PATH_VERSION, FIELD_VERSION,
        PROVIDER as PATH_PROVIDER, MAX_FIELDS, linear_extreme)
    identity(record_id,maximum=100); identity(path_id); identity(observation_id)
    if not isinstance(plan,GEFSPlan): raise EvidenceError('REMAINING_TYPED_PLAN_REQUIRED')
    deadline = min(time.monotonic()+2.,deadline) if deadline is not None else time.monotonic()+2.
    request_sha = digest(dict(version=VERSION,plan=asdict(plan),path_id=path_id,observation_id=observation_id))
    model = None
    try: model = store.get(record_id)
    except EvidenceError as exc:
        if str(exc) != 'EVIDENCE_MISSING': raise
    if model is not None:
        if model['kind'] != 'MODEL' or model['body']['payload'].get('derivation_sha256') != request_sha:
            raise EvidenceError('REMAINING_PREPARATION_REPLAY_CONFLICT')
        try: coverage = store.get(record_id+':coverage')
        except EvidenceError as exc:
            if str(exc) != 'EVIDENCE_MISSING': raise
        else:
            if (coverage['kind'] != 'FEATURES' or coverage['body']['payload'].get('model_evidence_sha256') != [model['sha256']]
                    or coverage['body']['payload'].get('observation_id') != observation_id):
                raise EvidenceError('REMAINING_PREPARATION_REPLAY_CONFLICT')
            return dict(model=model,coverage=coverage)
    now = finite(store.clock()); observation = store.get(observation_id)
    accepted, unresolved = _observation(observation,plan,now)
    official_head = store.latest(kind='OFFICIAL_OBSERVATION',event_id=plan.event_id)
    if official_head is None or official_head['id'] != observation_id:
        raise EvidenceError('REMAINING_CURRENT_OFFICIAL_REQUIRED')
    if model is None:
        parent = store.get(path_id); b = parent['body']; p = b.get('payload',{})
        refs = p.get('field_references')
        if (parent['kind'] != 'MODEL' or parent['event_id'] != plan.event_id or b['provider'] != PATH_PROVIDER
                or b['source_identity'] != plan.source_identity or p.get('version') != PATH_VERSION
                or type(refs) is not list or not 1 <= len(refs) <= MAX_FIELDS
                or any(type(ref) is not dict or set(ref) != {'id','sha256','source_identity'} for ref in refs)
                or len({ref['id'] for ref in refs}) != len(refs)
                or p.get('path_sha256') != digest(dict(plan=asdict(plan),field_ids=tuple(ref['id'] for ref in refs),version=PATH_VERSION))
                or p.get('rule_fingerprint') != plan.rule.sha256
                or b['issued_at'] != plan.initialized_at or b['available_at'] > now
                or b['evidence_class'] not in {'SYNTHETIC','PUBLIC_OBSERVED'}
                or not 0 <= now-plan.initialized_at < plan.maximum_run_age_seconds):
            raise EvidenceError('REMAINING_BOUND_FULL_PATH_REQUIRED')
        view = store.source_batch(kind='MODEL',event_id=plan.event_id,provider=PATH_PROVIDER,
            record_ids=tuple(ref['id'] for ref in refs),source_identities=(plan.source_identity,*(ref['source_identity'] for ref in refs)),deadline=deadline)
        if view['channels'].get(plan.source_identity,{}).get('id') != path_id:
            raise EvidenceError('REMAINING_PARENT_PATH_SUPERSEDED')
        fields = {}; receipts = []
        for ref in refs:
            row = view['records'][ref['id']]; data = row['body']; field = data['payload'].get('field',{})
            m,h = field.get('member'),field.get('forecast_hour')
            if (view['channels'].get(ref['source_identity'],{}).get('id') != row['id']
                    or row['sha256'] != ref['sha256'] or row['seq'] >= parent['seq']
                    or data['payload'].get('version') != FIELD_VERSION or (m,h) in fields
                    or type(m) is not int or not 0 <= m <= 30 or type(h) is not int or h not in plan.hours
                    or data['source_identity'] != plan.field_identity(m,h) or data['issued_at'] != plan.initialized_at
                    or data['available_at'] > b['available_at']
                    or data['evidence_class'] not in {'PUBLIC_OBSERVED','SYNTHETIC'}
                    or not 0 <= now-data['received_at'] < plan.forecast.maximum_receipt_age_seconds):
                raise EvidenceError('REMAINING_CURRENT_COMPLETE_FIELD_PATH_REQUIRED')
            fields[m,h] = finite(data['payload']['value_kelvin']); receipts.append(data['received_at'])
        if set(fields) != {(m,h) for m in range(31) for h in plan.hours}:
            raise EvidenceError('REMAINING_ALL_MEMBER_BRACKETS_REQUIRED')
        points = [plan.initialized_at+h*3600 for h in plan.hours]; members = []
        for m in range(31):
            if time.monotonic() >= deadline: raise EvidenceError('REMAINING_PREPARATION_TIME_BOUND')
            k = linear_extreme(points,[fields[m,h] for h in plan.hours],unresolved,
                              high=plan.rule.payload['family']==DAILY_HIGH)
            c = k-273.15; members.append(c*1.8+32 if plan.rule.payload['unit']=='F' else c)
        context = dict(observation_id=observation_id,observation_sha256=observation['sha256'],
            accepted_intervals=[list(pair) for pair in accepted],unresolved_intervals=[list(pair) for pair in unresolved])
        payload = dict(version=VERSION,derivation_sha256=request_sha,rule_fingerprint=plan.rule.sha256,
            **{k:plan.rule.payload[k] for k in ('station','target_date','family','unit')},
            temperature_input=dict(version=INPUT_VERSION,model_id=MODEL_ID,
                target_sha256=target_identity(plan.rule,UNRESOLVED_EXTREME),members=members),
            remaining_context=context,dependencies=[dict(id=r['id'],sha256=r['sha256']) for r in (parent,observation)],
            approximation='PIECEWISE_LINEAR_POINT_TEMPERATURE_PATH',elapsed_gaps_included=True,
            unobserved_intrastep_extrema_known=False,interpolation_uncertainty_calibrated=False,
            exact_population_independently_attested=False,settlement_authority=False,financial_authority=False)
        ready = finite(store.clock())
        model = store._append(record_id,'MODEL',plan.event_id,dict(provider=PROVIDER,
            source_identity=plan.source_identity+':REMAINING',revision=request_sha,payload=payload,
            observed_at=None,issued_at=b['issued_at'],published_at=None,received_at=min(receipts),
            evidence_class='SYNTHETIC' if any(r['body']['evidence_class']=='SYNTHETIC' for r in (parent,observation)) else 'PUBLIC_OBSERVED',
            source_kind='MODEL'),ready,ready,expected_heads=(('MODEL',plan.event_id,view['head']['seq']),
            ('OFFICIAL_OBSERVATION',plan.event_id,official_head['seq'])))
    heads = current_remaining_heads(store,model)
    if time.monotonic() >= deadline: raise EvidenceError('REMAINING_PREPARATION_TIME_BOUND')
    c = model['body']['payload']['remaining_context']
    at = model['body']['available_at']
    if not plan.window[0] <= at < plan.window[1]: raise EvidenceError('REMAINING_PREPARATION_CROSSED_DAY_END')
    payload = dict(version=COVERAGE_VERSION,rule_fingerprint=plan.rule.sha256,as_of=at,
        accepted_intervals=c['accepted_intervals'],unresolved_intervals=c['unresolved_intervals'],
        model_evidence_sha256=[model['sha256']],observation_id=observation_id,observation_sha256=observation['sha256'])
    ready = finite(store.clock())
    coverage = store._append(record_id+':coverage','FEATURES',plan.event_id,dict(provider=PROVIDER,
        source_identity=plan.source_identity+':COVERAGE',revision=request_sha,payload=payload,
        observed_at=at,issued_at=None,published_at=None,received_at=ready,evidence_class=model['body']['evidence_class'],
        source_kind='FEATURES'),ready,ready,expected_heads=heads)
    return dict(model=model,coverage=coverage)
