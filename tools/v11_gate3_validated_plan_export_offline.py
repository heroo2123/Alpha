"""Bounded supplied-byte prerequisite for a future Gate 3 plan export.

This module never validates a V4 manifest or issues an accepted export.  Its
snapshot is a content check of untrusted, synthetic/offline input bytes.  A
production exporter still needs the reviewed validation seam, bounded object
resolution, custody, and an independently authenticated pin channel.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Protocol

from tools.v11_multimodel_panel import canonical
from tools.v11_r09_gate3_launch import SLOT_COUNT, SLOTS, _slot_inventory

ROLES = ('manifest', 'plan_review', 'supplemental_pins', 'runtime_context',
         'terminal_precedence', 'event_policy', 'event_policy_review')
LIMITS = (32 * 1024**2, 4096, 2 * 1024**2, 4096, 4096, 65536, 16384)
MAX_TOTAL = 40 * 1024**2
HEX = re.compile(r'[0-9a-f]{64}\Z')
ID = re.compile(r'[A-Za-z0-9_-]{1,80}\Z')
PURPOSES = ('FIELD', 'INDEX', 'OBJECT_ID', 'METADATA', 'PROBE')
PROVIDERS = tuple(SLOTS)
PRECEDENCE = frozenset(('PREREQUISITE', 'CLOCK', 'RESOURCE', 'DENIAL',
                        'VALIDATION', 'SUCCESS', 'UNSCHEDULED'))
FLAGS = MappingProxyType({'execution_authority': False, 'launch_authority': False,
         'financial_authority': False, 'promotion_authority': False,
         'host_approved': False, 'resource_qualification': False,
         'provider_rights': False, 'clock_qualification': False,
         'capture_eligibility': False, 'feature_eligibility': False,
         'label_eligibility': False, 'learner_admission': False,
         'shadow_admission': False, 'g3l': 'NO_GO', 'g3e': 'GATED',
         'qualification_credit': 0})


class ExportRefusal(ValueError):
    """Stable refusal code, with no candidate bytes in the exception."""


class AuthenticatedPinChannel(Protocol):
    """Future external trust boundary; this repository has no issuer/adapter.

    Implementing this Python method in caller code does not authenticate it.
    A separately reviewed integration must pin the channel itself before any
    result can be used for an accepted export.
    """

    def resolve_exact(self, export_sha256: str, custody_sha256: str,
                      acceptance_sha256: str) -> object: ...


def _need(condition, code):
    if not condition:
        raise ExportRefusal(code)


def _exact(value, keys, code='VPE_SCHEMA'):
    _need(type(value) is dict and len(value) == len(keys) and
          all(type(k) is str for k in value) and set(value) == set(keys), code)


def _hash(value):
    _need(type(value) is str and HEX.fullmatch(value) is not None, 'VPE_SCHEMA')


def _integer(value, low=0, high=2**63 - 1):
    _need(type(value) is int and low <= value <= high, 'VPE_SCHEMA')


def _preflight(raw):
    """Bound tokens and nesting before json.loads can materialize a tree."""
    depth = nodes = token = 0
    quoted = escaped = atom = False
    for byte in raw:
        if quoted:
            token += 1
            _need(token <= 24576, 'VPE_INPUT_BOUNDS')
            if escaped:
                escaped = False
            elif byte == 92:
                escaped = True
            elif byte == 34:
                quoted = False
        elif byte == 34:
            quoted, token = True, 0
            nodes += 1
        elif byte in (91, 123):
            depth += 1
            nodes += 1
            atom = False
            _need(depth <= 16, 'VPE_INPUT_BOUNDS')
        elif byte in (93, 125):
            depth -= 1
            atom = False
        elif byte in b'-+0123456789.eE' and atom:
            token += 1
            _need(token <= 32, 'VPE_INPUT_BOUNDS')
        elif byte not in b' \t\r\n,:':
            nodes += 1
            atom = True
            token = 1
        else:
            atom = False
            token = 0
        _need(nodes <= 500000, 'VPE_INPUT_BOUNDS')
    _need(depth == 0 and not quoted, 'VPE_CANONICAL_BYTES')


def _parse(raw, limit):
    _need(type(raw) is bytes and 0 < len(raw) <= limit, 'VPE_INPUT_BOUNDS')
    _preflight(raw)
    def pairs(items):
        result = {}
        for key, value in items:
            _need(key not in result, 'VPE_CANONICAL_BYTES')
            result[key] = value
        return result
    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs,
                           parse_constant=lambda _: _need(False, 'VPE_CANONICAL_BYTES'))
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        if type(exc) is ExportRefusal:
            raise
        raise ExportRefusal('VPE_CANONICAL_BYTES') from exc
    def walk(item, depth=0):
        _need(depth <= 16, 'VPE_INPUT_BOUNDS')
        if type(item) is dict:
            _need(len(item) <= (3600 if depth == 0 else 64), 'VPE_INPUT_BOUNDS')
            for key, child in item.items():
                walk(key, depth + 1)
                walk(child, depth + 1)
        elif type(item) is list:
            _need(len(item) <= 3600, 'VPE_INPUT_BOUNDS')
            for child in item:
                walk(child, depth + 1)
        elif type(item) is str:
            _need(len(item.encode('utf-8', 'surrogatepass')) <= 4096 and
                  not any(0xD800 <= ord(c) <= 0xDFFF for c in item),
                  'VPE_INPUT_BOUNDS')
        elif type(item) is int:
            _integer(item, -(2**63 - 1))
        elif type(item) is float:
            _need(item == item and abs(item) != float('inf') and
                  not (item == 0 and json.dumps(item).startswith('-')),
                  'VPE_CANONICAL_BYTES')
        else:
            _need(item is None or type(item) is bool, 'VPE_SCHEMA')
    walk(value)
    try:
        _need(canonical(value) == raw, 'VPE_CANONICAL_BYTES')
    except (UnicodeError, OverflowError, ValueError) as exc:
        if type(exc) is ExportRefusal:
            raise
        raise ExportRefusal('VPE_CANONICAL_BYTES') from exc
    return value


@dataclass(frozen=True)
class OfflineInputSnapshot:
    """Immutable byte identity and derived identities; no validation claim."""
    raws: tuple[bytes, ...]
    refs: tuple[tuple[str, str, int], ...]
    bundle_sha256: str
    event_identities: tuple[bytes, ...]
    schedule_sha256: str
    flags_sha256: str


def inspect_supplied_inputs(raw_by_role):
    """Check all seven supplied roles and derive a closed untrusted snapshot.

    This deliberately has no repo/object-root/runtime argument and performs
    no I/O.  The caller's hashes are never treated as authenticated pins.
    """
    try:
        return _inspect_supplied_inputs(raw_by_role)
    except ExportRefusal:
        raise
    except (KeyError, IndexError, TypeError, ValueError, OverflowError,
            RecursionError) as exc:
        raise ExportRefusal('VPE_SCHEMA') from exc


def _inspect_supplied_inputs(raw_by_role):
    _exact(raw_by_role, ROLES)
    raws = tuple(raw_by_role[role] for role in ROLES)
    _need(all(type(raw) is bytes and 0 < len(raw) <= limit
              for raw, limit in zip(raws, LIMITS)) and
          sum(map(len, raws)) <= MAX_TOTAL, 'VPE_INPUT_BOUNDS')
    values = tuple(_parse(raw, limit) for raw, limit in zip(raws, LIMITS))
    manifest, review, pins, context, precedence, policy, policy_review = values
    refs = {role: {'sha256': hashlib.sha256(raw).hexdigest(),
                   'byte_length': len(raw), 'media_type': 'application/json'}
            for role, raw in zip(ROLES, raws)}
    _exact(manifest, ('identity', 'code', 'protocol', 'storage', 'cohort', 'time',
                      'sources', 'runs_and_slots', 'network', 'limits', 'schedule',
                      'clocks_and_receipts', 'accounting', 'runtime'))
    _need(manifest['identity'].get('schema') == 'R09_GATE3_LAUNCH_MANIFEST_V4',
          'VPE_SCHEMA')
    _exact(review, ('schema_version', 'manifest_sha256', 'window_sha256',
                    'request_schedule_sha256', 'event_schedule_sha256',
                    'supplemental_pins_sha256', 'terminal_precedence_sha256',
                    'runtime_context_sha256'))
    _need(type(review['schema_version']) is int and review['schema_version'] == 2 and
          review['manifest_sha256'] == refs['manifest']['sha256'] and
          review['supplemental_pins_sha256'] == refs['supplemental_pins']['sha256'] and
          review['terminal_precedence_sha256'] == refs['terminal_precedence']['sha256'] and
          review['runtime_context_sha256'] == refs['runtime_context']['sha256'],
          'VPE_PROJECTION_MISMATCH')
    _exact(context, ('manifest_runtime_sha256', 'boot_id', 'shared_root',
                     'session_root', 'budget_root', 'store_descriptor', 'report_root',
                     'store_policy', 'clock_method', 'allowed_peer_ips'))
    _need(context['manifest_runtime_sha256'] ==
          hashlib.sha256(canonical(manifest['runtime'])).hexdigest() and
          context['store_policy'] == manifest['runtime']['policy']['sha256'],
          'VPE_PROJECTION_MISMATCH')
    _need(all(type(context[k]) is list and len(context[k]) == 2 and
              all(type(n) is int and 0 <= n <= 2**63 - 1 for n in context[k])
              for k in ('shared_root', 'session_root', 'budget_root',
                        'report_root')) and
          type(context['boot_id']) is str and 0 < len(context['boot_id']) <= 4096 and
          type(context['clock_method']) is str and
          0 < len(context['clock_method']) <= 4096 and
          type(context['allowed_peer_ips']) is list and
          len(context['allowed_peer_ips']) <= 64 and
          all(type(ip) is str for ip in context['allowed_peer_ips']),
          'VPE_SCHEMA')
    _need(type(precedence) is list and len(precedence) == 7 and
          all(type(x) is str for x in precedence) and
          set(precedence) == PRECEDENCE, 'VPE_SCHEMA')
    _exact(policy, ('schema', 'manifest_sha256', 'entries'))
    _need(policy['schema'] == 'ALPHA_V11_GATE3_EVENT_EXPORT_POLICY_V1' and
          policy['manifest_sha256'] == refs['manifest']['sha256'],
          'VPE_EVENT_IDENTITY')
    _exact(policy_review, ('schema', 'status', 'event_policy_sha256',
                           'manifest_sha256', 'producer_commit_oid',
                           'producer_tree_oid', 'reviewer_id', 'reviewer_model',
                           'completed_utc'))
    _need(policy_review['schema'] ==
          'ALPHA_V11_GATE3_EVENT_EXPORT_POLICY_REVIEW_V1' and
          policy_review['status'] == 'ACCEPTED' and
          policy_review['event_policy_sha256'] == refs['event_policy']['sha256'] and
          policy_review['manifest_sha256'] == refs['manifest']['sha256'],
          'VPE_REVIEW_NOT_ACCEPTED')
    for key in ('producer_commit_oid', 'producer_tree_oid'):
        _need(type(policy_review[key]) is str and
              re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}',
                           policy_review[key]) is not None, 'VPE_SCHEMA')
    for key in ('reviewer_id', 'reviewer_model'):
        _need(type(policy_review[key]) is str and
              0 < len(policy_review[key]) <= 4096, 'VPE_SCHEMA')
    _integer(policy_review['completed_utc'], 1)
    cohort, schedule, runs = (manifest[k] for k in
                              ('cohort', 'schedule', 'runs_and_slots'))
    requests = schedule['requests']
    _need(type(requests) is list and 0 < len(requests) <= 3600 and
          type(pins) is dict and len(pins) == len(requests),
          'VPE_SCHEDULE_COMPLETENESS')
    ids = []
    field_ids = []
    field_slots = []
    totals = {p: {'requests': 0, 'reservation_bytes': 0} for p in PURPOSES}
    endpoints = manifest['network']['endpoints']
    _need(type(endpoints) is list and len(endpoints) == 15, 'VPE_SCHEDULE_COMPLETENESS')
    endpoint_map = {e['endpoint_id']: e for e in endpoints}
    _need(len(endpoint_map) == 15 and
          {(e['provider'], e['purpose']) for e in endpoints} ==
          {(p, purpose) for p in PROVIDERS for purpose in PURPOSES},
          'VPE_SCHEDULE_COMPLETENESS')
    slots = runs['slots']
    _need(type(slots) is list and len(slots) == SLOT_COUNT and
          slots == _slot_inventory(runs['run_utc']) and
          schedule['full_denominator'] == SLOT_COUNT and
          schedule['slot_inventory_sha256'] == hashlib.sha256(canonical(slots)).hexdigest(),
          'VPE_SCHEDULE_COMPLETENESS')
    projected_requests = []
    dependency_total = prerequisite_total = 0
    field_started = False
    for position, request in enumerate(requests):
        _exact(request, ('request_id', 'purpose', 'slot_index', 'provider', 'origin',
                         'path', 'object_id', 'index_id', 'cache_id', 'endpoint_id',
                         'range_start', 'range_end', 'reservation_bytes',
                         'prerequisites'), 'VPE_SCHEDULE_COMPLETENESS')
        rid, purpose = request['request_id'], request['purpose']
        _need(type(rid) is str and ID.fullmatch(rid) is not None and rid not in ids and
              purpose in PURPOSES and type(request['slot_index']) is int and
              0 <= request['slot_index'] < SLOT_COUNT and
              type(request['prerequisites']) is list and
              len(request['prerequisites']) <= 256 and
              len(set(request['prerequisites'])) == len(request['prerequisites']) and
              all(type(i) is int and 0 <= i < position for i in request['prerequisites']),
              'VPE_SCHEDULE_COMPLETENESS')
        _need(len(canonical(request)) <= 8192 and
              (purpose == 'FIELD' or not field_started),
              'VPE_SCHEDULE_COMPLETENESS')
        prerequisite_total += len(request['prerequisites'])
        _need(prerequisite_total <= 16384, 'VPE_INPUT_BOUNDS')
        ids.append(rid)
        _need(request['provider'] == slots[request['slot_index']][0] and
              request['endpoint_id'] in endpoint_map and
              all(request[k] == endpoint_map[request['endpoint_id']][k]
                  for k in ('provider', 'purpose', 'origin')),
              'VPE_SCHEDULE_COMPLETENESS')
        _exact(pins.get(rid), ('expected_etag', 'expected_object_bytes',
                               'dependency_commit_hashes'), 'VPE_PROJECTION_MISMATCH')
        supplemental = pins[rid]
        _need(type(supplemental['dependency_commit_hashes']) is list and
              len(supplemental['dependency_commit_hashes']) <= 256 and
              all(type(h) is str and HEX.fullmatch(h) is not None
                  for h in supplemental['dependency_commit_hashes']) and
              len(set(supplemental['dependency_commit_hashes'])) ==
              len(supplemental['dependency_commit_hashes']),
              'VPE_PROJECTION_MISMATCH')
        dependency_total += len(supplemental['dependency_commit_hashes'])
        _need(dependency_total <= 8192, 'VPE_INPUT_BOUNDS')
        _need(type(request['reservation_bytes']) is int and
              0 < request['reservation_bytes'] <= 2**63 - 1,
              'VPE_PURPOSE_BUDGET')
        totals[purpose]['requests'] += 1
        totals[purpose]['reservation_bytes'] += request['reservation_bytes']
        if purpose == 'FIELD':
            field_started = True
            field_ids.append(rid)
            field_slots.append(request['slot_index'])
        source = manifest['sources'][request['provider']]
        endpoint = endpoint_map[request['endpoint_id']]
        projected_requests.append({
            'request_id': rid, 'purpose': purpose,
            'endpoint_id': request['endpoint_id'],
            'control_domain_id': endpoint['control_domain_id'],
            'origin': request['origin'], 'path': request['path'],
            'provider': request['provider'] if purpose == 'FIELD' else None,
            'slot_index': request['slot_index'],
            'range_start': request['range_start'],
            'range_end': request['range_end'],
            'reservation_bytes': request['reservation_bytes'],
            'expected_etag': supplemental['expected_etag'],
            'expected_object_bytes': supplemental['expected_object_bytes'],
            'source_pin': source['dossier']['sha256'],
            'decoder_pin': source['decoder_build']['sha256'],
            'clock_policy_sha256': manifest['runtime']['clock_policy']['sha256'],
            'validator_sha256': endpoint['parser_identity']['sha256'],
            'dependency_commit_hashes': supplemental['dependency_commit_hashes'],
            'prerequisite_request_ids': [ids[i] for i in request['prerequisites']]})
    _need(set(pins) == set(ids) and
          field_slots == schedule['attempt_slots'] and
          sum(v['reservation_bytes'] for v in totals.values()) ==
          schedule['reservation_total_bytes'] and
          totals == manifest['runtime']['purpose_plan'],
          'VPE_PURPOSE_BUDGET')
    _need(type(cohort['events']) is list and 1 <= len(cohort['events']) <= 2 and
          len(cohort['events']) == len(cohort['requested_keys']) ==
          len(cohort['gate2_trial_keys']) == len(policy['entries']) and
          len(set(cohort['events'])) == len(cohort['events']) and
          0 < len(field_ids) <= SLOT_COUNT and
          len(field_ids) * len(cohort['events']) <= 5426,
          'VPE_EVENT_COMPLETENESS')
    identities = []
    projected_events = []
    seen = set()
    for ordinal, (side, key, trial, entry) in enumerate(zip(
            cohort['events'], cohort['requested_keys'],
            cohort['gate2_trial_keys'], policy['entries'])):
        _exact(entry, ('requested_key', 'primary_provider'), 'VPE_EVENT_IDENTITY')
        _need(side in ('HIGH', 'LOW') and type(key) is list and len(key) == 5 and
              all(type(x) is str and x for x in key) and
              type(trial) is list and trial == [key[0], key[1], key[3]] and
              entry['requested_key'] == key and
              key[0] == cohort['station_id'] and key[3] == cohort['target_date'] and
              key[4] == ('daily_high_temperature' if side == 'HIGH'
                         else 'daily_low_temperature') and
              ID.fullmatch(key[1]) is not None and key[1] not in seen and
              entry['primary_provider'] in
              {r['provider'] for r in requests if r['purpose'] == 'FIELD'},
              'VPE_EVENT_IDENTITY')
        seen.add(key[1])
        identity = {'ordinal': ordinal, 'event_id': key[1], 'side': side,
                    'primary_provider': entry['primary_provider'],
                    'requested_key': key, 'gate2_trial_key': trial,
                    'field_request_ids': field_ids}
        for name in ('station_id', 'station_version', 'latitude', 'longitude',
                     'timezone', 'tzdata', 'target_date', 'units', 'rounding',
                     'buckets', 'rule', 'settlement', 'metadata', 'selection',
                     'city_day'):
            identity[name] = cohort[name]
        for name in ('local_day_start_utc', 'local_day_end_utc', 'decision_utc',
                     'decision_lower_utc'):
            identity[name] = manifest['time'][name]
        identities.append(canonical(identity))
        projected_events.append({name: identity[name] for name in
                                 ('event_id', 'side', 'primary_provider',
                                  'field_request_ids', 'requested_key',
                                  'gate2_trial_key')})
    times, limits = manifest['time'], manifest['limits']
    window = {'start_utc': times['window_start_utc'],
              'acquisition_end_utc': times['last_acquisition_utc'],
              'decision_lower_utc': times['decision_lower_utc'],
              'request_deadline_seconds': limits['request_deadline_seconds'],
              'elapsed_cap_seconds': limits['max_elapsed_seconds'],
              'uncertainty_cap_seconds': times['uncertainty_seconds']}
    _need(review['window_sha256'] == digest_value(window) and
          review['request_schedule_sha256'] == digest_value(projected_requests) and
          review['event_schedule_sha256'] == digest_value(projected_events),
          'VPE_PROJECTION_MISMATCH')
    bundle = {'schema': 'ALPHA_V11_GATE3_PLAN_EXPORT_INPUTS_V1', 'refs': refs}
    return OfflineInputSnapshot(raws, tuple((role, refs[role]['sha256'],
                                            refs[role]['byte_length']) for role in ROLES),
                                hashlib.sha256(canonical(bundle)).hexdigest(),
                                tuple(identities),
                                hashlib.sha256(canonical(schedule)).hexdigest(),
                                hashlib.sha256(canonical(dict(FLAGS))).hexdigest())


def consume_accepted_export(*, export_raw, custody_raw, acceptance_raw,
                            authenticated_pin_channel: AuthenticatedPinChannel | None = None):
    """Fail closed until a reviewed producer hook and trusted-pin issuer exist.

    An untrusted caller may submit any bytes or mapping.  No object accepted
    by this module can mint a trusted decision or an accepted resource report.
    """
    for raw, limit in ((export_raw, 32 * 1024**2), (custody_raw, 16384),
                       (acceptance_raw, 16384)):
        _parse(raw, limit)
    # No reviewed issuer, independent verifier, or producer export hook is
    # installed here.  Python objects supplied by callers cannot be trusted
    # merely because they implement resolve_exact; do not invoke them.
    _ = authenticated_pin_channel
    raise ExportRefusal('VPE_EXTERNAL_TRUST_UNAVAILABLE')


def digest_value(value):
    return hashlib.sha256(canonical(value)).hexdigest()
