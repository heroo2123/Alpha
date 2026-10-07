from __future__ import annotations
import importlib.util
import json
from pathlib import Path

import pytest

from polymarket_scanner.v11.evidence import EvidenceError, EvidenceStore, ReleaseBinding

TOOLS = Path(__file__).resolve().parents[1] / 'tools'


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


score = load(TOOLS / 'v11_brain_forward_score.py', 'v11_brain_forward_score_test_target')
supervisor = load(TOOLS / 'v11_brain_forward_supervisor.py', 'v11_brain_forward_supervisor_test_target')

NAMESPACE = 'CHALLENGER:katl-shadow'

# A real ReleaseBinding, structurally identical to the one capture_forward.py
# pins per day (see learning_capture.capture_forecast_vector -> store.decision).
BINDING = ReleaseBinding(code_commit='a' * 40, code_tree='b' * 40, config_sha256='c' * 64,
                          bundle_sha256='d' * 64, rule_fingerprint='e' * 64)


def make_day(base, day, event_id, label_values, *, n_markets=None, labeled=True,
             duplicate_label=False, mismatched_market_id=False, clock_at=1000.0):
    """Build one captured Brain-forward day with n_markets buckets, using the
    REAL record shapes the production pipeline writes:
      - decisions via EvidenceStore.decision() (learning_capture.py), whose body
        carries 'explanation' at the top level, not nested under 'details';
      - labels via EvidenceStore.capture(kind='LABEL') with a payload shaped like
        label_forward.py's real output: identity lives in payload['target_identity'],
        there is no top-level 'market_id'.

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
    targets = []
    for i, mid in enumerate(market_ids):
        target = {'market_id': mid, 'condition_id': f'cond-{mid}', 'token_id': f'token-{mid}', 'side': 'YES'}
        targets.append(target)
        feature = store.capture(f'feature-{mid}', event_id=event_id, kind='FEATURES',
                                 provider='TEST_FEATURES', source_identity=mid, revision='1',
                                 payload={'target_identity': target}, evidence_class='PUBLIC_OBSERVED')
        decision = store.decision(f'decision-{mid}', event_id=event_id, strategy='FUTURE_FORECAST',
                                   binding=BINDING, evidence_ids=(feature['id'],),
                                   feature_ready_at=clock_at, valuation_type='SETTLEMENT',
                                   target='FINAL_CONTRACT_PAYOUT', outcome='GATED',
                                   reason='FORECAST_ONLY_NO_EXECUTABLE_ECONOMICS',
                                   explanation={'point': point, 'target_identity': target},
                                   expires_at=clock_at + 1.0)
        rows.append({'decision_id': decision['id'], 'target_identity': target})
    capture_rec = store.audit('capture-basket', event_id=event_id, kind='REGISTRY',
                               details={'rows': rows})
    (root / 'capture-status.json').write_text(json.dumps(
        {'capture_id': capture_rec['id'], 'prediction_sha256': 'a' * 64}))
    if labeled:
        label_ids = {}
        for i, mid in enumerate(market_ids):
            key = f'label-{mid}'
            target_identity = dict(targets[i])
            if mismatched_market_id and i == 0:
                # A real-shaped label record whose own target_identity names a
                # DIFFERENT market than the key it is filed under in
                # labels-status.json -- the swapped-winner/loser scenario the
                # reviewer reproduced on real 2026-10-05 evidence.
                target_identity = dict(targets[(i + 1) % n_markets])
            payload = {'context': {'city_id': 'atlanta'}, 'label_version': '1',
                       'decision_target': 'FINAL_CONTRACT_PAYOUT', 'target_identity': target_identity,
                       'knowable_at': clock_at, 'value': label_values[i], 'evidence_type': 'EXACT_SOURCE_LABEL',
                       'source_capture_id': 'raw-' + mid, 'source_capture_sha256': 'f' * 64,
                       'independent_label_attestation': False, 'financial_authority': False}
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
    # F3: the persisted artifact must not claim completeness while a day is
    # still pending labels.
    assert out['state'] == 'HAS_SCORED_RESOLVED_EVIDENCE_WITH_PENDING_DAYS'
    persisted = json.loads((tmp_path / 'forward-validation-status.json').read_text())
    assert persisted['state'] == 'HAS_SCORED_RESOLVED_EVIDENCE_WITH_PENDING_DAYS'


def test_all_resolved_reports_complete_state(tmp_path):
    make_day(tmp_path, '2026-10-05', 'event-05', [0, 1, 0])
    make_day(tmp_path, '2026-10-06', 'event-06', [1, 0, 0])
    out = score.run(tmp_path)
    assert out['pending_days'] == []
    assert out['state'] == 'FORWARD_VALIDATION_COMPLETE'


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


def test_winner_and_loser_label_record_ids_swapped_after_filing_is_rejected(tmp_path):
    """Reproduces the reviewer's exact C1 repro against a real-shaped day: the
    labels-status.json file itself (not the fixture builder) is mutated after
    filing to point the winner's market key at the loser's LABEL record and
    vice versa. Before the fix this scored successfully and named the wrong
    market as winner because the check only compared an absent top-level
    payload['market_id'] (always None on real data) to the key.
    """
    make_day(tmp_path, '2026-10-05', 'event-05', [0, 1, 0])
    path = tmp_path / '2026-10-05' / 'labels-status.json'
    ls = json.loads(path.read_text())
    winner, loser = 'event-05-m1', 'event-05-m0'
    ls['label_ids'][winner], ls['label_ids'][loser] = ls['label_ids'][loser], ls['label_ids'][winner]
    path.write_text(json.dumps(ls))
    with pytest.raises(RuntimeError, match='LABEL_MARKET_IDENTITY_MISMATCH:2026-10-05'):
        score.run(tmp_path)
    assert not (tmp_path / 'forward-validation-status.json').exists()


def test_two_losing_markets_crossfiled_is_rejected(tmp_path):
    """Reproduces the reviewer's C2 repro: two LOSING markets' label records
    are cross-filed (ids stay globally unique, so DUPLICATE_LABEL_RECORD_ID
    does not fire, and the truth vector would still sum to exactly one winner).
    The misattribution must still be caught via target_identity, since the
    label filed under m0 now actually belongs to m2.
    """
    make_day(tmp_path, '2026-10-05', 'event-05', [0, 1, 0])
    path = tmp_path / '2026-10-05' / 'labels-status.json'
    ls = json.loads(path.read_text())
    a, c = 'event-05-m0', 'event-05-m2'
    ls['label_ids'][a], ls['label_ids'][c] = ls['label_ids'][c], ls['label_ids'][a]
    path.write_text(json.dumps(ls))
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
