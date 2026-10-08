import pytest

from polymarket_scanner.v11.evidence import EvidenceError
from polymarket_scanner.v11.valuation import ENTRY_RISKS, PAYOUT, CostComponent, buy_fee_cost

from tools.v11_paper_cost_metric_manifest import (
    EVENT_METRIC_FIELDS, cost_manifest, metric_manifest, prerequisite_manifest,
)


def test_metric_manifest_covers_all_three_fields_with_nonnull_unknown_reason():
    rows = {row['field']: row for row in metric_manifest()}
    assert set(rows) == set(EVENT_METRIC_FIELDS)
    for row in rows.values():
        assert row['status'] == 'UNKNOWN'
        assert row['reason']
        assert row['producer_reference']


def test_cost_manifest_marks_all_seven_missing_with_no_producer_registered():
    manifest = cost_manifest()
    names = {c['name'] for c in manifest['categories']}
    assert names == set(ENTRY_RISKS)
    for c in manifest['categories']:
        if c['name'] == 'ACQUISITION_FEES':
            assert c['producer_in_tree'] is True
            assert c['producer_reference']
        else:
            assert c['producer_in_tree'] is False
            assert c['producer_reference'] is None
        assert c['status'] == 'MISSING'
    assert set(manifest['cost_summary']['missing']) == set(ENTRY_RISKS)
    assert manifest['cost_summary']['complete'] is False
    assert manifest['source_backed_complete'] is False


def _genuine_buy_fee_cost(*, token_id: str = 't'*40, condition_id: str = 'c'*40):
    from polymarket_scanner.production.fees import EXCHANGE_PUBLISHED_SCHEDULE, make_fee_evidence
    target = dict(token_id=token_id, condition_id=condition_id)
    proof = make_fee_evidence(EXCHANGE_PUBLISHED_SCHEDULE, token=target['token_id'], condition=target['condition_id'],
                             exchange='fixture', observed_at=100., fd={'r': '.05', 'e': '1', 'to': True},
                             max_fee_bps=0, max_fee_block={'number': 100, 'hash': 'fixture'},
                             maker_base_fee_bps=0, taker_base_fee_bps=0)
    snapshot = dict(fee_policy=EXCHANGE_PUBLISHED_SCHEDULE, fee_evidence=proof, token=target['token_id'],
                    condition=target['condition_id'], exchange='fixture', max_fee_bps=0, received_at=100.)
    return buy_fee_cost(snapshot, target=target, as_of=100., max_age_seconds=10.,
                        limit_price='.4', post_only=False)


def test_registering_genuine_acquisition_fee_cost_flips_only_that_one_category_to_declared_unverified():
    fee = _genuine_buy_fee_cost()
    manifest = cost_manifest((fee,))
    by_name = {c['name']: c for c in manifest['categories']}
    # Even a real `buy_fee_cost` output never reaches `KNOWN`: the manifest has no way to
    # distinguish this genuine component from a caller-forged one carrying the same shape
    # (see module docstring), so registry membership alone stays confined to DECLARED_UNVERIFIED.
    assert by_name['ACQUISITION_FEES']['status'] == 'DECLARED_UNVERIFIED'
    for name in ENTRY_RISKS - {'ACQUISITION_FEES'}:
        assert by_name[name]['status'] == 'MISSING'
    assert manifest['cost_summary']['missing'] == sorted(ENTRY_RISKS - {'ACQUISITION_FEES'})
    assert manifest['source_backed_complete'] is False


def test_directly_constructed_zero_fee_component_stays_declared_unverified_not_known():
    forged = CostComponent('CALLER_FORGED_NOT_BUY_FEE_COST', PAYOUT, '0', ('ACQUISITION_FEES',), 'a'*64)
    manifest = cost_manifest((forged,))
    by_name = {c['name']: c for c in manifest['categories']}
    assert by_name['ACQUISITION_FEES']['status'] == 'DECLARED_UNVERIFIED'
    assert manifest['source_backed_complete'] is False


def test_directly_constructed_expired_fee_shaped_component_stays_declared_unverified_not_known():
    forged = CostComponent('CALLER_FORGED_NOT_BUY_FEE_COST', PAYOUT, '.01', ('ACQUISITION_FEES',), 'b'*64,
                           priced_buy_limit='.4', post_only=False, valid_until=0.0)
    manifest = cost_manifest((forged,))
    by_name = {c['name']: c for c in manifest['categories']}
    assert by_name['ACQUISITION_FEES']['status'] == 'DECLARED_UNVERIFIED'
    assert manifest['source_backed_complete'] is False


def test_genuine_fee_component_bound_to_one_target_is_not_distinguished_from_wrong_target_reuse():
    # The manifest never receives any target at all, so a genuine, correctly-targeted
    # `buy_fee_cost` output cannot be told apart from the same component replayed against an
    # unrelated target -- both must stay DECLARED_UNVERIFIED, never KNOWN.
    fee_for_one_target = _genuine_buy_fee_cost(token_id='x'*40, condition_id='y'*40)
    manifest = cost_manifest((fee_for_one_target,))
    by_name = {c['name']: c for c in manifest['categories']}
    assert by_name['ACQUISITION_FEES']['status'] == 'DECLARED_UNVERIFIED'
    assert manifest['source_backed_complete'] is False


def test_fee_pricing_scope_mismatch_between_covers_and_priced_buy_limit_is_rejected():
    with pytest.raises(EvidenceError, match='FEE_PRICING_SCOPE_INVALID'):
        CostComponent('bogus_scope', PAYOUT, '.01', ('ACQUISITION_FEES', 'SETTLEMENT_REVISION'), 'c'*64,
                     priced_buy_limit='.4', post_only=False, valid_until=200.)


def test_non_producer_named_component_for_acquisition_fees_stays_declared_unverified_not_known():
    forged = CostComponent('NOT_A_REAL_PRODUCER', PAYOUT, '.005', ('ACQUISITION_FEES',), 'd'*64)
    manifest = cost_manifest((forged,))
    by_name = {c['name']: c for c in manifest['categories']}
    assert by_name['ACQUISITION_FEES']['status'] == 'DECLARED_UNVERIFIED'
    assert manifest['source_backed_complete'] is False


def test_single_component_covering_two_categories_stays_declared_unverified_not_known():
    bundled = CostComponent('bundled', PAYOUT, '0', ('ACQUISITION_FEES', 'SETTLEMENT_REVISION'), 'c'*64)
    manifest = cost_manifest((bundled,))
    by_name = {c['name']: c for c in manifest['categories']}
    assert by_name['ACQUISITION_FEES']['status'] == 'DECLARED_UNVERIFIED'
    assert by_name['SETTLEMENT_REVISION']['status'] == 'DECLARED_UNVERIFIED'
    assert manifest['cost_summary']['missing'] == sorted(ENTRY_RISKS - {'ACQUISITION_FEES', 'SETTLEMENT_REVISION'})
    assert manifest['source_backed_complete'] is False


def test_single_zero_component_covering_all_seven_categories_is_declared_complete_not_source_complete():
    all_seven = CostComponent('all', PAYOUT, '0', tuple(sorted(ENTRY_RISKS)), 'a'*64)
    manifest = cost_manifest((all_seven,))
    assert {c['status'] for c in manifest['categories']} == {'DECLARED_UNVERIFIED'}
    assert manifest['cost_summary']['missing'] == []
    assert manifest['cost_summary']['unknown'] == []
    assert manifest['cost_summary']['known_total_per_share'] == '0'
    # `_costs` own declared-coverage field stays True -- the zero-value component covers
    # every required category -- but that is not this manifest's claim of source-backed knowledge.
    assert manifest['cost_summary']['complete'] is True
    assert manifest['source_backed_complete'] is False
    for category in manifest['categories']:
        if category['name'] != 'ACQUISITION_FEES':
            assert category['producer_in_tree'] is False


def test_duplicate_or_overlapping_cost_coverage_is_rejected_not_silently_merged():
    dup = CostComponent('one', PAYOUT, '.01', ('ACQUISITION_FEES',), 'd'*64)
    other = CostComponent('two', PAYOUT, '.02', ('ACQUISITION_FEES',), 'e'*64)
    with pytest.raises(EvidenceError):
        cost_manifest((dup, other))


def test_manifest_never_sets_admission_authority_or_demonstrated_outcome():
    manifest = prerequisite_manifest()
    assert manifest['financial_authority'] is False
    assert manifest['admission_eligible'] is False
    assert 'DEMONSTRATED' not in manifest['promotion_status']


def test_forged_entry_risks_set_with_too_few_categories_raises():
    forged = frozenset(ENTRY_RISKS)
    forged = frozenset(list(forged)[:-1])
    assert len(forged) == len(ENTRY_RISKS) - 1
    with pytest.raises(EvidenceError, match='ENTRY_RISKS_SET_MISMATCH'):
        cost_manifest(required=forged)


def test_forged_entry_risks_set_with_extra_category_raises():
    forged = frozenset(ENTRY_RISKS) | {'MADE_UP_CATEGORY'}
    with pytest.raises(EvidenceError, match='ENTRY_RISKS_SET_MISMATCH'):
        cost_manifest(required=forged)


def test_mismatched_category_in_cost_component_is_rejected_by_reused_costs_logic():
    bogus = CostComponent('bogus', PAYOUT, '.01', ('MODEL_UNCERTAINTY',), 'f'*64)
    with pytest.raises(EvidenceError):
        cost_manifest((bogus,))
