from __future__ import annotations
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

from polymarket_scanner.v11.evidence import EvidenceStore, ReleaseBinding

REPO_ROOT = Path(__file__).resolve().parents[1]
TOOLS = REPO_ROOT / 'tools'


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


score = load(TOOLS / 'v11_brain_forward_score.py', 'v11_brain_forward_score_sup_target')
supervisor = load(TOOLS / 'v11_brain_forward_supervisor.py', 'v11_brain_forward_supervisor_sup_target')

NAMESPACE = 'CHALLENGER:katl-shadow'

# A real ReleaseBinding, structurally identical to the one capture_forward.py
# pins per day (see learning_capture.capture_forecast_vector -> store.decision).
BINDING = ReleaseBinding(code_commit='a' * 40, code_tree='b' * 40, config_sha256='c' * 64,
                          bundle_sha256='d' * 64, rule_fingerprint='e' * 64)


def make_day(base, day, event_id, label_values, *, labeled=True, duplicate_label=False, clock_at=1000.0):
    """Build one captured Brain-forward day using the REAL record shapes the
    production pipeline writes (see tests/test_v11_brain_forward_score.py for
    why: real DECISION bodies carry 'explanation' at the top level, and real
    LABEL payloads carry identity under payload['target_identity'], never a
    top-level 'market_id')."""
    n_markets = len(label_values) if label_values is not None else 3
    root = base / day
    root.mkdir(mode=0o700, parents=True)
    store = EvidenceStore(root / 'source.sqlite', NAMESPACE, clock=lambda: clock_at)
    market_ids = [f'{event_id}-m{i}' for i in range(n_markets)]
    point = 1.0 / n_markets
    rows = []
    for mid in market_ids:
        target = {'market_id': mid, 'condition_id': f'cond-{mid}', 'token_id': f'token-{mid}', 'side': 'YES'}
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
    capture_rec = store.audit('capture-basket', event_id=event_id, kind='REGISTRY', details={'rows': rows})
    (root / 'capture-status.json').write_text(json.dumps(
        {'capture_id': capture_rec['id'], 'prediction_sha256': 'a' * 64}))
    if labeled:
        label_ids = {}
        for i, mid in enumerate(market_ids):
            payload = {'context': {'city_id': 'atlanta'}, 'label_version': '1',
                       'decision_target': 'FINAL_CONTRACT_PAYOUT',
                       'target_identity': {'market_id': mid, 'condition_id': f'cond-{mid}',
                                            'token_id': f'token-{mid}', 'side': 'YES'},
                       'knowable_at': clock_at, 'value': label_values[i], 'evidence_type': 'EXACT_SOURCE_LABEL',
                       'source_capture_id': 'raw-' + mid, 'source_capture_sha256': 'f' * 64,
                       'independent_label_attestation': False, 'financial_authority': False}
            lab = store.capture(f'label-{mid}', event_id=event_id, kind='LABEL',
                                 provider='GAMMA_CLOSED_MARKET_EXACT_TOKEN_PAYOUT',
                                 source_identity=mid, revision='1', payload=payload,
                                 evidence_class='PUBLIC_OBSERVED')
            label_ids[mid] = lab['id']
        if duplicate_label:
            label_ids[market_ids[1]] = label_ids[market_ids[0]]
        (root / 'labels-status.json').write_text(json.dumps({'label_ids': label_ids}))
    return root


def in_process_runner(base, timeout=60):
    return score.run(base)


def subprocess_score_runner(base, timeout=60):
    """A faithful subprocess-shaped runner: it actually execs a separate
    python process that loads the frozen tools/v11_brain_forward_score.py and
    calls run(base), capturing stdout+stderr exactly like the production
    default_score_runner (subprocess.run(..., check=False)). On a fail-closed
    scorer exception, the process exits non-zero and never prints its final
    JSON result line, so last_json() falls back to {'raw': <tail>} -- this is
    the real failure shape the supervisor must not mistake for success.
    """
    script = (
        f"import sys; sys.path.insert(0, {str(REPO_ROOT)!r})\n"
        "import importlib.util\n"
        f"spec = importlib.util.spec_from_file_location('s', {str(TOOLS / 'v11_brain_forward_score.py')!r})\n"
        "m = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(m)\n"
        "from pathlib import Path\n"
        f"m.run(Path({str(base)!r}))\n"
    )
    proc = subprocess.run([sys.executable, '-c', script], cwd=str(base), stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, text=True, timeout=timeout, check=False)
    return supervisor.last_json(proc.stdout.strip())


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


def test_subprocess_runner_malformed_day_gates_not_attempted(tmp_path):
    """F4, same scenario as test_malformed_labeled_day_gates_the_supervisor_not_silently
    but through a REAL subprocess call (the production shape), not an in-process
    one. Before the fix, the in-process GATED test passed only because letting an
    exception propagate in-process is trivial; under the real subprocess runner
    the failure surfaced as FORWARD_VALIDATION_ATTEMPTED instead of GATED.
    """
    make_day(tmp_path, '2026-10-05', 'event-05', [1, 1, 0])  # two winners: malformed
    status = supervisor.iterate(tmp_path, score_runner=subprocess_score_runner)
    assert status['state'] == 'GATED'
    assert 'FORWARD_SCORE_RUNNER_FAILED' in status['error']
    assert not (tmp_path / 'forward-validation-status.json').exists()


def test_subprocess_runner_newly_failing_day_gates_despite_stale_result_file(tmp_path):
    """F4, same scenario as the reviewer's D4 probe: day 05 is scored first and
    leaves a forward-validation-status.json on disk, then day 06 resolves with
    a duplicate label record id (fails closed). Before the fix, the supervisor
    judged success purely by whether that status file existed on disk, so the
    STALE file from day 05's earlier successful run made the newly-resolved,
    actually-failing set report FORWARD_VALIDATION_COMPLETE instead of GATED.
    """
    make_day(tmp_path, '2026-10-05', 'event-05', [0, 1, 0])
    first = supervisor.iterate(tmp_path, score_runner=subprocess_score_runner)
    assert first['state'] == 'FORWARD_VALIDATION_COMPLETE'
    assert (tmp_path / 'forward-validation-status.json').exists()
    make_day(tmp_path, '2026-10-06', 'event-06', [1, 0, 0], duplicate_label=True)
    second = supervisor.iterate(tmp_path, score_runner=subprocess_score_runner)
    assert second['state'] == 'GATED'
    assert 'FORWARD_SCORE_RUNNER_FAILED' in second['error']
