"""Independent exact-commit repair probes; synthetic diagnostics only."""
from copy import deepcopy
import pytest
from tools import v11_brain_readiness as brain
from polymarket_scanner.v11.evidence import EvidenceError, digest

HIGH, LOW = 'daily_high_temperature', 'daily_low_temperature'


def population(unit='C', historical=False):
    bundles = {f: brain.CANDIDATES[f, unit] for f in (HIGH, LOW)}
    spec = dict(evidence_class='HISTORICAL_CORRECTED' if historical else 'SYNTHETIC',
                unit=unit, candidate_manifest_sha256=brain.MANIFEST_SHA256,
                candidate_bundles=bundles, event_ids=['a-high', 'z-low'],
                source_sha256=brain.DATASET_SHA256 if historical else '1'*64,
                evaluation_cutoff=100.)
    rows = []
    for event, family, winner in [('a-high', HIGH, 0), ('z-low', LOW, 1)]:
        rows.append(dict(event_id=event, city_day='city-one:2026-09-01', station='TEST1',
            local_date='2026-09-01', family=family,
            split='DEVELOPMENT' if historical else 'SYNTHETIC', winner=winner,
            decision_at=10., source_received_at=None if historical else 9., label_received_at=20.,
            source_sha256=spec['source_sha256'], label_sha256='2'*64,
            candidate_bundle_sha256=bundles[family],
            probabilities={a: [.7, .3] for a in brain.ARMS},
            provider_probabilities={}, provider_groups={}))
    return spec, rows


def cost(event, bundle):
    return dict(selection=brain.COST_SELECTION, execution_namespace='V11_PAPER',
        financial_authority=False, execution_class='SYNTHETIC_PAPER_FILL',
        status='COMPLETE_SYNTHETIC_COSTS_AND_COMPARISONS', complete_cost_population=True,
        complete_price_comparisons=True, selected_fill_count=1, known_cost_count=1,
        rows=[dict(event_id=event, model_bundle_sha256=bundle,
            cost_status='VALIDATED_SYNTHETIC_DETAILS', signal={'status':'MATCHED_VISIBLE_DEPTH'},
            post_validation={'status':'MATCHED_VISIBLE_DEPTH'}, total_cost_vs_signal='0.07',
            price_shortfall_vs_signal='0.04')])


def markout(event, bundle):
    return dict(selection=brain.MARKOUT_SELECTION, execution_class='SYNTHETIC_PAPER_FILL',
        financial_authority=False, request={'namespace':'V11_PAPER', 'bundle_sha256':bundle},
        outcome='NO_DECLARED_BREACH', cohort_sufficient=True,
        rows=[{'event_id':event, 'status':'MEASURED'}],
        scores={'n_fills':1, 'mean_fill_markout_per_share':'-0.015'})


@pytest.mark.parametrize('unit', ['C', 'F'])
@pytest.mark.parametrize('reverse', [False, True])
def test_alias_and_valid_shared_station_day(unit, reverse):
    spec, rows = population(unit, historical=True)
    if reverse: rows.reverse()
    control = brain.evaluate(spec, rows)
    assert control['scores']['CANDIDATE']['city_days'] == 1
    for split in ('DEVELOPMENT', 'HISTORICAL_CONFIRMATION'):
        bad = deepcopy(rows)
        bad[1].update(city_day='alias:2026-09-01', split=split)
        with pytest.raises(EvidenceError, match='BRAIN_STATION_DAY_ALIAS'):
            brain.evaluate(spec, bad)
    bad = deepcopy(rows)
    bad[1]['split'] = 'HISTORICAL_CONFIRMATION'
    with pytest.raises(EvidenceError, match='BRAIN_CITY_DAY_SPLIT'):
        brain.evaluate(spec, bad)
    # Distinct actual station/day identities still permit separate cohorts.
    rows[1].update(station='TEST2', city_day='city-two:2026-09-01', split='HISTORICAL_CONFIRMATION')
    assert brain.evaluate(spec, rows)['scores']['CANDIDATE']['city_days'] == 2


@pytest.mark.parametrize('unit', ['C', 'F'])
@pytest.mark.parametrize('reverse', [False, True])
@pytest.mark.parametrize('event,family,other', [('a-high', HIGH, LOW), ('z-low', LOW, HIGH)])
def test_exact_event_binding(unit, reverse, event, family, other):
    spec, rows = population(unit)
    if reverse: rows.reverse()
    for kind, maker, error in [('cost_report', cost, 'BRAIN_COST_SCOPE'),
                               ('markout_report', markout, 'BRAIN_MARKOUT_SCOPE')]:
        good = maker(event, brain.CANDIDATES[family, unit])
        report = brain.evaluate(spec, rows, **{kind: good})
        ex = report['execution']
        if kind == 'cost_report':
            assert ex['known_costs'] == ['0.07'] and ex['cost_report_sha256'] == digest(good)
        else:
            assert ex['mean_fill_markout_per_share'] == '-0.015' and ex['markout_report_sha256'] == digest(good)
        for bad_event, bad_family in [(event, other), ('absent-event', family)]:
            with pytest.raises(EvidenceError, match=error):
                brain.evaluate(spec, rows, **{kind: maker(bad_event, brain.CANDIDATES[bad_family, unit])})
        assert report['financial_authority'] is False and report['promotion_authority'] is False
        assert report['independent_acceptance'] is False
        assert ex['paper_pnl'] is None and ex['drawdown'] is None


@pytest.mark.parametrize('namespace', ['V11_LIVE', '', None, False, 'v11_paper', 'V11_PAPER '])
def test_namespace_closed(namespace):
    spec, rows = population()
    report = markout('a-high', brain.CANDIDATES[HIGH, 'C'])
    report['request']['namespace'] = namespace
    with pytest.raises(EvidenceError, match='BRAIN_MARKOUT_REPORT_TYPE'):
        brain.evaluate(spec, rows, markout_report=report)


@pytest.mark.parametrize('request_value', [None, [], {}, {'bundle_sha256': brain.CANDIDATES[HIGH, 'C']}])
def test_request_shape_or_missing_namespace(request_value):
    spec, rows = population()
    report = markout('a-high', brain.CANDIDATES[HIGH, 'C'])
    report['request'] = request_value
    with pytest.raises(EvidenceError, match='BRAIN_MARKOUT_REPORT_TYPE'):
        brain.evaluate(spec, rows, markout_report=report)


def test_markout_single_bundle_cannot_mix_event_families():
    spec, rows = population()
    report = markout('a-high', brain.CANDIDATES[HIGH, 'C'])
    report['rows'].append({'event_id':'z-low', 'status':'MEASURED'})
    report['scores']['n_fills'] = 2
    with pytest.raises(EvidenceError, match='BRAIN_MARKOUT_SCOPE'):
        brain.evaluate(spec, list(reversed(rows)), markout_report=report)
