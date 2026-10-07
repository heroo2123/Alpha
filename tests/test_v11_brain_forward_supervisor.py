from __future__ import annotations
import importlib.util
import shutil
from pathlib import Path

from polymarket_scanner.v11.evidence import EvidenceStore

TOOLS = Path(__file__).resolve().parents[1] / 'tools'


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


score = load(TOOLS / 'v11_brain_forward_score.py', 'v11_brain_forward_score_sup_target')
supervisor = load(TOOLS / 'v11_brain_forward_supervisor.py', 'v11_brain_forward_supervisor_sup_target')

NAMESPACE = 'CHALLENGER:katl-shadow'


def make_day(base, day, event_id, label_values, *, labeled=True, clock_at=1000.0):
    n_markets = len(label_values) if label_values is not None else 3
    root = base / day
    root.mkdir(mode=0o700, parents=True)
    store = EvidenceStore(root / 'source.sqlite', NAMESPACE, clock=lambda: clock_at)
    market_ids = [f'{event_id}-m{i}' for i in range(n_markets)]
    point = 1.0 / n_markets
    rows = []
    for mid in market_ids:
        rec = store.audit(f'measurement-{mid}', event_id=event_id, kind='MEASUREMENT',
                           details={'explanation': {'point': point}})
        rows.append({'decision_id': rec['id'], 'target_identity': {'market_id': mid}})
    capture_rec = store.audit('capture-basket', event_id=event_id, kind='REGISTRY', details={'rows': rows})
    import json
    (root / 'capture-status.json').write_text(json.dumps(
        {'capture_id': capture_rec['id'], 'prediction_sha256': 'a' * 64}))
    if labeled:
        label_ids = {}
        for i, mid in enumerate(market_ids):
            lab = store.capture(f'label-{mid}', event_id=event_id, kind='LABEL',
                                 provider='GAMMA_CLOSED_MARKET_EXACT_TOKEN_PAYOUT',
                                 source_identity=mid, revision='1',
                                 payload={'market_id': mid, 'value': label_values[i]},
                                 evidence_class='PUBLIC_OBSERVED')
            label_ids[mid] = lab['id']
        (root / 'labels-status.json').write_text(json.dumps({'label_ids': label_ids}))
    return root


def in_process_runner(base, timeout=60):
    return score.run(base)


def test_waiting_resolved_evidence_when_nothing_is_labeled(tmp_path):
    make_day(tmp_path, '2026-10-05', 'event-05', None, labeled=False)
    status = supervisor.iterate(tmp_path, score_runner=in_process_runner)
    assert status['state'] == 'WAITING_RESOLVED_EVIDENCE'
    assert status['fit'] is None
    assert status['automatic_promotion'] is False
    assert not (tmp_path / 'forward-validation-status.json').exists()


def test_mixed_days_report_scored_resolved_with_pending(tmp_path):
    make_day(tmp_path, '2026-10-05', 'event-05', [0, 1, 0])
    make_day(tmp_path, '2026-10-06', 'event-06', [1, 0, 0])
    make_day(tmp_path, '2026-10-07', 'event-07', None, labeled=False)
    status = supervisor.iterate(tmp_path, score_runner=in_process_runner)
    assert status['state'] == 'HAS_SCORED_RESOLVED_EVIDENCE_WITH_PENDING_DAYS'
    assert status['fit']['new_challenger_trained'] is False
    assert status['fit']['automatic_promotion'] is False
    assert [r['day'] for r in status['fit']['days']] == ['2026-10-05', '2026-10-06']
    assert status['fit']['pending_days'] == ['2026-10-07']
    assert status['days']['2026-10-07']['state'] == 'CAPTURED_WAITING_LABEL'
    assert status['days']['2026-10-05']['state'] == 'LABELED'
    # This is not a readiness or champion claim.
    assert 'CONTROLLED_LEARNING_READY' not in str(status)
    assert 'ACCEPTED_CHAMPION' not in str(status)


def test_all_resolved_days_report_forward_validation_complete(tmp_path):
    make_day(tmp_path, '2026-10-05', 'event-05', [0, 1, 0])
    make_day(tmp_path, '2026-10-06', 'event-06', [1, 0, 0])
    status = supervisor.iterate(tmp_path, score_runner=in_process_runner)
    assert status['state'] == 'FORWARD_VALIDATION_COMPLETE'
    assert status['fit']['pending_days'] == []


def test_malformed_labeled_day_gates_the_supervisor_not_silently(tmp_path):
    make_day(tmp_path, '2026-10-05', 'event-05', [1, 1, 0])  # two winners: malformed
    status = supervisor.iterate(tmp_path, score_runner=in_process_runner)
    assert status['state'] == 'GATED'
    assert 'FORWARD_VECTOR_INVALID' in status['error']
    assert not (tmp_path / 'forward-validation-status.json').exists()


def test_rerun_without_new_resolved_evidence_does_not_rescore(tmp_path):
    make_day(tmp_path, '2026-10-05', 'event-05', [0, 1, 0])
    make_day(tmp_path, '2026-10-06', 'event-06', [1, 0, 0])
    make_day(tmp_path, '2026-10-07', 'event-07', None, labeled=False)
    calls = []

    def counting_runner(base, timeout=60):
        calls.append(1)
        return score.run(base)

    first = supervisor.iterate(tmp_path, score_runner=counting_runner)
    second = supervisor.iterate(tmp_path, score_runner=counting_runner)
    assert len(calls) == 1  # second iteration must reuse the persisted result
    assert first['fit'] == second['fit']
    assert first['state'] == second['state'] == 'HAS_SCORED_RESOLVED_EVIDENCE_WITH_PENDING_DAYS'


def test_new_later_day_becoming_labeled_triggers_a_rescore(tmp_path):
    make_day(tmp_path, '2026-10-05', 'event-05', [0, 1, 0])
    make_day(tmp_path, '2026-10-06', 'event-06', None, labeled=False)
    first = supervisor.iterate(tmp_path, score_runner=in_process_runner)
    assert first['fit']['pending_days'] == ['2026-10-06']
    shutil.rmtree(tmp_path / '2026-10-06')
    make_day(tmp_path, '2026-10-06', 'event-06', [1, 0, 0])
    second = supervisor.iterate(tmp_path, score_runner=in_process_runner)
    assert second['state'] == 'FORWARD_VALIDATION_COMPLETE'
    assert [r['day'] for r in second['fit']['days']] == ['2026-10-05', '2026-10-06']
