"""Lot-level entry/exit lineage; one economic P&L partition, never double credit."""
from copy import deepcopy
from decimal import Decimal, ROUND_FLOOR

from .evidence import EvidenceError
from .scenario_risk import number, precise


def intent_lineage(store, intent):
    value = store.get(intent['valuation_id'])['body']['details']
    buy = intent['direction'] == 'BUY'
    joint = intent.get('joint_ev_only', False)
    ev = value.get('conservative_full_fill_ev_total') if joint else value.get(
            'conservative_ev_total' if buy else 'sale_advantage_per_share')
    return dict(intent_id=intent['proposal_id'], valuation_id=intent['valuation_id'],
                event_state_id=intent['event_state_id'], thesis_id=intent['thesis_id'],
                reason=value.get('thesis_reason') or value.get('reasons') or 'RECORDED_THESIS',
                ev=ev, ev_unit='JOINT_BASKET_TOTAL' if joint else 'TOTAL' if buy else 'PER_SHARE',
                admitted_ev_total=intent.get('conservative_ev_total'),
                joint_ev_is_not_individual_leg_alpha=joint, binding=deepcopy(intent['binding']),
                attribution=deepcopy(intent['attribution']), financial_authority=False)


@precise
def consume_lots(lots, *, token_id, quantity, net_proceeds):
    """FIFO allocation with exact residual conservation of basis and proceeds.

    Mutates the caller's private account-state copy only. The account CAS is the
    sole publisher. Old lots without lineage stay explicitly unknown.
    """
    todo, basis, proceeds_left = quantity, Decimal(0), net_proceeds
    pieces = []
    # Canonical JSON sorts object keys: dictionary order is NOT acquisition order.
    # Preserve old lots explicitly as an unknown-order cohort ahead of new lots.
    ordered = sorted(lots.items(), key=lambda item:(item[1].get('acquired_sequence', -1), item[0]))
    for lot_id, lot in ordered:
        if lot['token_id'] != token_id or todo == 0:
            continue
        units, cost = number(lot['units']), number(lot['all_in_cost_basis'])
        take = min(todo, units)
        allocated = cost if take == units else (cost*take/units).quantize(Decimal('1e-18'), rounding=ROUND_FLOOR)
        proceeds = proceeds_left if take == todo else (net_proceeds*take/quantity).quantize(Decimal('1e-18'), rounding=ROUND_FLOOR)
        pnl = proceeds-allocated
        weights = lot['attribution']; remainder = pnl; split = []
        for index, weight in enumerate(weights):
            part = remainder if index == len(weights)-1 else (pnl*number(weight['weight'])).quantize(Decimal('1e-18'), rounding=ROUND_FLOOR)
            remainder -= part
            split.append(dict(strategy=weight['strategy'], pnl=str(part)))
        pieces.append(dict(lot_id=lot_id, units=str(take), allocated_basis=str(allocated),
                           net_proceeds=str(proceeds), realized_pnl=str(pnl),
                           entry=deepcopy(lot.get('entry')), entry_lineage_status='RECORDED' if 'entry' in lot else 'LEGACY_UNKNOWN',
                           acquisition_order='RECEIPT_SEQUENCE_FIFO' if 'acquired_sequence' in lot else 'LEGACY_ORDER_UNKNOWN',
                           strategy_realized_pnl=split))
        basis += allocated; todo -= take; proceeds_left -= proceeds
        if take == units:
            del lots[lot_id]
        else:
            lot.update(units=str(units-take), all_in_cost_basis=str(cost-allocated))
    if todo != 0 or proceeds_left != 0:
        raise EvidenceError('SALE_ALLOCATION_INVENTORY_MISMATCH')
    return basis, pieces
