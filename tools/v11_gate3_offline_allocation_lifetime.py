"""Pure, synthetic allocation/lifetime reconciliation for a validated V4 budget.

The caller supplies the output of calculate_validated_v4_offline_resource_budget
and *asserted* domain ceilings and allocation claims. This module cannot verify
their provenance, physical backing, owner custody, or host availability. Envelope
rows deliberately remain separate: no V4/runtime overlap credit is inferred.
"""

from dataclasses import dataclass

MAX_INT = 2**63 - 1
MAX_ROWS = 128
FRESH_CHECKS = frozenset((
    'budget_events', 'budget_journal_bytes', 'store_events',
    'store_journal_bytes', 'store_objects', 'session_events',
    'session_journal_bytes', 'denial_events', 'denial_journal_bytes',
))

# Envelopes preserve both existing calculations. Other categories make missing
# allocation paths visible, even if the validated budget has no bound for them.
REQUIRED = {
    'v4_envelope': ('DISK', 'V4'),
    'runtime_envelope': ('DISK', 'RUNTIME'),
    'raw': ('DISK', 'UNCOVERED'),
    'temporary_copies': ('DISK', 'UNCOVERED'),
    'final_objects': ('DISK', 'UNCOVERED'),
    'decoded_outputs': ('DISK', 'UNCOVERED'),
    'aggregates': ('DISK', 'UNCOVERED'),
    'budget_journal': ('DISK', 'UNCOVERED'),
    'store_journal': ('DISK', 'UNCOVERED'),
    'session_journal': ('DISK', 'UNCOVERED'),
    'denial_journal': ('DISK', 'UNCOVERED'),
    'clock_receipts': ('DISK', 'UNCOVERED'),
    'evidence_imports': ('DISK', 'UNCOVERED'),
    'allocation_metadata': ('DISK', 'UNCOVERED'),
    'refusal_sentinels': ('DISK', 'UNCOVERED'),
    'custody_journal': ('DISK', 'UNCOVERED'),
    'report': ('DISK', 'UNCOVERED'),
    'finalization_scratch': ('DISK', 'UNCOVERED'),
    'diagnostics': ('DISK', 'UNCOVERED'),
    'parent_child_memory': ('MEMORY', 'RUNTIME_MEMORY'),
}


@dataclass(frozen=True, slots=True)
class Domain:
    name: str
    medium: str  # DISK or MEMORY; aliases must use one common pool.
    pool: str
    peak_bytes_ceiling: int  # Asserted offline ceiling, never a host reading.
    peak_inodes_ceiling: int


@dataclass(frozen=True, slots=True)
class Obligation:
    category: str
    domain: str
    owner: str
    backing_claim: str
    start: int  # Inclusive allocation lifetime sequence.
    end: int  # Exclusive; includes recovery/retention, not window expiry.
    maximum_bytes: int
    maximum_inodes: int
    materialized_bytes: int
    materialized_inodes: int
    outstanding_bytes: int
    outstanding_inodes: int
    materialized_at: int | None
    outstanding_at: int | None


def _uint(value):
    if type(value) is not int or not 0 <= value <= MAX_INT:
        raise ValueError('bounded integer required')
    return value


def _text(value):
    if (type(value) is not str or not 1 <= len(value) <= 96 or
            not value.isascii() or any(ord(c) < 33 or ord(c) > 126 for c in value)):
        raise ValueError('bounded identity required')
    return value


def _add(a, b):
    value = a + b
    return _uint(value)


def _plain_keys(mapping, limit):
    if type(mapping) is not dict or len(mapping) > limit:
        raise ValueError('bounded plain mapping required')
    for key in mapping:
        if type(key) is not str:
            raise ValueError('plain mapping keys required')


def _marker(mapping, key, expected):
    value = mapping[key]
    if type(value) is not str or value != expected:
        raise ValueError('unqualified validated budget required')


def _budget(budget):
    _plain_keys(budget, 64)
    try:
        capacity = budget['runtime_capacity']
        checks = budget['fresh_ceiling_checks']
        _plain_keys(capacity, 16)
        # Refuse excess freshness keys before copying or traversing them.
        if type(checks) is not dict or len(checks) != len(FRESH_CHECKS):
            raise ValueError('unqualified validated budget required')
        for key, value in checks.items():
            if type(key) is not str or key not in FRESH_CHECKS or value is not True:
                raise ValueError('unqualified validated budget required')
        _marker(budget, 'mode', 'OFFLINE_PROPOSAL')
        _marker(budget, 'manifest_validation', 'V4_VALIDATED_EXACT_BYTES')
        _marker(budget, 'event_binding',
                'MATCHES_VALIDATED_V4_MANIFEST_FIELD_EXPANSION')
        _marker(budget, 'g3l', 'NO_GO')
        _marker(budget, 'existing_occupancy', 'UNKNOWN')
        _marker(budget, 'live_host_resources', 'UNKNOWN')
        if (budget['capacity_covers_frozen_schedule'] is not True or
                budget['resource_qualification'] is not False or
                budget['execution_authority'] is not False or
                budget['provider_authority'] is not False or
                type(budget['qualification_credit']) is not int or
                budget['qualification_credit'] != 0):
            raise ValueError('unqualified validated budget required')
        digest = budget['validated_manifest_sha256']
        if (type(digest) is not str or len(digest) != 64 or
                any(c not in '0123456789abcdef' for c in digest)):
            raise ValueError('invalid manifest digest')
        quota = _uint(budget['v4_quota_formula_bytes'])
        if quota > _uint(budget['v4_local_storage_quota_bytes']):
            raise ValueError('validated quota ceiling mismatch')
        return (quota, _uint(capacity['disk_bytes']),
                _uint(capacity['memory_bytes']))
    except (KeyError, TypeError) as exc:
        raise ValueError('incomplete validated budget') from exc


def reconcile(budget, obligations, domains, *, snapshot):
    """Check asserted claims and peaks; return no operational authority.

    Materialized quantities were already reflected in a post-allocation snapshot;
    only outstanding quantities remain prospective. Each row's maximum is their
    exact sum. A row cannot silently release at the snapshot or at window end.
    Domain ceilings are synthetic lifetime limits, not sampled free space.
    """
    v4, runtime_disk, runtime_memory = _budget(budget)
    snap = _uint(snapshot)
    if (type(obligations) not in (tuple, list) or
            not 0 < len(obligations) <= MAX_ROWS or
            type(domains) not in (tuple, list) or not 1 <= len(domains) <= 16):
        raise ValueError('bounded rows and domains required')
    domain_by_name = {}
    pools = set()
    for domain in domains:
        if type(domain) is not Domain:
            raise ValueError('domain record required')
        name, pool = _text(domain.name), _text(domain.pool)
        if (name in domain_by_name or pool in pools or
                type(domain.medium) is not str or
                domain.medium not in ('DISK', 'MEMORY')):
            raise ValueError('duplicate or invalid backing pool')
        _uint(domain.peak_bytes_ceiling)
        _uint(domain.peak_inodes_ceiling)
        domain_by_name[name] = domain
        pools.add(pool)

    categories = set()
    claims = set()
    envelopes = {'V4': [], 'RUNTIME': [], 'RUNTIME_MEMORY': []}
    events = {name: [] for name in domain_by_name}
    outstanding = {name: {'bytes': 0, 'inodes': 0} for name in domain_by_name}
    for row in obligations:
        if (type(row) is not Obligation or type(row.category) is not str or
                row.category not in REQUIRED or type(row.domain) is not str):
            raise ValueError('known obligation record required')
        _text(row.owner)
        claim = _text(row.backing_claim)
        if claim in claims or row.domain not in domain_by_name:
            raise ValueError('duplicate backing claim or unknown domain')
        claims.add(claim)
        categories.add(row.category)
        domain = domain_by_name[row.domain]
        medium, component = REQUIRED[row.category]
        if medium != domain.medium:
            raise ValueError('category on wrong medium')
        start, end = _uint(row.start), _uint(row.end)
        if not start < end or end <= snap:
            raise ValueError('unreconciled or expired lifetime')
        maximum_bytes = _uint(row.maximum_bytes)
        maximum_inodes = _uint(row.maximum_inodes)
        materialized_bytes = _uint(row.materialized_bytes)
        materialized_inodes = _uint(row.materialized_inodes)
        outstanding_bytes = _uint(row.outstanding_bytes)
        outstanding_inodes = _uint(row.outstanding_inodes)
        if (maximum_bytes == 0 or
                maximum_bytes != _add(materialized_bytes, outstanding_bytes) or
                maximum_inodes != _add(materialized_inodes, outstanding_inodes) or
                (medium == 'DISK' and maximum_inodes == 0) or
                (medium == 'MEMORY' and maximum_inodes != 0)):
            raise ValueError('allocation quantities do not reconcile')
        has_materialized = materialized_bytes > 0 or materialized_inodes > 0
        has_outstanding = outstanding_bytes > 0 or outstanding_inodes > 0
        if has_materialized != (row.materialized_at is not None):
            raise ValueError('materialization order missing')
        if has_outstanding != (row.outstanding_at is not None):
            raise ValueError('outstanding order missing')
        if has_materialized and not start <= _uint(row.materialized_at) <= snap:
            raise ValueError('materialized after snapshot')
        if has_outstanding and not max(start, snap + 1) <= _uint(row.outstanding_at) < end:
            raise ValueError('outstanding allocation precedes snapshot or release')
        if component in envelopes:
            envelopes[component].extend(((start, 1, maximum_bytes),
                                         (end, -1, maximum_bytes)))
        events[row.domain].extend(((start, 1, maximum_bytes, maximum_inodes),
                                   (end, -1, maximum_bytes, maximum_inodes)))
        outstanding[row.domain]['bytes'] = _add(
            outstanding[row.domain]['bytes'], outstanding_bytes)
        outstanding[row.domain]['inodes'] = _add(
            outstanding[row.domain]['inodes'], outstanding_inodes)
    if categories != set(REQUIRED):
        raise ValueError('missing allocation categories')
    # All three whole envelopes must coexist somewhere in the asserted plan.
    # Sequential fragments cannot turn cumulative throughput into capacity.
    live = dict.fromkeys(envelopes, 0)
    envelope_events = sorted((time, direction, component, size)
                             for component, component_events in envelopes.items()
                             for time, direction, size in component_events)
    covered = False
    for _, direction, component, size in envelope_events:
        live[component] = _uint(live[component] + direction * size)
        if (live['V4'] >= v4 and live['RUNTIME'] >= runtime_disk and
                live['RUNTIME_MEMORY'] >= runtime_memory):
            covered = True
    if not covered or any(live.values()):
        raise ValueError('validated budget envelope not covered')
    peaks = {}
    for name, domain in domain_by_name.items():
        if not events[name]:
            raise ValueError('unused domain claim')
        live_bytes = live_inodes = peak_bytes = peak_inodes = 0
        # End events precede start events at the same sequence (half-open life).
        for _, direction, size, inodes in sorted(events[name]):
            live_bytes += direction * size
            live_inodes += direction * inodes
            if not 0 <= live_bytes <= MAX_INT or not 0 <= live_inodes <= MAX_INT:
                raise ValueError('overlapping lifetime arithmetic overflow')
            peak_bytes = max(peak_bytes, live_bytes)
            peak_inodes = max(peak_inodes, live_inodes)
        if (live_bytes or live_inodes or
                peak_bytes > domain.peak_bytes_ceiling or
                peak_inodes > domain.peak_inodes_ceiling):
            raise ValueError('overlapping lifetimes exceed checked ceiling')
        peaks[name] = {'bytes': peak_bytes, 'inodes': peak_inodes,
                       'outstanding_after_snapshot': outstanding[name]}
    return {'status': 'UNQUALIFIED', 'manifest_sha256': budget['validated_manifest_sha256'],
            'lifetime_peaks': peaks, 'execution_authority': False,
            'provider_authority': False, 'capture_authority': False,
            'host_authority': False, 'selected_window_evidence': False,
            'resource_qualification': False, 'g3l': 'NO_GO',
            'qualification_credit': 0}
