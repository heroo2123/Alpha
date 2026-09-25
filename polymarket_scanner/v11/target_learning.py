"""Exact conditioned-payout and paired observation capture, without fitting.

The archived numerical inputs and conditioning reproduce the original frozen
prediction. Capture never creates a label, economic proposal or model authority.
Observation labels must explicitly distinguish first receipt from publication.
"""
from dataclasses import asdict, replace
from types import SimpleNamespace
import time

from .datasets import CausalExample, archive_features, build_example
from .event_risk import EventContext
from .evidence import EvidenceError, EvidenceStore, ReleaseBinding, canonical, digest, finite, identity
from .forecast_features import ForecastFeatureContract
from .learning_capture import _get, capture_admission_ref
from .model_artifacts import PinnedBundle, predict_with_bundle
from .physical_inference import FAMILY, PhysicalFeatureContract
from .probability import BucketPrediction, FINAL_EXTREME, NEXT_OBSERVATION, UNRESOLVED_EXTREME, _partition
from .rules import RuleFingerprint
from .strategy_pipeline import _condition, _model_inputs


VERSION = 'alpha_v11_target_learning_capture_v1'
CONTEXT_VERSION = 'alpha_v11_learning_conditioning_v1'
PAYOUT = 'FINAL_CONTRACT_PAYOUT'


def _inputs(store, rule, prediction, pinned, ids, *, target, observation_id=None, coverage_id=None):
    """Reproduce values using pinned sources only; no current-head substitution."""
    p = prediction.payload; cutoff = finite(p['as_of'])
    components = _model_inputs(store, rule, ids, cutoff, target=target)
    widths = tuple((c.model_id, len(c.members)) for c in components)
    base = ForecastFeatureContract(widths, rule.payload['unit'], rule.payload['family'])
    physical = pinned.payload['components']['PROBABILITY']['parameters']['family'] == FAMILY
    if physical:
        contract = PhysicalFeatureContract(widths, base.unit, base.family, target)
    elif target == UNRESOLVED_EXTREME:
        contract = base
    else:
        raise EvidenceError('OBSERVATION_FEATURE_CONTRACT_UNSUPPORTED')
    parent = contract.require_bundle(pinned)
    observed = coverage = None
    if target == UNRESOLVED_EXTREME:
        request = SimpleNamespace(model_input_ids=ids, observed_input_id=observation_id, coverage_input_id=coverage_id)
        observed, coverage = _condition(store, rule, request, cutoff, components, available_cutoff=store.clock())
    # This age is a replay parameter only. Live freshness/eligibility is still
    # checked by admission; the numerical output must match the saved prediction.
    replay_age = min(86400., max(1., *(cutoff - at + 1. for c in components for at in (c.received_at, c.issued_at))))
    rebuilt = predict_with_bundle(pinned, rule, components, as_of=cutoff, max_source_age_seconds=replay_age,
                                  observed=observed, remaining_coverage=coverage)
    if rebuilt != prediction:
        raise EvidenceError('TARGET_LEARNING_PREDICTION_REPLAY_MISMATCH')
    values = {name: value for c in components for name, value in zip(base.mapping[c.model_id], c.members)}
    if physical:
        auxiliaries = [dict(c.auxiliary.values) for c in components]
        if any(a != auxiliaries[0] for a in auxiliaries[1:]):
            raise EvidenceError('TARGET_LEARNING_SHARED_PHYSICAL_FEATURES_REQUIRED')
        values.update(auxiliaries[0])
    conditioning = dict(version=CONTEXT_VERSION, input_target=target, inference_cutoff=cutoff,
        observed_constraint=p['observed_constraint'], remaining_coverage=p['remaining_coverage'],
        observation_id=observation_id, coverage_id=coverage_id)
    return contract.schema, base.mapping, values, conditioning, parent


def _capture(store, record_id, *, context, strategy, rule, binding, prediction, pinned_bundle,
             model_input_ids, expires_at, observed_input_id=None, coverage_input_id=None,
             observation_target=None, pair_id=None, ablation=None, admission_id=None):
    identity(record_id, maximum=110); identity(strategy)
    if (not isinstance(context, EventContext) or not isinstance(rule, RuleFingerprint)
            or not isinstance(binding, ReleaseBinding) or not isinstance(prediction, BucketPrediction)
            or not isinstance(pinned_bundle, PinnedBundle) or type(model_input_ids) is not tuple
            or not 1 <= len(model_input_ids) <= 16 or len(set(model_input_ids)) != len(model_input_ids)):
        raise EvidenceError('TARGET_LEARNING_TYPED_SCOPE_REQUIRED')
    request = dict(version=VERSION, context=asdict(context), strategy=strategy, rule=asdict(rule), binding=asdict(binding),
        prediction_sha256=prediction.sha256, bundle_sha256=pinned_bundle.sha256, model_input_ids=model_input_ids,
        expires_at=expires_at, observed_input_id=observed_input_id, coverage_input_id=coverage_input_id,
        observation_target=observation_target, pair_id=pair_id, ablation=ablation)
    if admission_id is not None: request['admission_id']=identity(admission_id)
    request_sha = digest(request); old = _get(store, record_id)
    if old is not None:
        if old['kind'] != 'MEASUREMENT' or old['body'].get('details', {}).get('request_sha256') != request_sha:
            raise EvidenceError('TARGET_LEARNING_REPLAY_CONFLICT')
        return old
    p = prediction.payload; rp = rule.payload; now = finite(store.clock()); expiry = finite(expires_at)
    target = NEXT_OBSERVATION if observation_target is not None else PAYOUT
    input_target = NEXT_OBSERVATION if target == NEXT_OBSERVATION else UNRESOLVED_EXTREME
    if (p['target'] != (NEXT_OBSERVATION if target == NEXT_OBSERVATION else FINAL_EXTREME)
            or p['rule_fingerprint'] != rule.sha256 or binding.rule_fingerprint != rule.sha256
            or p['bundle_sha256'] != binding.bundle_sha256 or pinned_bundle.sha256 != binding.bundle_sha256
            or (context.event_id, context.station_id) != (rp['event_id'], rp['station'])
            or not finite(p['as_of']) <= now < expiry):
        raise EvidenceError('TARGET_LEARNING_SCOPE_OR_TIME_MISMATCH')
    if (target == PAYOUT and (p['observed_constraint'] is None or p['remaining_coverage'] is None)
            or target == NEXT_OBSERVATION and (observed_input_id is not None or coverage_input_id is not None)):
        raise EvidenceError('TARGET_LEARNING_CONDITIONING_REQUIRED')
    admission=capture_admission_ref(store,admission_id,context=context,rule=rule,binding=binding,
        strategy=strategy,cutoff=p['as_of'])
    deadline = time.monotonic() + 2.
    schema, mapping, values, conditioning, parent = _inputs(store, rule, prediction, pinned_bundle, model_input_ids,
        target=input_target, observation_id=observed_input_id, coverage_id=coverage_input_id)
    conditioning.update(target=target, pair_id=pair_id, ablation=ablation, observation_target=observation_target)
    inputs = tuple(dict.fromkeys((*model_input_ids, *(x for x in (observed_input_id, coverage_input_id) if x is not None))))
    sources = [store.get(key) for key in inputs]
    refs = [dict(id=s['id'], sha256=s['sha256']) for s in sources]
    versions = dict(prediction=prediction.sha256, rule=rule.sha256, conditioning=digest(conditioning))
    rows = []; targets = [(observation_target, None)] if target == NEXT_OBSERVATION else [
        (dict(market_id=b['market_id'], condition_id=b['condition_id'], token_id=b['yes_token'], side='YES'), b)
        for b in _partition(rule)]
    for exact, bucket in targets:
        if time.monotonic() >= deadline:
            raise EvidenceError('TARGET_LEARNING_TIME_BOUND')
        if store.clock() >= expiry:
            raise EvidenceError('TARGET_LEARNING_EXPIRED')
        prefix = 'target-data:' + digest([record_id, exact]); feature_id = prefix+':features'; decision_id = prefix+':prediction'
        current = dict(values, lower_cut=None if bucket is None or bucket['lower'] is None else bucket['lower']-.5,
                       upper_cut=None if bucket is None or bucket['upper'] is None else bucket['upper']+.5)
        feature = _get(store, feature_id)
        if feature is None:
            feature = archive_features(store, feature_id, event_id=rp['event_id'], schema=schema, values=current,
                evidence_ids=inputs, source_versions=versions, context=conditioning, learning_only=True)
        elif any(feature['body']['payload'].get(k) != v for k, v in dict(values=current, dependencies=refs,
                feature_schema_sha256=schema.sha256, source_versions=versions, context=conditioning).items()):
            raise EvidenceError('TARGET_LEARNING_FEATURE_REPLAY_CONFLICT')
        explanation = dict(version=VERSION, request_sha256=request_sha, target_identity=exact,
            prediction_sha256=prediction.sha256, prediction=p, conditioning=conditioning,
            selection='ALL_SUPPORTED_PREDICTIONS', economic_qualification_evaluated=False, financial_authority=False)
        decision = _get(store, decision_id)
        evidence = [dict(id=feature_id, sha256=feature['sha256'])]
        if decision is None:
            decision = store.decision(decision_id, event_id=rp['event_id'], strategy=strategy, binding=binding,
                evidence_ids=(feature_id,), feature_ready_at=feature['body']['available_at'],
                valuation_type='OBSERVATION_ONLY' if target == NEXT_OBSERVATION else 'SETTLEMENT', target=target,
                outcome='GATED', reason='PREDICTION_CAPTURE_NO_EXECUTABLE_ECONOMICS', explanation=explanation, expires_at=expiry)
        elif (decision['body'].get('explanation') != explanation or decision['body'].get('binding') != asdict(binding)
                or decision['body'].get('evidence') != evidence):
            raise EvidenceError('TARGET_LEARNING_DECISION_REPLAY_CONFLICT')
        rows.append(dict(target_identity=exact, feature_id=feature_id, feature_sha256=feature['sha256'],
                         decision_id=decision_id, decision_sha256=decision['sha256']))
    return store.audit(record_id, event_id=rp['event_id'], kind='MEASUREMENT', evidence_ids=inputs, details=dict(
        version=VERSION, request_sha256=request_sha, context=asdict(context), rule=asdict(rule), binding=asdict(binding),
        target=target, conditioning=conditioning, prediction_sha256=prediction.sha256, rows=rows,
        feature_schema_sha256=schema.sha256, model_feature_mapping=mapping,
        parent_feature_artifact_sha256=parent['bundle']['artifacts']['FEATURES'], parent_feature_contract_verified=True,
        complete_event_vector=target == PAYOUT, complete_observation_distribution=target == NEXT_OBSERVATION,
        global_universe_coverage_verified=False, labels_created=False, financial_authority=False,
        training_or_promotion_started=False, fit_status='TARGET_SPECIFIC_LEARNER_REQUIRED',
        **(dict(admission_ref=admission) if admission is not None else {})))


def capture_conditioned_vector(store, record_id, **kwargs):
    return _capture(store, record_id, **kwargs)


def capture_observation_pair(store, record_id, *, lead_id, context, bundle, without_pws_bundle):
    """Retain both distributions with one exact receipt-clock observation target."""
    from .pws_lead import VERSION as LEAD_VERSION
    identity(record_id, maximum=100)
    lead = store.get(lead_id); d = lead['body'].get('details', {})
    if lead['kind'] != 'MEASUREMENT' or d.get('version') != LEAD_VERSION or not isinstance(context, EventContext):
        raise EvidenceError('TARGET_LEARNING_OBSERVATION_PAIR_REQUIRED')
    rule = RuleFingerprint(**d['request']['rule']); rp = rule.payload; window = d['observation_window']
    exact = {k:window[k] for k in ('station', 'population', 'window_start', 'window_end', 'clock')}
    anchor = store.get(d['request']['official_id'])
    exact.update(unit=rp['unit'], source_family=rp['source_family'], official_anchor_sha256=anchor['sha256'])
    request_sha = digest([VERSION, lead_id, lead['sha256'], asdict(context), bundle.sha256, without_pws_bundle.sha256])
    old = _get(store, record_id)
    if old is not None:
        if old['body'].get('details', {}).get('request_sha256') != request_sha:
            raise EvidenceError('TARGET_LEARNING_PAIR_REPLAY_CONFLICT')
        return old
    if (window['clock'] != 'FIRST_ALPHA_RECEIPT' or any(d[k]['as_of'] != window['window_start'] for k in ('with_pws', 'without_pws'))
            or any(pinned.sha256 != d['request'][key] for pinned, key in
                   ((bundle, 'bundle_sha256'), (without_pws_bundle, 'without_pws_bundle_sha256')))):
        raise EvidenceError('TARGET_LEARNING_OBSERVATION_WINDOW_OR_BUNDLE')
    children = []
    for name, pinned, key in (('with_pws', bundle, 'model_ids'), ('without_pws', without_pws_bundle, 'without_pws_model_ids')):
        prediction = BucketPrediction(canonical(d[name]), digest(d[name]))
        binding = replace(ReleaseBinding(**d['request']['binding']), bundle_sha256=pinned.sha256)
        child = _capture(store, 'observation-data:'+digest([record_id, name]), context=context, strategy='PWS_OBSERVATION_LEAD',
            rule=rule, binding=binding, prediction=prediction, pinned_bundle=pinned, model_input_ids=tuple(d['request'][key]),
            expires_at=window['window_end'], observation_target=exact, pair_id=lead_id, ablation=name)
        children.append(dict(ablation=name, id=child['id'], sha256=child['sha256']))
    return store.audit(record_id, event_id=lead['event_id'], kind='MEASUREMENT', evidence_ids=(lead_id, *(c['id'] for c in children)),
        details=dict(version=VERSION, request_sha256=request_sha, lead_id=lead_id, children=children, target=NEXT_OBSERVATION,
            target_identity=exact, pair_complete=True, paired_target_count=1, independent_sample_count=None, labels_created=False,
            source_truth_independently_attested=False, financial_authority=False, training_or_promotion_started=False))


def labeled_target_examples(store, capture_id, *, label_ids, city, horizon, season, prior_exposure='DEVELOPMENT'):
    """Join supplied labels to exact targets; pairing adds no independent event."""
    if isinstance(store, EvidenceStore):
        from .learning_sources import learning_source_view
        with learning_source_view(store) as source:
            return labeled_target_examples(source, capture_id, label_ids=label_ids, city=city,
                horizon=horizon, season=season, prior_exposure=prior_exposure)
    capture = store.get(capture_id); d = capture['body'].get('details', {})
    if capture['kind'] != 'MEASUREMENT' or d.get('version') != VERSION:
        raise EvidenceError('TARGET_LEARNING_CAPTURE_REQUIRED')
    if d.get('pair_complete'):
        if type(label_ids) is not str:
            raise EvidenceError('TARGET_LEARNING_ONE_OBSERVATION_LABEL_REQUIRED')
        results = []
        for child in d['children']:
            row = store.get(child['id'])
            if row['sha256'] != child['sha256']:
                raise EvidenceError('TARGET_LEARNING_CHILD_BINDING')
            results.extend(labeled_target_examples(store, row['id'], label_ids=label_ids, city=city, horizon=horizon,
                season=season, prior_exposure=prior_exposure))
        return tuple(results)
    if city != d['context']['city_id']:
        raise EvidenceError('TARGET_LEARNING_CITY_MISMATCH')
    target = d['target']; rows = d['rows']
    if target == PAYOUT:
        if not d.get('complete_event_vector') or type(label_ids) is not dict or set(label_ids) != {r['target_identity']['market_id'] for r in rows}:
            raise EvidenceError('TARGET_LEARNING_COMPLETE_PAYOUT_LABELS_REQUIRED')
    elif target != NEXT_OBSERVATION or not d.get('complete_observation_distribution') or type(label_ids) is not str or len(rows) != 1:
        raise EvidenceError('TARGET_LEARNING_ONE_OBSERVATION_LABEL_REQUIRED')
    p = RuleFingerprint(**d['rule']).payload; results = []
    for row in rows:
        decision = store.get(row['decision_id']); feature = store.get(row['feature_id'])
        if decision['sha256'] != row['decision_sha256'] or feature['sha256'] != row['feature_sha256']:
            raise EvidenceError('TARGET_LEARNING_CHILD_BINDING')
        label_id = label_ids[row['target_identity']['market_id']] if target == PAYOUT else label_ids
        receipt_provenance=None
        if target == NEXT_OBSERVATION:
            from .pws_lead import _report
            label=store.get(label_id);lp=label['body'].get('payload',{})
            score=store.get(lp.get('receipt_score_id'));sd=score['body'].get('details',{})
            if (score['kind']!='MEASUREMENT' or score['sha256']!=lp.get('receipt_score_sha256')
                    or sd.get('version')!='alpha_v11_pws_observation_lead_research_v1'
                    or sd.get('observation_id')!=d['conditioning']['pair_id']
                    or sd.get('status')!='MEASURED_FIRST_RECEIVED_REPORT'
                    or sd.get('target')!=NEXT_OBSERVATION or sd.get('reported_whole_degree')!=lp.get('value')
                    or sd.get('received_at')!=lp.get('source_received_at')
                    or score['event_id']!=capture['event_id']
                    or score['body']['recorded_at']>label['body']['available_at']):
                raise EvidenceError('TARGET_LEARNING_EXACT_RECEIPT_SCORE_REQUIRED')
            source=store.get(sd.get('official_id'));lead=store.get(d['conditioning']['pair_id'])
            rule=RuleFingerprint(**d['rule']);sb=source['body']
            if (source['sha256']!=sd.get('official_sha256') or _report(source,rule)!=lp['value']
                    or sb['received_at']!=lp['source_received_at'] or sb['available_at']>finite(lp.get('knowable_at'))
                    or sb['observed_at']<=lead['body']['details']['official_anchor_observed_at']
                    or not {source['id']:source['sha256'],lead['id']:lead['sha256']}.items()<=
                       {r['id']:r['sha256'] for r in score['body']['evidence']}.items()
                    or sb['evidence_class']=='SYNTHETIC' and label['body']['evidence_class']!='SYNTHETIC'):
                raise EvidenceError('TARGET_LEARNING_LABEL_SOURCE_BINDING')
            from .pws_scoring import replay_first_received_report
            proof = replay_first_received_report(store, score['id'])
            if not proof['score_match']:
                raise EvidenceError('TARGET_LEARNING_RECEIPT_SCORE_REPLAY_REQUIRED:'+proof['reason'])
            receipt_provenance=dict(score_replay_sha256=digest(proof),
                scoring_as_of=proof['scoring_as_of'], scan_through_seq=proof['scan_through_seq'],
                scanned_report_refs_sha256=proof['scanned_report_refs_sha256'],
                receipt_score_id=score['id'],receipt_score_sha256=score['sha256'],
                official_id=source['id'],official_sha256=source['sha256'],source_revision=sb['revision'],
                observed_at=sb['observed_at'],published_at=sb['published_at'],received_at=sb['received_at'],
                available_at=sb['available_at'],evidence_class=sb['evidence_class'],
                target_is_true_next_published_report=False,collection_continuity_verified=False)
        example=build_example(store, decision_id=decision['id'], feature_id=feature['id'], label_id=label_id,
            station=p['station'], city=city, local_date=p['target_date'], horizon=horizon, season=season, target=target,
            selection='ALL_SUPPORTED_PREDICTIONS', prior_exposure=prior_exposure)
        if receipt_provenance is not None:
            payload=dict(example.payload,label_receipt_provenance=receipt_provenance)
            example=CausalExample(canonical(payload),digest(payload))
        elif target==PAYOUT:
            payload=dict(example.payload,conditioning_rule=d['rule'])
            example=CausalExample(canonical(payload),digest(payload))
        results.append(example)
    if target == PAYOUT and sum(e.payload['label_value'] for e in results) != 1:
        raise EvidenceError('TARGET_LEARNING_EXCLUSIVE_PAYOUT_LABELS_REQUIRED')
    return tuple(results)
