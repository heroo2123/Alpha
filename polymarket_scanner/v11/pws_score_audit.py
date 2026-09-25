"""Complete retained receipt-score populations for bounded nonfinancial audits."""
from dataclasses import asdict
import time

from .causal_replay import ReplayPolicy
from .evidence import EvidenceError, canonical, digest, finite, sha
from .learning_sources import learning_source_view
from .pws_lead import VERSION as LEAD_VERSION
from .pws_scoring import ref, replay_first_received_report

VERSION = 'alpha_v11_pws_receipt_score_audit_v1'
MAX_SCORES = 32
MAX_BYTES = 256*1024


def _is_score(row):
    d = row['body'].get('details', {})
    return row['kind'] == 'MEASUREMENT' and type(d) is dict and (
        d.get('version') == LEAD_VERSION and 'observation_id' in d or 'score_protocol' in d)


def fold_pws_scores(row, aggregate, window):
    """Count UNKNOWN and legacy scores; leads and STARTs are not scored rows."""
    if not _is_score(row) or not window['start'] <= row['body']['recorded_at'] < window['end']:
        return
    cohort = aggregate.setdefault('pws_score_selection', dict(count=0, refs=[], overflow=False))
    cohort['count'] += 1
    if len(cohort['refs']) < MAX_SCORES: cohort['refs'].append(ref(row))
    else: cohort['overflow'] = True


def pws_score_audit(store, *, selection, through_seq, window, archive_complete, policy, monotonic=time.monotonic):
    """One source snapshot/deadline for the complete pinned score population."""
    if not isinstance(policy, ReplayPolicy) or store.namespace != 'V11_PAPER':
        raise EvidenceError('PWS_SCORE_AUDIT_PAPER_POLICY_REQUIRED')
    result = dict(version=VERSION, policy=asdict(policy), window=window, through_seq=through_seq,
        retained_score_count=selection.get('count'), status='GATED', reason=None, rows=[],
        complete_retained_selection=False, score_matches=0, all_scores_reproduced=False,
        original_status_counts={}, reproduced_measured_count=0,
        independent_sample_count=None, original_prediction_recomputed=False,
        collection_continuity_verified=False, target_is_true_next_published_report=False,
        source_truth_independently_attested=False, calibration_authority=False,
        financial_authority=False, new_economic_commands=0)
    try:
        if (type(through_seq) is not int or through_seq < 0
                or not finite(window['start']) < finite(window['end'])
                or type(selection['count']) is not int or selection['count'] < 0
                or archive_complete is not True or selection['overflow'] is not False
                or type(selection['refs']) is not list or len(selection['refs']) != selection['count']
                or len(selection['refs']) > MAX_SCORES):
            raise EvidenceError('PWS_SCORE_AUDIT_INCOMPLETE_OR_OVERFLOW')
        deadline = monotonic()+policy.maximum_seconds; seen = set()
        with learning_source_view(store, deadline=deadline, monotonic=monotonic) as source:
            if through_seq > source.snapshot_seq:
                raise EvidenceError('PWS_SCORE_AUDIT_REFERENCE_BOUND')
            for original in selection['refs']:
                source.check()
                if (type(original) is not dict or set(original) != {'id','sha256','seq'} or original['id'] in seen
                        or type(original['seq']) is not int or not 0 < original['seq'] <= through_seq):
                    raise EvidenceError('PWS_SCORE_AUDIT_REFERENCE_BOUND')
                seen.add(original['id']); sha(original['sha256'])
                row = source.get(original['id'])
                if (ref(row) != original or not _is_score(row)
                        or not window['start'] <= row['body']['recorded_at'] < window['end']):
                    raise EvidenceError('PWS_SCORE_AUDIT_ORIGINAL_BINDING')
                # Gated replay intentionally clears proof refs. Retain the independently
                # checked original here so legacy/unsupported rows stay in the cohort.
                proof = replay_first_received_report(source, row['id']); source.check()
                if proof['reason'] in {'LEARNING_SOURCE_VIEW_READ_BOUND','LEARNING_SOURCE_VIEW_TIME_BOUND'}:
                    raise EvidenceError(proof['reason'])
                original_status = row['body']['details'].get('status', 'UNRECOGNIZED')
                if type(original_status) is not str or len(original_status) > 128:
                    raise EvidenceError('PWS_SCORE_AUDIT_ORIGINAL_STATUS_BOUND')
                result['rows'].append(dict(score_ref=original, original_status=original_status,
                    status=proof['status'], reason=proof['reason'], score_match=proof['score_match'],
                    comparisons=proof['comparisons'], result_sha256=digest(proof),
                    start_ref=proof.get('start_ref'), observation_ref=proof.get('observation_ref'),
                    scoring_as_of=proof.get('scoring_as_of'), scan_through_seq=proof.get('scan_through_seq'),
                    scanned_report_count=proof.get('scanned_report_count'),
                    scanned_report_refs_sha256=proof.get('scanned_report_refs_sha256'),
                    original_score_sha256=proof.get('original_score_sha256'),
                    recomputed_score_sha256=proof.get('recomputed_score_sha256')))
                if len(canonical(result).encode()) > MAX_BYTES:
                    raise EvidenceError('PWS_SCORE_AUDIT_OUTPUT_BOUND')
            source.check()
            rows = result['rows']; count = sum(row['score_match'] for row in rows)
            states = {}
            for row in rows: states[row['original_status']] = states.get(row['original_status'], 0)+1
            result.update(status='NO_RETAINED_SCORES' if not rows else 'SCORES_REPRODUCED' if count == len(rows) else 'PARTIAL',
                reason='RETAINED_SCORE_MATH_ONLY_NOT_LABEL_COMPLETENESS', complete_retained_selection=True,
                score_matches=count, all_scores_reproduced=bool(rows) and count == len(rows), original_status_counts=states,
                reproduced_measured_count=sum(row['score_match'] and row['original_status'] == 'MEASURED_FIRST_RECEIVED_REPORT' for row in rows))
            if len(canonical(result).encode()) > MAX_BYTES:
                raise EvidenceError('PWS_SCORE_AUDIT_OUTPUT_BOUND')
            source.check()
        return result
    except (EvidenceError, KeyError, TypeError, ValueError, OverflowError, OSError) as exc:
        result.update(status='GATED', reason=str(exc) if isinstance(exc, EvidenceError) else 'PWS_SCORE_AUDIT_MALFORMED_SELECTION',
            rows=[], complete_retained_selection=False, score_matches=0, all_scores_reproduced=False,
            original_status_counts={}, reproduced_measured_count=0)
        return result
