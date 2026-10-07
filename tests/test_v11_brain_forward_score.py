from __future__ import annotations
import importlib.util
import json
from pathlib import Path

import pytest

from polymarket_scanner.v11.evidence import EvidenceError, EvidenceStore

TOOLS = Path(__file__).resolve().parents[1] / 'tools'


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


score = load(TOOLS / 'v11_brain_forward_score.py', 'v11_brain_forward_score_test_target')
supervisor = load(TOOLS / 'v11_brain_forward_supervisor.py', 'v11_brain_forward_supervisor_test_target')

NAMESPACE = 'CHALLENGER:katl-shadow'


def make_day(base, day, event_id, label_values, *, n_markets=None, labeled=True,
             duplicate_label=False, mismatched_market_id=False, clock_at=1000.0):
    """Build one captured Brain-forward day with n_markets buckets.

    label_values, when given, is the one-hot (or deliberately malformed) truth
    vector; when labeled=False no labels-status.json is written at all.
    """
    if n_markets is None:
        n_markets = len(label_values) if label_values is not None else 3
    root = base / day
    root.mkdir(mode=0o700, parents=True)
    store = EvidenceStore(root / 'source.sqlite', NAMESPACE, clock=lambda: clock_at)
    market_ids = [f'{event_id}-m{i}' for i in range(n_markets)]
    point = 1.0 / n_markets
    rows = []
    for i, mid in enumerate(market_ids):
        rec = store.audit(f'measurement-{mid}', event_id=event_id, kind='MEASUREMENT',
                           details={'explanation': {'point': point}})
        rows.append({'decision_id': rec['id'], 'target_identity': {'market_id': mid}})
    capture_rec = store.audit('capture-basket', event_id=event_id, kind='REGISTRY',
                               details={'rows': rows})
    (root / 'capture-status.json').write_text(json.dumps(
        {'capture_id': capture_rec['id'], 'prediction_sha256': 'a' * 64}))
    if labeled:
        label_ids = {}
        for i, mid in enumerate(market_ids):
            key = f'label-{mid}'
            payload = {'market_id': 'WRONG_MARKET' if (mismatched_market_id and i == 0) else mid,
                       'value': label_values[i]}
            lab = store.capture(key, event_id=event_id, kind='LABEL',
                                 provider='GAMMA_CLOSED_MARKET_EXACT_TOKEN_PAYOUT',
                                 source_identity=mid, revision='1', payload=payload,
                                 evidence_class='PUBLIC_OBSERVED')
            label_ids[mid] = lab['id']
        if duplicate_label:
            label_ids[market_ids[1]] = label_ids[market_ids[0]]
        (root / 'labels-status.json').write_text(json.dumps({'label_ids': label_ids}))
    return root


def test_mixed_labeled_and_unlabeled_days_scores_only_resolved_subset(tmp_path):
    make_day(tmp_path, '2026-10-05', 'event-05', [0, 1, 0])
    make_day(tmp_path, '2026-10-06', 'event-06', [1, 0, 0])
    make_day(tmp_path, '2026-10-07', 'event-07', None, labeled=False)
    out = score.run(tmp_path)
    assert [r['day'] for r in out['days']] == ['2026-10-05', '2026-10-06']
    assert out['pending_days'] == ['2026-10-07']
    assert out['new_challenger_trained'] is False
    assert out['automatic_promotion'] is False
    assert out['scores']['n_city_days'] == 2
    assert (tmp_path / 'forward-validation-status.json').exists()


def test_all_unlabeled_days_wait_without_persisting_result(tmp_path):
    make_day(tmp_path, '2026-10-05', 'event-05', None, labeled=False)
    make_day(tmp_path, '2026-10-06', 'event-06', None, labeled=False)
    with pytest.raises(SystemExit, match='WAITING_FORWARD_LABELS'):
        score.run(tmp_path)
    assert not (tmp_path / 'forward-validation-status.json').exists()


def test_malformed_labeled_day_fails_closed_not_silently_dropped(tmp_path):
    make_day(tmp_path, '2026-10-05', 'event-05', [1, 1, 0])  # two winners
    with pytest.raises(RuntimeError, match='FORWARD_VECTOR_INVALID:2026-10-05'):
        score.run(tmp_path)
    assert not (tmp_path / 'forward-validation-status.json').exists()


def test_duplicate_label_record_id_is_rejected(tmp_path):
    make_day(tmp_path, '2026-10-05', 'event-05', [0, 1, 0], duplicate_label=True)
    with pytest.raises(RuntimeError, match='DUPLICATE_LABEL_RECORD_ID:2026-10-05'):
        score.run(tmp_path)
    assert not (tmp_path / 'forward-validation-status.json').exists()


def test_label_market_identity_mismatch_is_rejected(tmp_path):
    make_day(tmp_path, '2026-10-05', 'event-05', [0, 1, 0], mismatched_market_id=True)
    with pytest.raises(RuntimeError, match='LABEL_MARKET_IDENTITY_MISMATCH:2026-10-05'):
        score.run(tmp_path)
    assert not (tmp_path / 'forward-validation-status.json').exists()


def test_new_later_day_becoming_labeled_extends_the_resolved_set(tmp_path):
    make_day(tmp_path, '2026-10-05', 'event-05', [0, 1, 0])
    make_day(tmp_path, '2026-10-06', 'event-06', None, labeled=False)
    first = score.run(tmp_path)
    assert [r['day'] for r in first['days']] == ['2026-10-05']
    assert first['pending_days'] == ['2026-10-06']
    # Oct 6 resolves later: relabel it in place, exactly like the market closing.
    root = tmp_path / '2026-10-06'
    import shutil
    shutil.rmtree(root)
    make_day(tmp_path, '2026-10-06', 'event-06', [1, 0, 0])
    second = score.run(tmp_path)
    assert [r['day'] for r in second['days']] == ['2026-10-05', '2026-10-06']
    assert second['pending_days'] == []


def test_no_fit_or_promotion_from_two_city_days(tmp_path):
    make_day(tmp_path, '2026-10-05', 'event-05', [0, 1, 0])
    make_day(tmp_path, '2026-10-06', 'event-06', [1, 0, 0])
    out = score.run(tmp_path)
    assert out['new_challenger_trained'] is False
    assert out['automatic_promotion'] is False
    assert out['scores']['n_city_days'] == 2
    assert not list(tmp_path.glob('*fit*'))


def test_rerun_without_new_evidence_is_byte_identical(tmp_path):
    make_day(tmp_path, '2026-10-05', 'event-05', [0, 1, 0])
    make_day(tmp_path, '2026-10-06', 'event-06', [1, 0, 0])
    first = score.run(tmp_path)
    path = tmp_path / 'forward-validation-status.json'
    before = path.read_text()
    second = score.run(tmp_path)
    assert first == second
    assert path.read_text() == before


def test_no_captures_at_all_waits(tmp_path):
    with pytest.raises(SystemExit, match='WAITING_FORWARD_CAPTURES'):
        score.run(tmp_path)


def test_causal_append_order_is_still_enforced_by_the_evidence_store(tmp_path):
    root = tmp_path / '2026-10-05'
    root.mkdir(mode=0o700, parents=True)
    store = EvidenceStore(root / 'source.sqlite', NAMESPACE, clock=lambda: 1000.0)
    with pytest.raises(EvidenceError, match='SOURCE_TIME_IN_FUTURE'):
        store.capture('future-label', event_id='event-05', kind='LABEL',
                       provider='GAMMA_CLOSED_MARKET_EXACT_TOKEN_PAYOUT', source_identity='m',
                       revision='1', payload={'market_id': 'm', 'value': 1},
                       evidence_class='PUBLIC_OBSERVED', observed_at=2000.0)
