"""Offline mechanism checks; these fixtures are never calibration evidence."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from polymarket_scanner.v11.evidence import EvidenceError, digest
from polymarket_scanner.v11.execution_costs import SELECTION as COST_SELECTION
from polymarket_scanner.v11.fill_markout import SELECTION as MARKOUT_SELECTION
from tools.v11_brain_readiness import CANDIDATES, DATASET_SHA256, MANIFEST_SHA256, evaluate


HIGH = 'daily_high_temperature'


def fixture(evidence_class='SYNTHETIC'):
    split = {'SYNTHETIC': 'SYNTHETIC', 'HISTORICAL_CORRECTED': 'HISTORICAL_CONFIRMATION',
             'FORWARD_SHADOW': 'FORWARD_SHADOW'}[evidence_class]
    spec = dict(evidence_class=evidence_class, unit='C', candidate_manifest_sha256=MANIFEST_SHA256,
        candidate_bundles={f: h for (f, u), h in CANDIDATES.items() if u == 'C'},
        event_ids=['event-1', 'event-2'], source_sha256=DATASET_SHA256 if evidence_class == 'HISTORICAL_CORRECTED' else 'a'*64,
        evaluation_cutoff=50.)
    observations = []
    for n in (1, 2):
        observations.append(dict(event_id=f'event-{n}', city_day=f'city:2026-10-0{n}', station='station',
            local_date=f'2026-10-0{n}', family=HIGH, split=split, winner=n-1,
            decision_at=10.+n, source_received_at=None if evidence_class == 'HISTORICAL_CORRECTED' else 9.+n,
            label_received_at=20.+n, source_sha256=spec['source_sha256'], label_sha256='c'*64,
            candidate_bundle_sha256=CANDIDATES[HIGH, 'C'], probabilities={'BASELINE': [0.5, 0.5],
                'CHAMPION': [0.6, 0.4], 'CANDIDATE': [0.8, 0.2]},
            provider_probabilities={'GEFS': [0.8, 0.2], 'IFS': [0.6, 0.4], 'AIFS': [0.4, 0.6]},
            provider_groups={'GEFS': 'NOAA_GEFS', 'IFS': 'ECMWF_LINEAGE', 'AIFS': 'ECMWF_LINEAGE'}))
    return spec, observations


def test_scores_are_deterministic_and_never_claim_calibration():
    spec, rows = fixture()
    a = evaluate(spec, rows)
    assert a == evaluate(spec, list(reversed(rows)))
    assert a['scores']['CANDIDATE']['brier'] == pytest.approx(.68)
    assert a['scores']['CANDIDATE']['log_loss'] is not None
    assert len(a['scores']['CANDIDATE']['reliability']) == 10
    assert a['candidate_comparisons']['BASELINE']['date_blocks'] == 2
    assert a['candidate_comparisons']['CHAMPION']['interval'].startswith('DESCRIPTIVE_')
    assert a['multimodel']['scores']['events'] == 2
    assert a['calibration_status'] == 'CALIBRATION_EVIDENCE_PENDING_FORWARD_DATA'
    assert a['promotion_status'] == 'NO_PROMOTION'
    assert a['execution']['known_costs'] is None
    assert a['execution']['paper_pnl'] is None
    assert a['report_sha256'] == digest({k: v for k, v in a.items() if k != 'report_sha256'})


def test_rejects_cohort_changes_wrong_lineage_and_lookahead():
    spec, rows = fixture()
    for mutation in (
        lambda s, r: s['event_ids'].pop(),
        lambda s, r: s.update(candidate_manifest_sha256='b'*64),
        lambda s, r: r[0]['probabilities'].pop('BASELINE'),
        lambda s, r: r[0].update(label_received_at=10.),
        lambda s, r: r[0].update(source_received_at=30.),
        lambda s, r: r[0].update(candidate_bundle_sha256='b'*64),
        lambda s, r: r[0].update(event_id='event-2'),
    ):
        s, r = deepcopy(spec), deepcopy(rows)
        mutation(s, r)
        with pytest.raises(EvidenceError):
            evaluate(s, r)


def test_historical_partition_is_diagnostic_not_forward_evidence():
    spec, rows = fixture('HISTORICAL_CORRECTED')
    out = evaluate(spec, rows)
    assert out['historical_confirmation_is_forward_holdout'] is False
    assert out['calibration_status'] == 'CALIBRATION_EVIDENCE_PENDING_FORWARD_DATA'
    rows[0]['source_received_at'] = 1.
    with pytest.raises(EvidenceError, match='HISTORICAL_AVAILABILITY_UNKNOWN'):
        evaluate(spec, rows)


def test_forward_rows_are_scored_without_automatic_acceptance():
    spec, rows = fixture('FORWARD_SHADOW')
    out = evaluate(spec, rows)
    assert out['scores']['CANDIDATE']['events'] == 2
    assert out['independent_acceptance'] is False
    assert out['multimodel']['scores'] is None


def test_missing_provider_fails_closed_and_groups_cannot_be_renamed():
    spec, rows = fixture()
    del rows[0]['provider_probabilities']['AIFS']
    del rows[0]['provider_groups']['AIFS']
    out = evaluate(spec, rows)
    assert out['multimodel']['status'] == 'INCOMPLETE_PROVIDER_COVERAGE'
    assert out['multimodel']['scores'] is None
    rows[0]['provider_groups']['IFS'] = 'INDEPENDENT'
    with pytest.raises(EvidenceError, match='PROVIDER_GROUPS'):
        evaluate(spec, rows)


def test_paper_cost_and_markout_are_bound_and_missing_values_remain_unknown():
    spec, rows = fixture()
    cost = dict(selection=COST_SELECTION, execution_namespace='V11_PAPER', financial_authority=False,
        execution_class='SYNTHETIC_PAPER_FILL', status='PARTIAL', complete_cost_population=False,
        complete_price_comparisons=False, rows=[dict(model_bundle_sha256=CANDIDATES[HIGH, 'C'],
            event_id='event-1', total_cost_vs_signal=None, price_shortfall_vs_signal=None)])
    markout = dict(selection=MARKOUT_SELECTION, execution_class='SYNTHETIC_PAPER_FILL',
        financial_authority=False, request=dict(bundle_sha256=CANDIDATES[HIGH, 'C']),
        rows=[dict(event_id='event-1', status='UNKNOWN')], outcome='INCOMPLETE_HORIZON_COHORT',
        cohort_sufficient=False, scores=dict(mean_fill_markout_per_share=None))
    out = evaluate(spec, rows, cost_report=cost, markout_report=markout)
    assert out['execution']['known_costs'] is None
    assert out['execution']['mean_fill_markout_per_share'] is None
    assert out['execution']['cost_report_sha256'] == digest(cost)
    assert out['execution']['markout_report_sha256'] == digest(markout)
    cost['rows'][0]['model_bundle_sha256'] = 'b'*64
    with pytest.raises(EvidenceError, match='COST_SCOPE'):
        evaluate(spec, rows, cost_report=cost)


def test_cli_reproduces_report_and_rejects_duplicate_keys(tmp_path):
    spec, rows = fixture()
    path = tmp_path / 'input.json'
    path.write_text(json.dumps({'spec': spec, 'observations': rows}))
    command = [sys.executable, str(Path(__file__).resolve().parents[1] / 'tools/v11_brain_readiness.py'), str(path)]
    first = subprocess.run(command, check=True, capture_output=True, text=True).stdout
    second = subprocess.run(command, check=True, capture_output=True, text=True).stdout
    assert first == second
    assert json.loads(first) == evaluate(spec, rows)
    path.write_text('{"spec":{},"spec":{},"observations":[]}')
    assert subprocess.run(command, capture_output=True, text=True).returncode != 0


def test_complete_paper_diagnostics_are_kept_separate_from_real_pnl():
    spec, rows = fixture()
    cost_row = dict(model_bundle_sha256=CANDIDATES[HIGH, 'C'], event_id='event-1',
        cost_status='VALIDATED_SYNTHETIC_DETAILS', signal={'status': 'MATCHED_VISIBLE_DEPTH'},
        post_validation={'status': 'MATCHED_VISIBLE_DEPTH'}, total_cost_vs_signal='0.03',
        price_shortfall_vs_signal='0.01')
    cost = dict(selection=COST_SELECTION, execution_namespace='V11_PAPER', financial_authority=False,
        execution_class='SYNTHETIC_PAPER_FILL', status='COMPLETE_SYNTHETIC_COSTS_AND_COMPARISONS',
        complete_cost_population=True, complete_price_comparisons=True, selected_fill_count=1,
        known_cost_count=1, rows=[cost_row])
    markout = dict(selection=MARKOUT_SELECTION, execution_class='SYNTHETIC_PAPER_FILL',
        financial_authority=False, request={'bundle_sha256': CANDIDATES[HIGH, 'C']},
        rows=[{'event_id': 'event-1', 'status': 'MEASURED'}], outcome='NO_DECLARED_BREACH',
        cohort_sufficient=True, scores={'n_fills': 1, 'mean_fill_markout_per_share': '-0.02'})
    out = evaluate(spec, rows, cost_report=cost, markout_report=markout)['execution']
    assert out['status'] == 'PAPER_COST_DIAGNOSTICS_ONLY'
    assert out['known_costs'] == ['0.03']
    assert out['mean_fill_markout_per_share'] == '-0.02'
    assert out['paper_pnl'] is None and out['drawdown'] is None and out['missed_fill_rate'] is None
    cost['known_cost_count'] = 0
    with pytest.raises(EvidenceError, match='COST_COMPLETENESS'):
        evaluate(spec, rows, cost_report=cost)
