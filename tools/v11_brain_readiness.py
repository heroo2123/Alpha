"""Offline, data-only Brain comparison machinery. No fitting or model authority.

The R47 v2 identity table is frozen to the corrected exact-local-day candidate
lineage. Supplied observations are scored as diagnostics; this module cannot
admit a real multi-model learner example or certify forward calibration.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from polymarket_scanner.v11.evidence import EvidenceError, canonical, digest, finite, identity, sha
from polymarket_scanner.v11.execution_costs import SELECTION as COST_SELECTION
from polymarket_scanner.v11.fill_markout import SELECTION as MARKOUT_SELECTION
from polymarket_scanner.v11.model_artifacts import parse_data
from tools.v11_multimodel_stacking import metrics, paired_date_bootstrap, pool, vector


VERSION = 'alpha_v11_brain_offline_readiness_v1'
MANIFEST_SHA256 = 'b9bcd07f252ab38b4a1e6b7c0b2f932fb5968e1bdb5f673e9152e213d27fa957'
DATASET_SHA256 = '649fd39acab88dd2508c34668f37228b173b8d5902c93f59f2b75f50bbf990a9'
CANDIDATES = {
    ('daily_high_temperature', 'C'): '005d661fab697e0d9075a0e80cd54f64cd931b8ca26d8fc50a807c4793c7d7a5',
    ('daily_high_temperature', 'F'): 'fd9a32aa12ac7c8018ec524d1b74514bb57c42c0e60241672a4446b95908e641',
    ('daily_low_temperature', 'C'): 'b9a067c9030f41740789a5761d9d4ee68d88192935eda6569d093bc2863887b8',
    ('daily_low_temperature', 'F'): 'e5478c88dc7aeca03f486efc845d94c2e2e5c8900f6775d7d0840191e83f3c6d',
}
ARMS = ('BASELINE', 'CHAMPION', 'CANDIDATE')
CLASSES = ('SYNTHETIC', 'HISTORICAL_CORRECTED', 'FORWARD_SHADOW')
GROUPS = {'GEFS': 'NOAA_GEFS', 'IFS': 'ECMWF_LINEAGE', 'AIFS': 'ECMWF_LINEAGE'}


def require(ok, reason):
    if not ok:
        raise EvidenceError(reason)


@dataclass(frozen=True)
class ScoreRow:
    event_id: str
    city_day: str
    station: str
    local_date: str
    winner: int


def _cohort(spec, observations):
    require(type(spec) is dict and set(spec) == {'evidence_class', 'unit', 'candidate_manifest_sha256',
        'candidate_bundles', 'event_ids', 'source_sha256', 'evaluation_cutoff'}, 'BRAIN_COHORT_SCHEMA')
    cls, unit = spec['evidence_class'], spec['unit']
    require(cls in CLASSES and unit in ('C', 'F'), 'BRAIN_COHORT_CLASS_UNIT')
    require(spec['candidate_manifest_sha256'] == MANIFEST_SHA256, 'BRAIN_CANDIDATE_MANIFEST_PIN')
    require(spec['candidate_bundles'] == {family: h for (family, u), h in CANDIDATES.items() if u == unit},
            'BRAIN_CANDIDATE_BUNDLE_PIN')
    require(spec['source_sha256'] == DATASET_SHA256 if cls == 'HISTORICAL_CORRECTED' else
            isinstance(spec['source_sha256'], str), 'BRAIN_SOURCE_PIN')
    sha(spec['source_sha256'])
    cutoff = finite(spec['evaluation_cutoff'])
    ids = spec['event_ids']
    require(type(ids) is list and 1 <= len(ids) <= 20000 and ids == sorted(set(ids)), 'BRAIN_FROZEN_EVENT_ROSTER')
    for event_id in ids:
        identity(event_id)
    require(type(observations) is list and len(observations) == len(ids), 'BRAIN_COMPLETE_POPULATION')
    return cls, cutoff


def _score_rows(spec, observations):
    cls, cutoff = _cohort(spec, observations)
    rows, predictions, multi = [], {arm: [] for arm in ARMS}, []
    seen, city_dates, city_splits, station_days = set(), {}, {}, {}
    for item in observations:
        require(type(item) is dict and set(item) == {'event_id', 'city_day', 'station', 'local_date',
            'family', 'split', 'winner', 'decision_at', 'source_received_at', 'label_received_at',
            'source_sha256', 'label_sha256', 'candidate_bundle_sha256',
            'probabilities', 'provider_probabilities', 'provider_groups'}, 'BRAIN_OBSERVATION_SCHEMA')
        event_id, city_day = identity(item['event_id']), identity(item['city_day'])
        require(event_id not in seen, 'BRAIN_DUPLICATE_EVENT')
        seen.add(event_id)
        station = identity(item['station'])
        day = item['local_date']
        require(type(day) is str and date.fromisoformat(day).isoformat() == day, 'BRAIN_LOCAL_DATE')
        require(city_day not in city_dates or city_dates[city_day] == day, 'BRAIN_CITY_DAY_DATE')
        city_dates[city_day] = day
        split = item['split']
        require(split in ('SYNTHETIC', 'DEVELOPMENT', 'HISTORICAL_CONFIRMATION', 'FORWARD_SHADOW') and
                (split == 'SYNTHETIC' if cls == 'SYNTHETIC' else split in ('DEVELOPMENT', 'HISTORICAL_CONFIRMATION')
                 if cls == 'HISTORICAL_CORRECTED' else split == 'FORWARD_SHADOW'), 'BRAIN_PARTITION_CLASS')
        require(city_day not in city_splits or city_splits[city_day] == split, 'BRAIN_CITY_DAY_SPLIT')
        city_splits[city_day] = split
        station_day = (station, day)
        require(station_day not in station_days or station_days[station_day] == (city_day, split),
                'BRAIN_STATION_DAY_ALIAS')
        station_days[station_day] = (city_day, split)
        require(item['family'] in spec['candidate_bundles'], 'BRAIN_FAMILY')
        require(item['source_sha256'] == spec['source_sha256'] and
                item['candidate_bundle_sha256'] == spec['candidate_bundles'][item['family']],
                'BRAIN_ROW_PROVENANCE_BINDING')
        sha(item['label_sha256'])
        decision, label = finite(item['decision_at']), finite(item['label_received_at'])
        require(decision < label <= cutoff, 'BRAIN_LABEL_LOOKAHEAD')
        received = item['source_received_at']
        if cls == 'HISTORICAL_CORRECTED':
            require(received is None, 'BRAIN_HISTORICAL_AVAILABILITY_UNKNOWN')
        else:
            require(received is not None and finite(received) <= decision, 'BRAIN_SOURCE_RECEIPT_REQUIRED')
        p = item['probabilities']
        require(type(p) is dict and set(p) == set(ARMS), 'BRAIN_ARM_COMPLETENESS')
        values = {arm: vector(p[arm]) for arm in ARMS}
        require(len({len(v) for v in values.values()}) == 1, 'BRAIN_ARM_BUCKETS')
        winner = item['winner']
        require(type(winner) is int and 0 <= winner < len(values['CANDIDATE']), 'BRAIN_WINNER')
        for arm in ARMS:
            predictions[arm].append(values[arm])
        providers = item['provider_probabilities']
        require(type(providers) is dict and set(providers) <= set(GROUPS) and
                item['provider_groups'] == {name: GROUPS[name] for name in providers}, 'BRAIN_PROVIDER_GROUPS')
        if set(providers) == set(GROUPS):
            require(all(len(vector(v)) == len(values['CANDIDATE']) for v in providers.values()), 'BRAIN_PROVIDER_BUCKETS')
            # Frozen demonstration weights only. No fit, fallback, or promotion.
            multi.append(pool(providers, .5, .5))
        else:
            for v in providers.values():
                require(len(vector(v)) == len(values['CANDIDATE']), 'BRAIN_PROVIDER_BUCKETS')
            multi.append(None)
        rows.append(ScoreRow(event_id, city_day, station, day, winner))
    require(seen == set(spec['event_ids']), 'BRAIN_FROZEN_EVENT_ROSTER_MISMATCH')
    order = sorted(range(len(rows)), key=lambda i: rows[i].event_id)
    return (cls, [rows[i] for i in order], {a: [v[i] for i in order] for a, v in predictions.items()},
            [multi[i] for i in order], [observations[i] for i in order])


def _execution(cost, markout, spec, event_bundles):
    result = dict(status='UNKNOWN_PENDING_PAPER_SHADOW', cost_report_sha256=None, markout_report_sha256=None,
                  known_costs=None, price_shortfall_vs_signal=None, mean_fill_markout_per_share=None,
                  missed_fill_rate=None, slippage=None, paper_pnl=None, drawdown=None)
    bundles = set(spec['candidate_bundles'].values())
    if cost is not None:
        require(type(cost) is dict and cost.get('selection') == COST_SELECTION and
                cost.get('execution_namespace') == 'V11_PAPER' and cost.get('financial_authority') is False and
                cost.get('execution_class') == 'SYNTHETIC_PAPER_FILL' and type(cost.get('rows')) is list,
                'BRAIN_COST_REPORT_TYPE')
        for r in cost['rows']:
            require(r['event_id'] in event_bundles and
                    r['model_bundle_sha256'] == event_bundles[r['event_id']], 'BRAIN_COST_SCOPE')
        result['cost_report_sha256'] = digest(cost)
        if cost.get('status') == 'COMPLETE_SYNTHETIC_COSTS_AND_COMPARISONS' and cost.get('rows') and cost.get('complete_cost_population') is True and cost.get('complete_price_comparisons') is True:
            # Retain typed source strings; never fill missing components with zero.
            require(cost.get('selected_fill_count') == len(cost['rows']) and
                    cost.get('known_cost_count') == len(cost['rows']) and
                    all(r.get('cost_status') == 'VALIDATED_SYNTHETIC_DETAILS' and
                        r.get('signal', {}).get('status') == 'MATCHED_VISIBLE_DEPTH' and
                        r.get('post_validation', {}).get('status') == 'MATCHED_VISIBLE_DEPTH' and
                        _decimal(r.get('total_cost_vs_signal')) is not None and
                        _decimal(r.get('price_shortfall_vs_signal')) is not None
                        for r in cost['rows']), 'BRAIN_COST_COMPLETENESS')
            result['known_costs'] = [r['total_cost_vs_signal'] for r in cost['rows']]
            result['price_shortfall_vs_signal'] = [r['price_shortfall_vs_signal'] for r in cost['rows']]
            result['status'] = 'PAPER_COST_DIAGNOSTICS_ONLY'
    if markout is not None:
        require(type(markout) is dict and markout.get('selection') == MARKOUT_SELECTION and
                markout.get('execution_class') == 'SYNTHETIC_PAPER_FILL' and
                markout.get('financial_authority') is False and type(markout.get('rows')) is list and
                type(markout.get('request')) is dict and
                markout['request'].get('namespace') == 'V11_PAPER' and
                markout['request'].get('bundle_sha256') in bundles,
                'BRAIN_MARKOUT_REPORT_TYPE')
        for r in markout['rows']:
            require(r['event_id'] in event_bundles and
                    markout['request']['bundle_sha256'] == event_bundles[r['event_id']],
                    'BRAIN_MARKOUT_SCOPE')
        result['markout_report_sha256'] = digest(markout)
        if markout.get('outcome') in ('NO_DECLARED_BREACH', 'DEGRADATION_CANDIDATE') and markout.get('cohort_sufficient') is True and markout['rows'] and all(r.get('status') == 'MEASURED' for r in markout['rows']) and markout.get('scores', {}).get('n_fills') == len(markout['rows']) and _decimal(markout['scores'].get('mean_fill_markout_per_share')) is not None:
            result['mean_fill_markout_per_share'] = markout['scores']['mean_fill_markout_per_share']
    return result


def _decimal(value):
    if type(value) is not str:
        return None
    try:
        result = Decimal(value)
    except InvalidOperation:
        return None
    return result if result.is_finite() else None


def evaluate(spec, observations, *, cost_report=None, markout_report=None):
    """Score an exact frozen roster; never select parameters or certify evidence."""
    cls, rows, predictions, multi, ordered = _score_rows(spec, observations)
    scores = {arm: metrics(rows, predictions[arm]) for arm in ARMS}
    comparisons = {reference: paired_date_bootstrap(rows, predictions['CANDIDATE'], predictions[reference])
                   for reference in ('BASELINE', 'CHAMPION')}
    missing = sum(p is None for p in multi)
    result = dict(version=VERSION, evidence_class=cls, cohort_sha256=digest(spec),
        observation_sha256=digest(ordered), candidate_manifest_sha256=MANIFEST_SHA256,
        candidate_bundles=spec['candidate_bundles'], scores=scores,
        candidate_comparisons=comparisons,
        multimodel=dict(status='SYNTHETIC_INTERFACE_ONLY' if cls == 'SYNTHETIC' and not missing else
                        'INCOMPLETE_PROVIDER_COVERAGE' if missing else 'GATE_3_4_ADMISSION_REQUIRED',
                        fixed_group_weights={'NOAA_GEFS': .5, 'ECMWF_LINEAGE': .5},
                        fixed_ecmwf_provider_weights={'IFS': .5, 'AIFS': .5}, missing_provider_events=missing,
                        scores=metrics(rows, multi) if cls == 'SYNTHETIC' and not missing else None),
        execution=_execution(cost_report, markout_report, spec,
                             {r.event_id: item['candidate_bundle_sha256']
                              for r, item in zip(rows, ordered)}),
        machinery_status='MECHANISM_VALIDATED', calibration_status='CALIBRATION_EVIDENCE_PENDING_FORWARD_DATA',
        training_status='FITTED_NOT_CALIBRATED', promotion_status='NO_PROMOTION',
        historical_confirmation_is_forward_holdout=False, independent_acceptance=False,
        financial_authority=False, order_authority=False, promotion_authority=False)
    result['report_sha256'] = digest(result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description='Offline Brain diagnostics; never calibration acceptance')
    parser.add_argument('input', type=Path, help='Local JSON object: spec, observations, optional cost_report/markout_report')
    args = parser.parse_args(argv)
    require(args.input.is_file() and args.input.stat().st_size <= 1024*1024, 'BRAIN_INPUT_BYTES_BOUND')
    data = parse_data(args.input.read_bytes(), max_bytes=1024*1024)
    require(set(data) == {'spec', 'observations'} or
            set(data) == {'spec', 'observations', 'cost_report', 'markout_report'}, 'BRAIN_INPUT_SCHEMA')
    print(canonical(evaluate(data['spec'], data['observations'], cost_report=data.get('cost_report'),
                             markout_report=data.get('markout_report'))))


if __name__ == '__main__':
    main()
