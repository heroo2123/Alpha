"""Pure Gate 3 V4 resource proposal for an already validated manifest.

The caller must validate the complete canonical manifest with
``validate_manifest_v4`` separately. This module neither performs that
filesystem based validation nor treats a caller's payload as approval.
It models fresh journal headroom; existing occupancy and delivered bytes are
unknown. Nothing here acquires resources or changes runtime admission.
"""

from __future__ import annotations

from tools.v11_r09_gate3_launch import FIELD_LIMITS, MAX_BYTES
from tools.v11_r09_gate3_launch_v4 import PURPOSES, SCHEMA

MAX_INT = 2**63 - 1
MAX_REQUESTS = 3600
MAX_EVENTS = 2713
MAX_EVENT_LINKS = 8192
RECORD_BYTES = 65536
DISK_FLOOR = 2 * 1024**3
MEMORY_FLOOR = 512 * 1024**2
REPORT_RESERVE_BYTES = 16 * 1024**2


def _number(value, low=0, high=MAX_INT):
    if type(value) is not int or not low <= value <= high:
        raise ValueError('bounded integer required')
    return value


def _mapping(value, keys):
    if (type(value) is not dict or len(value) != len(keys) or
            any(type(key) is not str for key in value) or
            set(value) != set(keys)):
        raise ValueError('unexpected proposal shape')
    return value


def _calculate(manifest, frozen_plan):
    """Recount one full frozen V4 schedule; return diagnostics, never authority.

    ``manifest`` is the parsed payload of a separately accepted V4 manifest.
    ``frozen_plan`` has exactly ``mode``, ``requests`` and ``events``. Requests
    repeat the manifest's ordered (id, purpose, reservation) triples; events
    carry only ``field_request_ids``. In a validated V4 manifest, each cohort
    event projects to every FIELD request in schedule order. Check that exact
    capacity-relevant expansion; side, keys and event metadata are outside
    this deliberately narrow projection.
    """
    if type(manifest) is not dict or type(frozen_plan) is not dict:
        raise ValueError('mapping required')
    _mapping(frozen_plan, ('mode', 'requests', 'events'))
    if type(frozen_plan['mode']) is not str or frozen_plan['mode'] != 'OFFLINE_PROPOSAL':
        raise ValueError('unsupported mode')
    try:
        identity = manifest['identity']
        limits = manifest['limits']
        schedule = manifest['schedule']
        cohort = manifest['cohort']
        runtime = manifest['runtime']
        storage = manifest['storage']
        source_requests = schedule['requests']
        requests = frozen_plan['requests']
        events = frozen_plan['events']
        purpose_plan = runtime['purpose_plan']
        bounds = runtime['resource_bounds']
    except (KeyError, TypeError) as exc:
        raise ValueError('incomplete V4 projection') from exc
    if (any(type(value) is not dict for value in
            (identity, limits, schedule, cohort, runtime, storage, bounds)) or
            identity.get('schema') != SCHEMA or
            type(source_requests) is not list or
            type(requests) is not list or
            not 0 < len(requests) == len(source_requests) <= MAX_REQUESTS or
            type(events) is not list or len(events) > MAX_EVENTS or
            type(purpose_plan) is not dict or len(purpose_plan) != len(PURPOSES) or
            set(purpose_plan) != set(PURPOSES)):
        raise ValueError('unbounded or mismatched V4 projection')
    # Work from one bounded event-list snapshot. The caller's mutable list
    # cannot change the number of capacity nodes after the shape check.
    events = tuple(events)

    maximum_requests = _number(limits['max_requests'], 1, MAX_REQUESTS)
    maximum_bytes = _number(limits['max_received_bytes'], 1, MAX_BYTES)
    maximum_elapsed = _number(limits['max_elapsed_seconds'], 1, 10800)
    deadline = _number(limits['request_deadline_seconds'], 1, 30)
    interval = _number(limits['min_start_interval_seconds'], 2, 10800)
    processing = _number(schedule['processing_seconds'], 60, 10800)
    finalization = _number(schedule['finalization_seconds'], 60, 10800)
    report_reserve = _number(storage['report_reserve_bytes'], REPORT_RESERVE_BYTES,
                             MAX_BYTES)
    if (report_reserve != _number(bounds['report_reserve_bytes']) or
            _number(bounds['max_body_chunks_per_request']) != 32 or
            _number(bounds['store_object_max_bytes']) != 4194304 or
            _number(bounds['journal_record_max_bytes']) != RECORD_BYTES):
        raise ValueError('unsupported V4 resource bounds')

    totals = {p: {'requests': 0, 'reservation_bytes': 0} for p in PURPOSES}
    ids = set()
    body = chunks = largest = 0
    for source, request in zip(source_requests, requests):
        _mapping(request, ('request_id', 'purpose', 'reservation_bytes'))
        if (type(source) is not dict or
                any(type(source.get(k)) is not type(request[k]) or
                    request[k] != source.get(k) for k in request)):
            raise ValueError('frozen request differs from V4 schedule')
        rid, purpose = request['request_id'], request['purpose']
        if (type(rid) is not str or not 1 <= len(rid) <= 80 or
                not all(c.isascii() and (c.isalnum() or c in '_-') for c in rid) or
                rid in ids or type(purpose) is not str or purpose not in totals):
            raise ValueError('invalid frozen request')
        ids.add(rid)
        size = _number(request['reservation_bytes'], 1, MAX_BYTES)
        provider = source.get('provider')
        if provider is not None and type(provider) is not str:
            raise ValueError('invalid provider')
        cap = (FIELD_LIMITS.get(provider, 0) if purpose == 'FIELD'
               else 3145728 if purpose == 'INDEX' else 4194304)
        if size > cap:
            raise ValueError('purpose cap exceeded')
        totals[purpose]['requests'] += 1
        totals[purpose]['reservation_bytes'] += size
        body += size
        chunks += min(size, 32)
        largest = max(largest, size)
    if (len(requests) > maximum_requests or body > maximum_bytes or
            body != _number(schedule['reservation_total_bytes'], 0, MAX_BYTES)):
        raise ValueError('V4 request or body ceiling exceeded')
    for purpose in PURPOSES:
        claim = _mapping(purpose_plan[purpose], ('requests', 'reservation_bytes'))
        if (totals[purpose]['requests'] != _number(claim['requests'], 0, MAX_REQUESTS) or
                totals[purpose]['reservation_bytes'] !=
                _number(claim['reservation_bytes'], 0, MAX_BYTES)):
            raise ValueError('V4 purpose plan mismatch')

    cohort_events = cohort.get('events')
    if (type(cohort_events) is not list or
            not 1 <= len(cohort_events) <= 2 or
            any(type(side) is not str or side not in ('HIGH', 'LOW')
                for side in cohort_events) or
            len(set(cohort_events)) != len(cohort_events) or
            len(events) != len(cohort_events)):
        raise ValueError('V4 cohort event count mismatch')
    field_ids = [r['request_id'] for r in requests if r['purpose'] == 'FIELD']
    if not field_ids or len(field_ids) * len(events) > MAX_EVENT_LINKS:
        raise ValueError('V4 event expansion exceeds bounds')
    aggregate_nodes = links = 0
    for event in events:
        _mapping(event, ('field_request_ids',))
        members = event['field_request_ids']
        if type(members) is not list:
            raise ValueError('frozen event differs from V4 field expansion')
        # Refuse an oversized caller plan before allocating its snapshot.
        if (len(members) != len(field_ids) or
                len(members) > MAX_EVENT_LINKS - links):
            raise ValueError('frozen event differs from V4 field expansion')
        members = tuple(members[:len(field_ids) + 1])
        if (len(members) != len(field_ids) or
                len(members) > MAX_EVENT_LINKS - links or
                any(type(rid) is not str or rid != expected
                    for rid, expected in zip(members, field_ids))):
            raise ValueError('frozen event differs from V4 field expansion')
        links += len(members)
        width = len(members)
        aggregate_nodes += 1
        while width > 256:
            width = (width + 255) // 256
            aggregate_nodes += width

    n = len(requests)
    records = chunks + 3 * n
    store_events = 3 * (n + aggregate_nodes)
    disk = (2 * body + 2 * aggregate_nodes * 4194304 +
            (records + store_events + 20 * n + 4 * n + 4) * RECORD_BYTES +
            REPORT_RESERVE_BYTES + 4096)
    memory = 2 * largest + 4 * RECORD_BYTES
    serial = n * deadline + (n - 1) * interval + processing + finalization
    required_objects = 2 * n
    width = n
    while width > 1:
        width = (width + 255) // 256
        required_objects += width
    v4_quota = (4 * 64 * 1024**2 + report_reserve +
                required_objects * 4194304 +
                _number(limits['decoded'], 1, MAX_BYTES) + body)
    if any(x > MAX_INT for x in (disk, memory, serial, v4_quota)):
        raise ValueError('resource arithmetic overflow')
    if (serial > maximum_elapsed or
            required_objects != _number(bounds['required_store_objects']) or
            v4_quota > _number(bounds['local_storage_quota_bytes']) or
            _number(bounds['store_max_objects']) != 4096 or
            _number(bounds['store_max_events']) != 10000 or
            _number(bounds['session_journal_max_events']) != 32768 or
            _number(bounds['denial_journal_max_events']) != 32768):
        raise ValueError('V4 ceiling or quota mismatch')
    # These are fresh-root estimates. The schedule coverage flag below is a
    # mathematical comparison against the supplied manifest under the external
    # validation precondition, not proof of manifest custody or runtime admission.
    # Existing journal bytes/events and host resources are deliberately absent.
    fresh_ceiling_checks = {
        'budget_events': records <= 131072,
        'budget_journal_bytes': records * RECORD_BYTES <= 64 * 1024**2,
        'store_events': store_events <= 10000,
        'store_journal_bytes': store_events * RECORD_BYTES + REPORT_RESERVE_BYTES <= 64 * 1024**2,
        'store_objects': n + aggregate_nodes <= 4096,
        'session_events': 20 * n + 1 <= 32768,
        'session_journal_bytes': (20 * n + 1) * RECORD_BYTES <= 64 * 1024**2,
        'denial_events': 4 * n <= 32768,
        'denial_journal_bytes': 4 * n * RECORD_BYTES <= 64 * 1024**2,
    }
    return {
        'mode': 'OFFLINE_PROPOSAL',
        'purpose_budgets': totals,
        'received_bytes_ceiling': maximum_bytes,
        'unallocated_received_bytes': maximum_bytes - body,
        'unallocated_request_count': maximum_requests - n,
        'unallocated_elapsed_seconds': maximum_elapsed - serial,
        'unknown_delivered_bytes_range': [0, body],
        'unknown_delivered_bytes_by_purpose': {
            p: [0, totals[p]['reservation_bytes']] for p in PURPOSES},
        'elapsed_seconds_ceiling': maximum_elapsed,
        'serial_seconds_upper': serial,
        'runtime_capacity': {'disk_bytes': disk, 'memory_bytes': memory,
                             'budget_records': records, 'store_events': store_events,
                             'aggregate_nodes': aggregate_nodes},
        'event_binding': 'MATCHES_SUPPLIED_V4_MANIFEST_FIELD_EXPANSION',
        'capacity_covers_frozen_schedule': True,
        'v4_local_storage_quota_bytes': _number(bounds['local_storage_quota_bytes']),
        'v4_quota_formula_bytes': v4_quota,
        'minimum_free_disk_for_fresh_plan_bytes': DISK_FLOOR + disk,
        'minimum_available_memory_for_fresh_plan_bytes': MEMORY_FLOOR + memory,
        'fresh_ceiling_checks': fresh_ceiling_checks,
        'existing_occupancy': 'UNKNOWN',
        'live_host_resources': 'UNKNOWN',
        'execution_authority': False, 'provider_authority': False,
        'resource_qualification': False, 'g3l': 'NO_GO',
        'qualification_credit': 0,
    }


def calculate_offline_resource_budget(manifest, frozen_plan):
    """Return a bounded paper estimate; normalize malformed input to ValueError."""
    try:
        return _calculate(manifest, frozen_plan)
    except (KeyError, TypeError, AttributeError, OverflowError) as exc:
        raise ValueError('malformed V4 resource proposal') from exc
