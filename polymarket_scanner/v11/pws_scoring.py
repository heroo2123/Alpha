"""Pinned first-receipt label scoring and read-only numerical comparison.

The later label cutoff never replaces the original prediction cutoff. A receipt
score is not proof of publication continuity, calibration, payout or P&L.
"""
import math

from .evidence import EvidenceError, canonical, digest, finite
from .probability import NEXT_OBSERVATION, _partition
from .pws_lead import VERSION as LEAD_VERSION, _report
from .rules import RuleFingerprint

VERSION = 'alpha_v11_pws_receipt_score_v1'


def ref(row):
    return dict(id=row['id'], sha256=row['sha256'], seq=row['seq'])


def first_received_report_details(source, start):
    """Shared selection/scoring at the durable start's immutable receipt prefix."""
    source.check(); sd = start['body'].get('details', {})
    request = sd['request']; observation = source.get(request['observation_ref']['id'])
    d = observation['body'].get('details', {})
    cutoff = finite(start['body']['recorded_at']); boundary = start['seq']-1
    if (start['kind'] != 'MEASUREMENT' or sd.get('version') != VERSION or sd.get('stage') != 'START'
            or sd.get('request_sha256') != digest(request) or set(request) != {'observation_ref'}
            or request['observation_ref'] != ref(observation)
            or observation['kind'] != 'MEASUREMENT' or d.get('version') != LEAD_VERSION
            or d.get('target') != NEXT_OBSERVATION or 'observation_window' not in d
            or start['event_id'] != observation['event_id']
            or not observation['seq'] <= boundary < source.snapshot_seq
            or observation['body']['recorded_at'] > cutoff
            or start['body']['evidence'] != [dict(id=observation['id'], sha256=observation['sha256'])]):
        raise EvidenceError('LEAD_SCORE_ORIGINAL_START_BINDING')
    rule = RuleFingerprint(**d['request']['rule']); window = d['observation_window']
    rows = source.records(kind='OFFICIAL_OBSERVATION', event_id=observation['event_id'],
        after_seq=observation['seq'], through_seq=boundary, limit=1000)
    if len(rows) >= 1000:
        raise EvidenceError('LEAD_LABEL_SCAN_BOUND')
    chosen = None; first_update = None
    for row in rows:
        source.check()
        if row['body']['recorded_at'] > cutoff or row['body']['available_at'] > cutoff:
            continue
        if first_update is None:
            first_update = row
        try:
            value = _report(row, rule)
        except EvidenceError:
            continue
        if row['body']['observed_at'] <= d['official_anchor_observed_at']:
            continue
        if not observation['body']['recorded_at'] <= row['body']['available_at'] <= window['window_end']:
            break
        chosen = (row, value); break
    result = dict(version=LEAD_VERSION, observation_id=observation['id'], target=NEXT_OBSERVATION,
        status='UNKNOWN', reason='NO_RECEIVED_ELIGIBLE_REPORT_IN_WINDOW',
        first_official_update=first_update['id'] if first_update else None,
        target_is_true_next_published_report=False, collection_continuity_verified=False,
        comparison_partition='DEVELOPMENT', calibration_status='SCORED_NOT_CALIBRATED',
        lead_advantage_verified=False, settlement_label=None, executable_markout=None, trading_pnl=None,
        financial_authority=False, score_protocol=VERSION, start_ref=ref(start), observation_ref=ref(observation),
        scoring_as_of=cutoff, scan_through_seq=boundary, scanned_report_count=len(rows),
        scanned_report_refs_sha256=digest([ref(row) for row in rows]))
    refs = [observation['id'], start['id']]
    if first_update:
        refs.append(first_update['id'])
    if chosen:
        row, value = chosen
        partition = _partition(rule)
        markets = {b['market_id'] for b in partition}
        matches = [b['market_id'] for b in partition if
            (b['lower'] is None or value >= b['lower']) and (b['upper'] is None or value <= b['upper'])]
        if len(matches) != 1:
            raise EvidenceError('LEAD_REPORTED_VALUE_NOT_IN_EXACT_PARTITION')
        scores = []
        for prediction in (d['with_pws'], d['without_pws']):
            vector = prediction['buckets']
            if (type(vector) is not list or len(vector) != len(partition) or len(markets) != len(partition)
                    or {b['market_id'] for b in vector} != markets
                    or prediction['target'] != NEXT_OBSERVATION or prediction['event_id'] != observation['event_id']
                    or prediction['rule_fingerprint'] != rule.sha256 or prediction['as_of'] != d['feature_ready_at']
                    or any(not 0 <= finite(b['point']) <= 1 for b in vector)
                    or not math.isclose(math.fsum(b['point'] for b in vector), 1., rel_tol=0., abs_tol=1e-12)):
                raise EvidenceError('LEAD_SCORE_ORIGINAL_DISTRIBUTION_REQUIRED')
            win = next(b['point'] for b in vector if b['market_id'] == matches[0])
            scores.append(dict(brier=math.fsum((b['point']-(b['market_id'] == matches[0]))**2 for b in vector),
                log_loss=-math.log(win) if win > 0 else None, log_loss_infinite=win == 0))
        result.update(status='MEASURED_FIRST_RECEIVED_REPORT', reason='ARCHIVED_RECEIPT_COMPARISON_NOT_CERTIFIED_NEXT_LABEL',
            official_id=row['id'], official_sha256=row['sha256'], reported_whole_degree=value,
            observed_at=row['body']['observed_at'], received_at=row['body']['received_at'],
            provider_published_at=row['body']['published_at'],
            receipt_lead_seconds=row['body']['received_at']-observation['body']['recorded_at'],
            with_pws=scores[0], without_pws=scores[1], brier_improvement=scores[1]['brier']-scores[0]['brier'],
            log_loss_improvement=scores[1]['log_loss']-scores[0]['log_loss'] if all(s['log_loss'] is not None for s in scores) else None,
            label_evidence_class=row['body']['evidence_class'])
        refs.append(row['id'])
    source.check()
    return result, tuple(dict.fromkeys(refs))


def replay_first_received_report(source, score_id):
    """Borrow one bounded snapshot; legacy/missing pins cannot earn replay credit."""
    result = dict(version=VERSION, score_id=score_id, status='GATED', score_match=False,
        comparisons={}, financial_authority=False, source_truth_independently_attested=False,
        original_prediction_recomputed=False, target_is_true_next_published_report=False,
        collection_continuity_verified=False, calibration_authority=False, new_economic_commands=0)
    try:
        source.check(); score = source.get(score_id); d = score['body'].get('details', {})
        if score['kind'] != 'MEASUREMENT' or d.get('version') != LEAD_VERSION or d.get('score_protocol') != VERSION:
            raise EvidenceError('LEAD_SCORE_PIN_REQUIRED_LEGACY_UNSUPPORTED')
        start = source.get(score_id+':start')
        if (d.get('start_ref') != ref(start) or start['event_id'] != score['event_id']
                or not start['seq'] < score['seq'] <= source.snapshot_seq
                or start['body']['recorded_at'] > score['body']['recorded_at']):
            raise EvidenceError('LEAD_SCORE_ORIGINAL_START_BINDING')
        measured, refs = first_received_report_details(source, start)
        expected = [dict(id=key, sha256=source.get(key)['sha256']) for key in refs]
        comparisons = dict(score=canonical(d) == canonical(measured),
            evidence=canonical(score['body']['evidence']) == canonical(expected))
        source.check()
        result.update(status='SCORE_REPRODUCED' if all(comparisons.values()) else 'MISMATCH',
            reason='SHARED_FIRST_RECEIPT_SCORING_ORIGINAL_BOUNDARIES', score_match=all(comparisons.values()),
            comparisons=comparisons, score_ref=ref(score), start_ref=ref(start),
            observation_ref=measured['observation_ref'], scoring_as_of=measured['scoring_as_of'],
            scan_through_seq=measured['scan_through_seq'], scanned_report_count=measured['scanned_report_count'],
            scanned_report_refs_sha256=measured['scanned_report_refs_sha256'],
            original_score_sha256=digest(d), recomputed_score_sha256=digest(measured))
        if len(canonical(result).encode()) > 16*1024:
            raise EvidenceError('LEAD_SCORE_REPLAY_OUTPUT_BOUND')
        source.check()
    except (EvidenceError, KeyError, TypeError, ValueError, OverflowError, StopIteration, OSError) as exc:
        for key in tuple(result):
            if key not in {'version','score_id','financial_authority','source_truth_independently_attested',
                    'original_prediction_recomputed','target_is_true_next_published_report',
                    'collection_continuity_verified','calibration_authority','new_economic_commands'}:
                result.pop(key)
        result.update(status='GATED', score_match=False, comparisons={},
            reason=str(exc) if isinstance(exc, EvidenceError) else 'LEAD_SCORE_REPLAY_MALFORMED_OR_UNAVAILABLE')
    return result
