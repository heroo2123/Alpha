"""Read-only qualification of one causal, mutually exclusive forward outcome.

This module does not infer an admission from a forecast or grant financial,
settlement, calibration, or promotion authority.
"""
from __future__ import annotations

import math

from ..settlement import exact_token_payout
from .evidence import EvidenceError, digest, finite
from .learning_capture import TARGET, VERSION as CAPTURE_VERSION
from .probability import _partition
from .rules import RuleFingerprint

VERSION = 'alpha_v11_grouped_forward_qualification_v1'


def _require(ok, reason):
    if not ok:
        raise EvidenceError('FORWARD_' + reason)


def _get(store, key, kind, event):
    row = store.get(key)
    _require(row['kind'] == kind and row['event_id'] == event
             and row['body'].get('financial_authority') is False, 'LINEAGE_MISMATCH')
    return row


def grouped_outcome(store, capture_id, label_ids):
    """Verify explicit local receipts; return pinned identities, never an authority.

    Every LABEL in the event is scanned so an unselected duplicate or conflicting
    recapture cannot silently become a unique grouped outcome.
    """
    capture = store.get(capture_id)
    d = capture['body'].get('details', {})
    event = capture['event_id']
    _require(capture['kind'] == 'MEASUREMENT' and d.get('version') == CAPTURE_VERSION
             and d.get('complete_event_vector') is True and d.get('financial_authority') is False
             and d.get('target') == TARGET and d.get('selection_scope') == 'ALL_BUCKETS_OF_THIS_EVALUATED_EVENT',
             'COMPLETE_CAPTURE_REQUIRED')
    rule = RuleFingerprint(**d['rule'])
    p = rule.payload
    binding = d['binding']
    context = d['context']
    _require(p['event_id'] == event == context['event_id'] and p['station'] == context['station_id']
             and binding['rule_fingerprint'] == rule.sha256
             and d['inference_cutoff'] <= capture['body']['recorded_at'], 'EVENT_RULE_BINDING')
    buckets = _partition(rule)
    expected = {b['market_id']: dict(market_id=b['market_id'], condition_id=b['condition_id'],
                                     token_id=b['yes_token'], side='YES') for b in buckets}
    rows = d['rows']
    source_refs = capture['body'].get('evidence', [])
    _require(type(source_refs) is list and 1 <= len(source_refs) <= 16
             and len({r['id'] for r in source_refs}) == len(source_refs), 'MODEL_SET_REQUIRED')
    _require(type(rows) is list and len(rows) == len(expected)
             and len({r['target_identity']['market_id'] for r in rows}) == len(rows)
             and {r['target_identity']['market_id']: r['target_identity'] for r in rows} == expected,
             'INCOMPLETE_PARTITION')
    _require(type(label_ids) is dict and set(label_ids) == set(expected)
             and len(set(label_ids.values())) == len(label_ids), 'LABEL_SET_INCOMPLETE_OR_DUPLICATE')
    all_labels = []
    cursor = 0
    while True:
        page = store.records(kind='LABEL', event_id=event, after_seq=cursor, limit=200)
        all_labels.extend(page)
        _require(len(all_labels) <= 1000, 'LABEL_SCAN_BOUND')
        if not page or len(page) < 200:
            break
        cursor = page[-1]['seq']
    relevant = [r for r in all_labels if r['body'].get('payload', {}).get('target_identity', {}).get('market_id') in expected]
    _require(len(relevant) == len(expected)
             and {r['id'] for r in relevant} == set(label_ids.values()), 'MISSING_DUPLICATE_OR_CONFLICTING_LABEL')
    cutoff = finite(d['inference_cutoff'])
    decision_ids, feature_ids, model_ids, label_refs, values, probabilities = set(), set(), set(), [], [], []
    for child in rows:
        target = child['target_identity']
        mid = target['market_id']
        decision = _get(store, child['decision_id'], 'DECISION', event)
        feature = _get(store, child['feature_id'], 'FEATURES', event)
        label = _get(store, label_ids[mid], 'LABEL', event)
        db, fb, lb = decision['body'], feature['body'], label['body']
        ex, lp = db['explanation'], lb['payload']
        _require(decision['sha256'] == child['decision_sha256'] and feature['sha256'] == child['feature_sha256']
                 and decision['id'] not in decision_ids and feature['id'] not in feature_ids
                 and db['binding'] == binding and db['strategy'] == 'FUTURE_FORECAST'
                 and db.get('evidence_class') == fb.get('evidence_class') == 'PUBLIC_OBSERVED'
                 and db['target'] == TARGET and db['valuation_type'] == 'SETTLEMENT'
                 and db['execution_status'] == 'NONFINANCIAL_NOT_SUBMITTED'
                 and db['evidence'] == [dict(id=feature['id'], sha256=feature['sha256'])]
                 and fb.get('payload', {}).get('dependencies') == source_refs
                 and ex['target_identity'] == target and ex['prediction_sha256'] == d['prediction_sha256']
                 and ex['request_sha256'] == d['request_sha256'] and ex['inference_cutoff'] == cutoff
                 and ex.get('financial_authority') is False
                 and feature['seq'] < decision['seq'] < capture['seq'] < label['seq']
                 # archive_features samples its ready clock before capture() stamps
                 # the durable receipt. The decision pins that receipt exactly.
                 and 0 <= fb['available_at'] - finite(fb['payload'].get('feature_ready_at')) <= 1.
                 and fb['available_at'] == db['feature_ready_at']
                 and db['feature_ready_at'] <= db['recorded_at']
                 and cutoff <= db['recorded_at'] < lb['recorded_at']
                 and db['recorded_at'] < finite(db['expires_at']), 'DECISION_LINEAGE_OR_LOOKAHEAD')
        decision_ids.add(decision['id']); feature_ids.add(feature['id'])
        for dep in fb.get('payload', {}).get('dependencies', []):
            source = _get(store, dep['id'], 'MODEL', event)
            _require(source['sha256'] == dep['sha256'] and source['seq'] < feature['seq']
                     and source['body']['available_at'] <= cutoff
                     and source['body'].get('evidence_class') == 'PUBLIC_OBSERVED',
                     'MODEL_LINEAGE_OR_LOOKAHEAD')
            model_ids.add(source['id'])
        _require(fb.get('payload', {}).get('source_versions', {}).get('prediction') == d['prediction_sha256']
                 and fb['payload']['source_versions']['rule'] == rule.sha256,
                 'FEATURE_PREDICTION_DRIFT')
        raw = _get(store, lp['source_capture_id'], 'RULES', event)
        rb = raw['body']; rp = rb.get('payload', {})
        market = rp.get('response')
        _require(raw['sha256'] == lp['source_capture_sha256']
                 and rb.get('provider') == 'GAMMA_CLOSED_MARKET'
                 and rb.get('source_identity') == 'market:' + mid
                 and rb.get('revision') == 'gamma-closed:' + digest(market)
                 and rb.get('evidence_class') == lb.get('evidence_class') == 'PUBLIC_OBSERVED'
                 and rp.get('capture_role') == 'EXACT_TOKEN_PAYOUT_LABEL_SOURCE'
                 and rp.get('http_status') == 200
                 and rp.get('endpoint') == 'https://gamma-api.polymarket.com/markets/' + mid
                 and isinstance(market, dict) and str(market.get('id')) == mid
                 and market.get('conditionId') == target['condition_id']
                 and raw['seq'] < label['seq'] and cutoff < rb['recorded_at'] <= lb['recorded_at']
                 and cutoff < rb['available_at'] <= finite(lp['knowable_at']) <= lb['available_at']
                 and lp['target_identity'] == target and lp['decision_target'] == TARGET
                 and lp['context'] == dict(station=p['station'], city=context['city_id'],
                                          local_date=p['target_date'], target=TARGET,
                                          rule_fingerprint=rule.sha256)
                 and lp['evidence_type'] == 'EXACT_SOURCE_LABEL'
                 and lp['financial_authority'] is False
                 and lb['provider'] == 'GAMMA_CLOSED_MARKET_EXACT_TOKEN_PAYOUT'
                 and lb['source_identity'] == target['token_id']
                 and lb['revision'] == lp['label_version']
                 == 'gamma-payout:' + digest([raw['sha256'], target['token_id'], float(lp['value'])])
                 and type(lp['value']) is int and lp['value'] in (0, 1)
                 and exact_token_payout(target['token_id'], market) == lp['value'],
                 'LABEL_PROVENANCE_OR_CHRONOLOGY')
        label_refs.append(dict(market_id=mid, id=label['id'], sha256=label['sha256'],
                               source_id=raw['id'], source_sha256=raw['sha256'],
                               knowable_at=lp['knowable_at'], value=lp['value']))
        values.append(lp['value'])
        probabilities.append(finite(ex['point']))
    _require(sum(values) == 1 and math.isclose(math.fsum(probabilities), 1., abs_tol=1e-10)
             and all(0 <= x <= 1 for x in probabilities), 'GROUPED_OUTCOME_INVALID')
    return dict(version=VERSION, event_id=event, capture_id=capture['id'], capture_sha256=capture['sha256'],
                binding=binding, rule_fingerprint=rule.sha256, prediction_sha256=d['prediction_sha256'],
                inference_cutoff=cutoff, admission_ref=d.get('admission_ref'),
                model_source_ids=sorted(model_ids),
                market_ids=sorted(expected), labels=sorted(label_refs, key=lambda x: x['market_id']),
                financial_authority=False, independent_label_attestation=False)
