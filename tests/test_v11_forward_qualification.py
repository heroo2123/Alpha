"""Offline checks against retained Oct 5/6 Brain record shapes.

The proxy repairs only test inputs. It never modifies or qualifies the retained
archive; the unchanged archive is separately asserted to fail closed.
"""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from polymarket_scanner.v11.evidence import EvidenceError, EvidenceStore, digest
from polymarket_scanner.v11.forward_qualification import grouped_outcome

BASE = Path('/home/alphaadmin/AlphaV11_BrainForward')


@pytest.fixture(params=('2026-10-05', '2026-10-06'))
def retained(request):
    root = BASE / request.param
    if not (root / 'source.sqlite').exists():
        pytest.skip('local retained Brain ledger unavailable')
    store = EvidenceStore(root / 'source.sqlite', 'CHALLENGER:katl-shadow')
    capture_id = json.loads((root / 'capture-status.json').read_text())['capture_id']
    labels = json.loads((root / 'labels-status.json').read_text())['label_ids']
    return store, capture_id, labels


class RepairedView:
    """Deliberately incomplete diagnostic proxy for nonchronology mutations."""
    def __init__(self, source, labels):
        self.source, self.labels = source, labels
        self.changes = {}
        self.keep_selected_only = True

    def get(self, key):
        row = deepcopy(self.source.get(key))
        if row['kind'] == 'LABEL' and key in self.labels.values():
            raw = self.source.get(row['body']['payload']['source_capture_id'])
            row['body']['payload']['knowable_at'] = raw['body']['available_at']
        for path, value in self.changes.get(key, ()):
            obj = row
            for part in path[:-1]:
                obj = obj[part]
            obj[path[-1]] = value
        row['sha256'] = digest(row['body'])
        return row

    def records(self, **kwargs):
        if kwargs['kind'] == 'RULES':
            # Mutation-only proxy: it does not represent complete raw history.
            return []
        rows = self.source.records(**kwargs)
        if kwargs['kind'] == 'LABEL' and self.keep_selected_only:
            rows = [row for row in rows if row['id'] in self.labels.values()]
        return rows


def test_retained_days_reject_duplicate_labels_and_missing_admission(retained):
    store, capture_id, labels = retained
    with pytest.raises(EvidenceError, match='MISSING_DUPLICATE_OR_CONFLICTING_LABEL'):
        grouped_outcome(store, capture_id, labels)
    group = grouped_outcome(RepairedView(store, labels), capture_id, labels)
    assert len(group['market_ids']) == 11
    assert sum(row['value'] for row in group['labels']) == 1
    assert group['admission_ref'] is None
    assert group['financial_authority'] is False
    assert group['independent_label_attestation'] is False


def test_adversarial_lineage_and_group_mutations(retained):
    store, capture_id, labels = retained
    view = RepairedView(store, labels)
    cap = store.get(capture_id)
    child = cap['body']['details']['rows'][0]
    first_mid = child['target_identity']['market_id']
    label_id = labels[first_mid]
    raw_id = store.get(label_id)['body']['payload']['source_capture_id']
    cases = [
        (capture_id, ('body', 'details', 'rows'), cap['body']['details']['rows'][:-1], 'INCOMPLETE_PARTITION'),
        (capture_id, ('body', 'details', 'binding', 'code_commit'), 'f' * 40, 'DECISION_LINEAGE'),
        (capture_id, ('body', 'details', 'binding', 'rule_fingerprint'), 'f' * 64, 'EVENT_RULE_BINDING'),
        (child['decision_id'], ('event_id',), 'other-event', 'LINEAGE_MISMATCH'),
        (child['decision_id'], ('body', 'feature_ready_at'), 0., 'DECISION_LINEAGE'),
        (child['decision_id'], ('body', 'expires_at'), 1., 'DECISION_LINEAGE'),
        (label_id, ('body', 'payload', 'target_identity', 'market_id'), 'wrong-market', 'LABEL_PROVENANCE'),
        (raw_id, ('body', 'payload', 'response', 'id'), 'wrong-market', 'LABEL_PROVENANCE'),
        (label_id, ('body', 'payload', 'value'), 1 - store.get(label_id)['body']['payload']['value'], 'LABEL_PROVENANCE'),
        (label_id, ('body', 'payload', 'knowable_at'), 0., 'LABEL_PROVENANCE'),
    ]
    for key, path, value, reason in cases:
        view.changes = {key: [(path, value)]}
        with pytest.raises(EvidenceError, match=reason):
            grouped_outcome(view, capture_id, labels)
    view.changes = {}
    with pytest.raises(EvidenceError, match='LABEL_SET_INCOMPLETE'):
        grouped_outcome(view, capture_id, {k: v for k, v in labels.items() if k != first_mid})
    with pytest.raises(EvidenceError, match='LABEL_SET_INCOMPLETE'):
        grouped_outcome(view, capture_id, {k: label_id for k in labels})
    view.keep_selected_only = False
    with pytest.raises(EvidenceError, match='MISSING_DUPLICATE_OR_CONFLICTING_LABEL'):
        grouped_outcome(view, capture_id, labels)
