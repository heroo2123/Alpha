"""Pure, bounded synthetic PAPER risk diagnostics from an archived read frontier.

This module has no store, clock, network, account writer, or EventMetrics adapter.
Its result is never an admission input. A caller must supply every archive row from
sequence 1 through the pinned tip; gaps and oversized cuts stay UNKNOWN.
"""
from dataclasses import asdict, dataclass
from decimal import Context, Decimal, DecimalException, ROUND_HALF_EVEN, localcontext
import math

from .evidence import EvidenceError, canonical, digest, finite, identity, sha
from .fill_evidence import execution_details
from .microstructure import STREAM_VERSION
from .paper_coordinator import ACCOUNT_KEY, VERSION as ACCOUNT_VERSION
from .rules import GUARD_VERSION, fingerprint_event
from .scenario_risk import number


VERSION = 'alpha_v11_paper_risk_observation_v1'
MAX_ROWS = 4096
MAX_RECORD_BYTES = 8 * 1024 * 1024
MAX_ARCHIVE_BYTES = 32 * 1024 * 1024
MAX_RECORD_NODES = 20_000
MAX_ARCHIVE_NODES = 100_000
NUMERIC_CONTEXT = Context(prec=160, rounding=ROUND_HALF_EVEN, Emin=-999999, Emax=999999)


def _bounded_json(value):
    """Count a conservative ASCII JSON upper bound before canonical allocates it."""
    pending = [(value, 0)]
    size = nodes = 0
    while pending:
        item, depth = pending.pop()
        nodes += 1
        if depth > 24 or nodes > MAX_RECORD_NODES:
            raise EvidenceError('OBSERVATION_RECORD_BOUND')
        if type(item) is dict:
            if len(item) > MAX_RECORD_NODES - nodes:
                raise EvidenceError('OBSERVATION_RECORD_BOUND')
            size += 2 + 2 * len(item)
            for key, child in item.items():
                if type(key) is not str:
                    raise EvidenceError('OBSERVATION_JSON_SHAPE')
                pending.append((key, depth + 1))
                pending.append((child, depth + 1))
        elif type(item) is list:
            if len(item) > MAX_RECORD_NODES - nodes:
                raise EvidenceError('OBSERVATION_RECORD_BOUND')
            size += 2 + len(item)
            pending.extend((child, depth + 1) for child in item)
        elif type(item) is str:
            # JSON's worst single Unicode code point is two escaped surrogates.
            size += 2 + 12 * len(item)
        elif type(item) is int:
            if item.bit_length() > 256:
                raise EvidenceError('OBSERVATION_RECORD_BOUND')
            size += 80
        elif type(item) is float:
            if not math.isfinite(item):
                raise EvidenceError('OBSERVATION_JSON_SHAPE')
            size += 32
        elif item is None or type(item) is bool:
            size += 5
        else:
            raise EvidenceError('OBSERVATION_JSON_SHAPE')
        if size > MAX_RECORD_BYTES:
            raise EvidenceError('OBSERVATION_RECORD_BOUND')
    return size, nodes


def _mapping(value, reason='OBSERVATION_MALFORMED_INPUT'):
    if type(value) is not dict:
        raise EvidenceError(reason)
    return value


def _deadline(start, interval, reason='OBSERVATION_TIME_UNREPRESENTABLE'):
    end = start + interval
    if not math.isfinite(end) or end <= start:
        raise EvidenceError(reason)
    return end


@dataclass(frozen=True)
class ObservationPolicy:
    version: str
    policy_sha256: str
    lookback_seconds: float | None
    horizon_seconds: float | None
    maximum_horizon_delay_seconds: float | None
    maximum_book_receipt_delay_seconds: float | None
    maximum_measurement_age_seconds: float | None
    complete_history_scan_bound: int | None
    book_provider: str | None
    book_source_convention: str | None

    def reason(self):
        try:
            identity(self.version)
            sha(self.policy_sha256)
            values = (self.lookback_seconds, self.horizon_seconds,
                      self.maximum_horizon_delay_seconds,
                      self.maximum_book_receipt_delay_seconds,
                      self.maximum_measurement_age_seconds)
            if any(v is None for v in values) or self.complete_history_scan_bound is None or not self.book_provider or not self.book_source_convention:
                return 'OBSERVATION_POLICY_INCOMPLETE'
            w, h, j, r, a = (finite(v) for v in values)
            if not w > h > 0 or j < 0 or r <= 0 or a <= 0:
                return 'OBSERVATION_POLICY_INTERVAL_INVALID'
            if type(self.complete_history_scan_bound) is not int or not 1 <= self.complete_history_scan_bound <= MAX_ROWS:
                return 'OBSERVATION_POLICY_SCAN_BOUND'
            identity(self.book_provider)
            if self.book_source_convention != 'TOKEN_ID':
                return 'OBSERVATION_BOOK_SOURCE_CONVENTION_UNKNOWN'
            parameters = asdict(self)
            parameters.pop('policy_sha256')
            if digest(parameters) != self.policy_sha256:
                return 'OBSERVATION_POLICY_HASH_MISMATCH'
        except EvidenceError:
            return 'OBSERVATION_POLICY_MALFORMED'
        return None


class _Archive:
    namespace = 'V11_PAPER'

    def __init__(self, rows):
        self.rows = rows
        self.by_id = {r['id']: r for r in rows}

    def get(self, key):
        try:
            return self.by_id[key]
        except (KeyError, TypeError):
            raise EvidenceError('OBSERVATION_REFERENCE_MISSING') from None


def _result(reason, *, at, policy, tip_sha256, account_id, event_id, rule_fingerprint,
            collateral_asset,
            fill_count=None, adverse=None, markout=None, evidence=(), valid_until=None,
            frontier_sha256=None):
    status = ('OBSERVED_SYNTHETIC_DIAGNOSTIC' if reason is None else
              'NO_RECONCILED_RECENT_PAPER_FILLS' if reason == 'NO_RECONCILED_RECENT_PAPER_FILLS' else 'UNKNOWN')
    try:
        config_sha = digest(asdict(policy))
    except EvidenceError:
        config_sha = None
    return dict(version=VERSION, namespace='V11_PAPER', financial_authority=False,
                evidence_class='SYNTHETIC_PAPER_DIAGNOSTIC', admission_eligible=False,
                event_metrics_adverse_fills=None, event_metrics_recent_markout_per_share=None,
                new_risk_cutoff_at=None, seconds_to_new_risk_cutoff=None,
                settlement_finality_status='UNKNOWN', cutoff_reason='REVIEWED_CUTOFF_POLICY_UNAVAILABLE',
                execution_status=status, reason=reason, observed_at=at,
                account_id=account_id, event_id=event_id, rule_fingerprint=rule_fingerprint,
                collateral_asset=collateral_asset,
                policy_sha256=policy.policy_sha256, policy_config_sha256=config_sha,
                frontier_tip_sha256=tip_sha256, frontier_sha256=frontier_sha256, fill_count=fill_count,
                diagnostic_adverse_fill_count=adverse,
                diagnostic_markout_collateral_per_share=markout,
                evidence_ids=list(evidence), valid_until=valid_until,
                replay_sha256=digest([VERSION, config_sha, at, frontier_sha256, tip_sha256,
                                      account_id, event_id, rule_fingerprint, collateral_asset]))


def _validate_rows(rows, tip_sha256, at, bound):
    if type(rows) is not tuple or len(rows) > bound:
        raise EvidenceError('OBSERVATION_FRONTIER_BOUND')
    if not rows or type(rows[-1]) is not dict or len(rows) != rows[-1].get('seq'):
        raise EvidenceError('OBSERVATION_FRONTIER_INCOMPLETE')
    ids = set()
    total_bytes = total_nodes = 0
    for seq, row in enumerate(rows, 1):
        if type(row) is not dict:
            raise EvidenceError('OBSERVATION_FRONTIER_INCOMPLETE')
        row_bytes, row_nodes = _bounded_json(row)
        total_bytes += row_bytes
        total_nodes += row_nodes
        if total_bytes > MAX_ARCHIVE_BYTES or total_nodes > MAX_ARCHIVE_NODES:
            raise EvidenceError('OBSERVATION_ARCHIVE_BOUND')
        if row.get('seq') != seq or type(row.get('id')) is not str or row['id'] in ids:
            raise EvidenceError('OBSERVATION_FRONTIER_INCOMPLETE')
        ids.add(row['id'])
        b = row.get('body')
        if (type(b) is not dict or b.get('record_id') != row['id'] or b.get('kind') != row.get('kind')
                or b.get('event_id') != row.get('event_id') or b.get('namespace') != 'V11_PAPER'
                or b.get('financial_authority') is not False or digest(b) != row.get('sha256')
                or b.get('recorded_at') is None or b.get('available_at') is None
                or b['available_at'] > b['recorded_at']):
            raise EvidenceError('OBSERVATION_RECORD_INTEGRITY')
        if finite(b['available_at']) > at or finite(b['recorded_at']) > at:
            raise EvidenceError('OBSERVATION_FUTURE_RECEIPT')
    if rows[-1]['sha256'] != tip_sha256:
        raise EvidenceError('OBSERVATION_FRONTIER_TIP_CHANGED')


def _sequence(book):
    seq = _mapping(book['body'].get('payload')).get('book_sequence')
    if (type(seq) is not dict or set(seq) != {'version', 'epoch', 'sequence', 'previous_sequence'}
            or seq['version'] != STREAM_VERSION or not isinstance(seq['epoch'], str) or not seq['epoch']
            or type(seq['sequence']) is not int or not 0 <= seq['sequence'] <= 2**53
            or seq['previous_sequence'] is not None and
            (type(seq['previous_sequence']) is not int or not 0 <= seq['previous_sequence'] < seq['sequence'])):
        raise EvidenceError('OBSERVATION_BOOK_SEQUENCE_UNKNOWN')
    return seq


def _touch(book, proof, intent, policy, at, *, after_fill=True):
    b = book['body']; p = _mapping(b.get('payload'))
    if ((after_fill and book['seq'] <= proof['seq']) or book['event_id'] != intent['event_id']
            or b.get('evidence_class') != 'PUBLIC_OBSERVED'
            or (b.get('provider'), b.get('source_identity')) != (policy.book_provider, intent['token_id'])
            or any(p.get(k) != v for k, v in intent['target'].items())
            or p.get('rule_fingerprint') != intent['binding']['rule_fingerprint']
            or p.get('collateral_asset') != intent['collateral_asset']
            or p.get('stream_healthy') is not True):
        raise EvidenceError('OBSERVATION_BOOK_SCOPE_OR_HEALTH')
    observed = finite(b.get('observed_at')); received = finite(b.get('received_at'))
    if not observed <= received <= finite(b['available_at']) <= finite(b['recorded_at']) <= at:
        raise EvidenceError('OBSERVATION_BOOK_CHRONOLOGY')
    if received - observed > policy.maximum_book_receipt_delay_seconds:
        raise EvidenceError('OBSERVATION_BOOK_LATE_RECEIPT')
    from .measurement import executable_depth
    bids, asks = p.get('bids'), p.get('asks')
    executable_depth(bids, '1', direction='SELL', fee_per_share=None)
    executable_depth(asks, '1', direction='ACQUIRE', fee_per_share=None)
    bid = max(number(level['price']) for level in bids)
    ask = min(number(level['price']) for level in asks)
    if not Decimal(0) <= bid < ask <= Decimal(1):
        raise EvidenceError('OBSERVATION_BOOK_CROSSED')
    return bid, ask


def _rule_lineage(archive, rule_heads, event_id, fingerprint):
    if any(_mapping(r['body'].get('details'), 'OBSERVATION_RULE_LINEAGE_UNKNOWN').get('quarantined') is not False
           or r['body']['details'].get('state') != 'SEMANTICS_OBSERVED' for r in rule_heads):
        raise EvidenceError('OBSERVATION_RULE_LINEAGE_UNKNOWN')
    rule = rule_heads[-1]
    rd = _mapping(rule['body'].get('details'))
    preimage = _mapping(rd.get('preimage'))
    refs = rule['body'].get('evidence')
    if (rd.get('version') != GUARD_VERSION or rd.get('fingerprint') != fingerprint
            or preimage.get('event_id') != event_id or digest(preimage) != fingerprint
            or type(refs) is not list or len(refs) != 1 or type(refs[0]) is not dict
            or rd.get('changed') is not False or rd.get('automatic_recertification') is not False):
        raise EvidenceError('OBSERVATION_RULE_LINEAGE_UNKNOWN')
    raw = archive.get(refs[0].get('id'))
    rb = raw['body']; payload = _mapping(rb.get('payload'))
    event = _mapping(payload.get('event'))
    if (raw['kind'] != 'RULES' or raw['event_id'] != event_id or raw['seq'] >= rule['seq']
            or rb.get('evidence_class') not in {'PUBLIC_OBSERVED', 'SYNTHETIC'}
            or raw['sha256'] != refs[0].get('sha256')
            or rd.get('source_event_sha256') != digest(event)
            or rb.get('received_at') != rd.get('source_received_at')
            or finite(rb.get('received_at')) > finite(rb['available_at'])
            or finite(rd.get('source_received_at')) > finite(rule['body']['recorded_at'])):
        raise EvidenceError('OBSERVATION_RULE_LINEAGE_UNKNOWN')
    origin = raw
    if any(k in payload for k in ('discovery_page_id', 'discovery_page_sha256', 'page_index')):
        if not all(k in payload for k in ('discovery_page_id', 'discovery_page_sha256', 'page_index')):
            raise EvidenceError('OBSERVATION_RULE_LINEAGE_UNKNOWN')
        origin = archive.get(payload['discovery_page_id'])
        ob = origin['body']; op = _mapping(ob.get('payload'))
        response = _mapping(op.get('response'))
        events = response.get('events'); index = payload['page_index']
        if (origin['kind'] != 'RULES' or origin['event_id'] != 'v11-discovery-catalog'
                or origin['sha256'] != payload['discovery_page_sha256']
                or type(events) is not list or type(index) is not int or not 0 <= index < len(events)
                or events[index] != event or origin['seq'] >= raw['seq']
                or ob.get('received_at') != rb.get('received_at')
                or ob.get('evidence_class') != rb.get('evidence_class')):
            raise EvidenceError('OBSERVATION_RULE_LINEAGE_UNKNOWN')
    if (rd.get('source_receipt_seq') != origin['seq']
            or fingerprint_event(event, station_timezone=preimage['timezone'],
                                 metadata_fingerprint=preimage['metadata_fingerprint']).sha256 != fingerprint):
        raise EvidenceError('OBSERVATION_RULE_PREIMAGE_MISMATCH')
    return rule, raw, preimage


def _account_lineage(archive, account_heads, account, proofs, rule, raw, account_id, event_id, fingerprint):
    state = _mapping(_mapping(account['body'].get('details')).get('state'))
    policy_hash = account['body']['details'].get('policy_sha256')
    if (account['body']['details'].get('version') != ACCOUNT_VERSION
            or not isinstance(policy_hash, str) or len(policy_hash) != 64
            or state.get('account_id') != account_id or state.get('execution_namespace') != 'V11_PAPER'
            or state.get('financial_authority') is not False):
        raise EvidenceError('OBSERVATION_ACCOUNT_SCOPE')
    sha(policy_hash)
    for index, head in enumerate(account_heads):
        details = _mapping(head['body'].get('details'))
        prior_state = _mapping(details.get('state'))
        if (details.get('version') != ACCOUNT_VERSION or details.get('policy_sha256') != policy_hash
                or prior_state.get('account_id') != account_id
                or prior_state.get('execution_namespace') != 'V11_PAPER'
                or prior_state.get('financial_authority') is not False):
            raise EvidenceError('OBSERVATION_ACCOUNT_LINEAGE_UNKNOWN')
        if index and details.get('request', {}).get('action') == 'FILL':
            effects = _mapping(details.get('effect_inputs'))
            before = account_heads[index - 1]
            if (effects.get('before_ref') != {'id': before['id'], 'seq': before['seq'], 'sha256': before['sha256']}
                    or effects.get('before_state_sha256') != digest(before['body']['details']['state'])
                    or effects.get('request_sha256') != digest(details['request'])):
                raise EvidenceError('OBSERVATION_RECONCILIATION_LINEAGE_UNKNOWN')
    rules = _mapping(state.get('rules'))
    stored = _mapping(rules.get(event_id))
    if (stored.get('sha256') != fingerprint or stored.get('canonical_json') != canonical(rule['body']['details']['preimage'])
            or stored.get('source_event_sha256') != digest(raw['body']['payload']['event'])):
        raise EvidenceError('OBSERVATION_ACCOUNT_RULE_MISMATCH')
    for proof in proofs.values():
        matches = [head for head in account_heads if proof['seq'] < head['seq'] <= account['seq']
                   and type(head['body'].get('details')) is dict
                   and head['body']['details'].get('request') == {'action': 'FILL', 'evidence_id': proof['id']}
                   and type(head['body']['details'].get('state')) is dict
                   and head['body']['details']['state'].get('fills', {}).get(proof['body']['payload']['fill_id']) == proof['sha256']]
        if len(matches) != 1:
            raise EvidenceError('OBSERVATION_RECONCILIATION_LINEAGE_UNKNOWN')
    return state


def _observe(rows, *, tip_sha256, at, policy, account_id, event_id, rule_fingerprint, collateral_asset):
    """Observe a complete pinned archive prefix. Returns only nonfinancial diagnostics.

    `rows` must be the entire contiguous prefix through the supplied tip, not a
    selected event page. The caller retains responsibility for later head CAS.
    """
    at = finite(at)
    identity(account_id); identity(event_id); identity(collateral_asset); sha(rule_fingerprint)
    if not isinstance(policy, ObservationPolicy):
        raise EvidenceError('OBSERVATION_POLICY_REQUIRED')
    reason = policy.reason()
    if reason:
        return _result(reason, at=at, policy=policy, tip_sha256=tip_sha256, account_id=account_id,
                       event_id=event_id, rule_fingerprint=rule_fingerprint, collateral_asset=collateral_asset)
    frontier_sha256 = None
    try:
        sha(tip_sha256)
        _validate_rows(rows, tip_sha256, at, policy.complete_history_scan_bound)
        frontier_sha256 = digest([r['sha256'] for r in rows])
        archive = _Archive(rows)
        rule_heads = [r for r in rows if r['kind'] == 'RULE_STATE' and r['event_id'] == event_id]
        account_heads = [r for r in rows if r['kind'] == 'COORDINATOR_EVENT' and r['event_id'] == ACCOUNT_KEY]
        if not rule_heads or not account_heads:
            raise EvidenceError('OBSERVATION_RULE_OR_ACCOUNT_HEAD_MISSING')
        rule, raw, preimage = _rule_lineage(archive, rule_heads, event_id, rule_fingerprint)
        partition = preimage.get('partition')
        if not isinstance(partition, list) or not partition or len(partition) > 32:
            raise EvidenceError('OBSERVATION_PARTITION_INCOMPLETE')
        targets = {(b['market_id'], b['condition_id'], b['yes_token'], 'YES') for b in partition}
        targets |= {(b['market_id'], b['condition_id'], b['no_token'], 'NO') for b in partition}
        if len(targets) != 2 * len(partition):
            raise EvidenceError('OBSERVATION_PARTITION_INCOMPLETE')
        account = account_heads[-1]
        state = _mapping(_mapping(account['body'].get('details')).get('state'))
        fills = state.get('fills'); intents = state.get('intents')
        if not isinstance(fills, dict) or not isinstance(intents, dict) or len(fills) > 2048:
            raise EvidenceError('OBSERVATION_POPULATION_INCOMPLETE')
        proofs = {}
        for row in rows:
            if row['kind'] != 'TRADE':
                continue
            p = _mapping(row['body'].get('payload'))
            if p.get('record_type') != 'PAPER_FILL':
                continue
            if row['event_id'] == event_id and p.get('account_id') != account_id:
                raise EvidenceError('OBSERVATION_AMBIGUOUS_FILL_ACCOUNT')
            if p.get('account_id') != account_id:
                continue
            if row['body'].get('evidence_class') != 'SYNTHETIC' or p.get('execution_namespace') != 'V11_PAPER':
                raise EvidenceError('OBSERVATION_PROOF_SCOPE')
            fill_id = identity(p.get('fill_id'))
            if fill_id in proofs:
                raise EvidenceError('OBSERVATION_DUPLICATE_ECONOMIC_FILL')
            proofs[fill_id] = row
        if set(proofs) != set(fills) or any(proofs[k]['sha256'] != v for k, v in fills.items()):
            raise EvidenceError('OBSERVATION_POPULATION_INCOMPLETE')
        state = _account_lineage(archive, account_heads, account, proofs, rule, raw,
                                 account_id, event_id, rule_fingerprint)
        recent = []; evidence = [raw['id'], rule['id'], account['id']]
        intent_quantities = {}
        for fill_id, proof in proofs.items():
            p = proof['body']['payload']; intent = intents.get(p.get('intent_id'))
            if not isinstance(intent, dict) or intent.get('event_id') != proof['event_id']:
                raise EvidenceError('OBSERVATION_INTENT_SCOPE')
            if (intent.get('direction') not in {'BUY', 'SELL'} or p.get('direction') != intent['direction']
                    or intent.get('proposal_id') != p.get('intent_id')):
                raise EvidenceError('OBSERVATION_DIRECTION_OR_INTENT_UNKNOWN')
            if proof['event_id'] != event_id:
                continue
            target = intent.get('target', {})
            if (not isinstance(target, dict) or tuple(target.get(k) for k in ('market_id', 'condition_id', 'token_id', 'side')) not in targets
                    or _mapping(intent.get('binding')).get('rule_fingerprint') != rule_fingerprint):
                raise EvidenceError('OBSERVATION_TARGET_OR_RULE_SCOPE')
            qty = number(p.get('units'))
            intent_quantities[p['intent_id']] = intent_quantities.get(p['intent_id'], Decimal(0)) + qty
            if intent['direction'] == 'BUY':
                lot = _mapping(_mapping(state.get('lots')).get(fill_id), 'OBSERVATION_LOT_LINEAGE_UNKNOWN')
                if (lot.get('lot_id') != fill_id or lot.get('token_id') != intent.get('token_id')
                        or lot.get('event_id') != event_id or lot.get('acquired_sequence') != proof['seq']
                        or number(lot.get('units')) != qty
                        or number(lot.get('all_in_cost_basis')) != number(p.get('all_in_collateral'))):
                    raise EvidenceError('OBSERVATION_LOT_LINEAGE_UNKNOWN')
            else:
                # Sale basis/remaining inventory requires a separate bounded adapter.
                raise EvidenceError('OBSERVATION_SELL_RECONCILIATION_UNKNOWN')
            scoped = dict(intent, collateral_asset=collateral_asset)
            detail = execution_details(archive, proof, scoped, account_id=account_id, collateral_asset=collateral_asset)
            if detail['status'] != 'VALIDATED_SYNTHETIC_DETAILS':
                raise EvidenceError('OBSERVATION_FILL_TIMING_UNKNOWN')
            executed = finite(detail['executed_at'])
            if executed > at:
                raise EvidenceError('OBSERVATION_FILL_FUTURE')
            evidence.append(proof['id'])
            if at - policy.lookback_seconds <= executed:
                recent.append((proof, scoped, detail, executed))
        for intent_id, total in intent_quantities.items():
            intent = _mapping(intents[intent_id])
            if number(intent.get('filled_units')) != total or total > number(intent.get('units')):
                raise EvidenceError('OBSERVATION_INTENT_RECONCILIATION_UNKNOWN')
        if not recent:
            valid_until = _deadline(at, policy.maximum_measurement_age_seconds)
            return _result('NO_RECONCILED_RECENT_PAPER_FILLS', at=at, policy=policy, tip_sha256=tip_sha256,
                           account_id=account_id, event_id=event_id, rule_fingerprint=rule_fingerprint,
                           collateral_asset=collateral_asset, fill_count=0, adverse=0, evidence=evidence, valid_until=valid_until,
                           frontier_sha256=frontier_sha256)
        if any(_deadline(executed, policy.horizon_seconds) > at for _, _, _, executed in recent):
            raise EvidenceError('PENDING_MARKOUT')
        numerator = Decimal(0); quantity_sum = Decimal(0); adverse = 0
        valid_until = _deadline(at, policy.maximum_measurement_age_seconds)
        for proof, intent, detail, executed in recent:
            horizon = _deadline(executed, policy.horizon_seconds)
            horizon_end = _deadline(horizon, policy.maximum_horizon_delay_seconds) if policy.maximum_horizon_delay_seconds else horizon
            anchor = archive.get(detail['post_validation_book_ref']['id'])
            if (anchor['sha256'] != detail['post_validation_book_ref']['sha256']
                    or anchor['body'].get('provider') != policy.book_provider
                    or anchor['body'].get('source_identity') != intent['token_id']):
                raise EvidenceError('OBSERVATION_BOOK_ANCHOR_SCOPE')
            evidence.append(anchor['id'])
            books = [r for r in rows if r['kind'] == 'BOOK' and r['event_id'] == event_id
                     and r['seq'] > anchor['seq'] and r['body'].get('provider') == policy.book_provider
                     and r['body'].get('source_identity') == intent['token_id']]
            # A declared stream chain must cover every received update after the
            # fill, so a conveniently selected horizon book cannot hide a gap.
            previous = _sequence(anchor); candidates = []
            for book in books:
                seq = _sequence(book)
                if seq['epoch'] != previous['epoch'] or seq['previous_sequence'] != previous['sequence']:
                    raise EvidenceError('OBSERVATION_BOOK_SEQUENCE_GAP')
                previous = seq
                bid, ask = _touch(book, proof, intent, policy, at,
                                  after_fill=book['seq'] > proof['seq'])
                if book['seq'] <= proof['seq']:
                    continue
                observed = book['body']['observed_at']
                if horizon <= observed <= horizon_end:
                    candidates.append((observed, book['seq'], book, bid, ask))
            if not candidates:
                raise EvidenceError('OBSERVATION_HORIZON_BOOK_MISSING')
            _, _, chosen, bid, ask = min(candidates)
            evidence.append(chosen['id'])
            qty = number(proof['body']['payload']['units'])
            price = number(detail['price_per_share']); fee = number(detail['fee_collateral'])
            cost = number(detail['other_cost_collateral'])
            markout = (bid - price if intent['direction'] == 'BUY' else price - ask) - (fee + cost) / qty
            numerator += qty * markout; quantity_sum += qty
            adverse += markout < 0
            valid_until = min(valid_until, _deadline(executed, policy.lookback_seconds))
        mean = str(numerator / quantity_sum)
        return _result(None, at=at, policy=policy, tip_sha256=tip_sha256, account_id=account_id,
                       event_id=event_id, rule_fingerprint=rule_fingerprint, fill_count=len(recent),
                       collateral_asset=collateral_asset, adverse=adverse, markout=mean, evidence=evidence, valid_until=valid_until,
                       frontier_sha256=frontier_sha256)
    except (EvidenceError, DecimalException, KeyError, TypeError, ValueError, ZeroDivisionError, AttributeError) as exc:
        reason = str(exc) if isinstance(exc, EvidenceError) else 'OBSERVATION_MALFORMED_INPUT'
        return _result(reason, at=at, policy=policy, tip_sha256=tip_sha256,
                       account_id=account_id, event_id=event_id, rule_fingerprint=rule_fingerprint,
                       collateral_asset=collateral_asset,
                       frontier_sha256=frontier_sha256)


def observe(rows, *, tip_sha256, at, policy, account_id, event_id, rule_fingerprint, collateral_asset):
    """Pure nonfinancial observation under a fixed decimal arithmetic contract."""
    with localcontext(NUMERIC_CONTEXT):
        return _observe(rows, tip_sha256=tip_sha256, at=at, policy=policy,
                        account_id=account_id, event_id=event_id,
                        rule_fingerprint=rule_fingerprint, collateral_asset=collateral_asset)
