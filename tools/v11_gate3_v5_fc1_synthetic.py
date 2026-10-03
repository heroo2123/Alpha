"""Closed, explicitly synthetic FC1 schema/projection exercise.

This is not ManifestV5 validation or an interpretation/capability registry. The
fixture facts below are a test language; every result is untrusted and no-credit.
The production FC1 consumer in the companion module unconditionally refuses.
All work is over supplied bytes. No imported legacy validator can dispatch/I/O.
"""
from __future__ import annotations

from dataclasses import dataclass

from tools.v11_gate3_v5_fc1 import (
    AllocationPlan, ExecutionProfile, FIELDS, GIB, INPUT_ROLES, MIB, PROVIDERS,
    PURPOSES, ROLES, RecordPlan, Ref, Refusal, SYNTHETIC, TimingPlan, canonical,
    CostBounds, derive_costs, digest, exact, hash_value, ident, integer, need, parse, total,
)

GATES = ('H1', 'H2', 'H3', 'H4', 'H5', 'H6', 'A1', 'A2', 'A3', 'A4', 'A5',
         'A6', 'A7', 'A8', 'G3-L', 'G3-E')
PHASES = ('setup', 'parse_hash', 'import_verify', 'reservation', 'clock', 'fsync',
          'raw_seal', 'decode_success', 'decode_failure', 'graph', 'replay',
          'snapshots', 'recovery', 'finalize', 'jitter', 'clock_guard')
# Canonical exact inherited map, not just a caller-provided set/count.
PRESERVATION_SHA256 = 'a4db64a5c41675866d093ac94fb26f7dda450f26ad8c3e9c84b3368b7bb31a4a'


@dataclass(frozen=True)
class SyntheticSnapshot:
    projection: bytes
    profile: ExecutionProfile
    timing: TimingPlan
    allocation: AllocationPlan
    records: RecordPlan
    costs: CostBounds
    input_refs: tuple[Ref, ...]
    closure_refs: tuple[Ref, ...]
    qualification_credit: int = 0


def slot_inventory(runs):
    exact(runs, PROVIDERS)
    for p in PROVIDERS:
        integer(runs[p], 1)
        need(runs[p] % 86400 == 0, 'APPLICABILITY')
    return [[p, runs[p], m, h] for p, members, cadence in
            (('GEFS', 31, 3), ('IFS', 51, 3), ('AIFS', 51, 6))
            for m in range(members) for h in range(0, 73, cadence)]


def reference(raw):
    return {'sha256': digest(raw), 'byte_length': len(raw), 'media_type': 'application/json'}


def _check_preservation(value):
    exact(value, ('identities', 'missing', 'gates', 'credit', 'g3l', 'g3e'))
    need(type(value['credit']) is int and value['credit'] == 0 and
         value['g3l'] == 'NO_GO' and value['g3e'] == 'GATED' and value['missing'] == 77,
         'TRUST')
    need(value['gates'] == list(GATES), 'PROJECTION')
    need(digest(canonical(value['identities'])) == PRESERVATION_SHA256, 'PROJECTION')


def _clock(v, elapsed):
    exact(v, ('now_ms', 'preregistration_ms', 'review_ms', 'start_ms', 'end_ms',
              'decision_lower_ms', 'target_start_ms', 'metadata_upper_ms',
              'feature_upper_ms', 'uncertainty_ms', 'restriction_expiry_ms',
              'unresolved_denial', 'retry', 'credentials', 'redirect'))
    for k in ('now_ms', 'preregistration_ms', 'review_ms', 'start_ms', 'end_ms',
              'decision_lower_ms', 'target_start_ms', 'metadata_upper_ms',
              'feature_upper_ms', 'uncertainty_ms'):
        integer(v[k])
    for k in ('unresolved_denial', 'retry', 'credentials', 'redirect'):
        need(type(v[k]) is bool)
        need(v[k] is False, 'RIGHTS_LINEAGE')
    need(type(v['restriction_expiry_ms']) is int and
         v['restriction_expiry_ms'] <= v['start_ms'], 'RIGHTS_LINEAGE')
    need(v['uncertainty_ms'] <= 1000 and
         v['now_ms'] <= v['review_ms'] and
         v['preregistration_ms'] <= v['review_ms'] < v['start_ms'] and
         v['metadata_upper_ms'] < v['start_ms'] and
         v['end_ms'] - v['start_ms'] == 10800000 and
         v['start_ms'] + elapsed <= v['end_ms'] < v['decision_lower_ms'] < v['target_start_ms'] and
         v['feature_upper_ms'] <= v['decision_lower_ms'], 'CLOCK')


def inspect_synthetic(input_bytes, artifact_bytes):
    """Deterministic full-cohort synthetic necessary-boundary projection only.

    Both mappings must be ordinary dicts of bytes. Values are snapshotted and
    resolved by digest, never by path/URI/callback. No authentication is inferred.
    """
    try:
        return _inspect(input_bytes, artifact_bytes)
    except Refusal:
        raise
    except (TypeError, KeyError, IndexError, AttributeError, ValueError, OverflowError):
        raise Refusal('SCHEMA') from None


def _inspect(input_bytes, artifact_bytes):
    exact(input_bytes, INPUT_ROLES)
    need(type(artifact_bytes) is dict and len(artifact_bytes) <= 4096, 'BOUNDS')
    raws = tuple(input_bytes[k] for k in INPUT_ROLES)
    limits = (32*MIB, 4096, 2*MIB, 4096, 4096, 65536, 16384)
    need(all(type(raw) is bytes and 0 < len(raw) <= limit for raw, limit in zip(raws, limits)), 'BOUNDS')
    need(total(map(len, raws)) <= 40*MIB, 'BOUNDS')
    # This step has no streaming reader: its smaller supplied-byte ceiling is
    # intentional. The 1 GiB artifact limit does not permit materializing it here.
    need(all(type(k) is str and type(v) is bytes for k, v in artifact_bytes.items()))
    need(total(map(len, artifact_bytes.values())) <= 32*MIB, 'BOUNDS')
    artifacts = dict(artifact_bytes)
    for key, raw in artifacts.items():
        hash_value(key)
        need(digest(raw) == key, 'SOURCE_PIN')
    values = tuple(parse(raw, limit) for raw, limit in zip(raws, limits))
    manifest, review, pins, context, precedence, policy, policy_review = values
    exact(manifest, ('schema', 'runs', 'slots', 'requests', 'provider_caps', 'events',
                     'roles', 'execution_profile', 'abort_bytes', 'clock', 'preservation'))
    need(manifest['schema'] == 'G3_V5_FC1_SYNTHETIC_MANIFEST_1')
    profile = ExecutionProfile.read(canonical(manifest['execution_profile']))
    resolved = {}

    def resolve(ref):
        need(ref.sha256 in artifacts, 'DEPENDENCY')
        raw = artifacts[ref.sha256]
        ref.verify(raw)
        need(ref.media_type == 'application/json')
        resolved[ref.sha256] = ref
        return raw

    plans = [resolve(r) for r in profile.refs()]
    allocation = AllocationPlan.read(plans[5])
    records = RecordPlan.read(plans[6])
    timing = TimingPlan.read(plans[7])
    # Reviews are synthetic exact bytes, with no credential/trust authority.
    certs = [parse(r) for r in plans[:5]]
    for cert, owner in zip(certs, ('scheduler', 'store', 'ledger', 'stream', 'native')):
        exact(cert, ('schema', 'owner', 'phases', 'successful_path', 'build', 'dependencies'))
        need(cert['schema'] == 'G3_V5_FC1_SYNTHETIC_COST_CERTIFICATE_1' and cert['owner'] == owner, 'TRUST')
        need(cert['successful_path'] is True and cert['phases'] == list(PHASES), 'BUDGET')
        for ref in [cert['build'], *cert['dependencies']]:
            resolve(Ref.read(ref))
    need(tuple(p.id for p in timing.phase_bounds) == PHASES, 'BUDGET')
    for p in timing.phase_bounds:
        need(p.wall_ms > 0, 'BUDGET')
        need(p.review == profile.scheduler_review, 'SOURCE_PIN')
    for a in allocation.allocations:
        need(a.last_phase < len(PHASES), 'BUDGET')
        need(a.review == profile.store_review, 'SOURCE_PIN')
    for j in records.journals:
        for t in j.types:
            need(t.review == profile.ledger_review, 'SOURCE_PIN')

    _check_preservation(manifest['preservation'])
    slots = slot_inventory(manifest['runs'])
    need(manifest['slots'] == slots, 'APPLICABILITY')
    exact(manifest['provider_caps'], PROVIDERS)
    for p, maximum in zip(PROVIDERS, (2*MIB, 4*MIB, 4*MIB)):
        integer(manifest['provider_caps'][p], 1, maximum, 'BUDGET')
    requests = manifest['requests']
    need(type(requests) is list)
    integer(len(requests), FIELDS, 3600, 'BUDGET')
    ids, field_ids, caps, purpose_plan = [], [], [], {p: [0, 0] for p in PURPOSES}
    for r in requests:
        exact(r, ('id', 'purpose', 'slot', 'provider', 'cap', 'reservation', 'start',
                  'end', 'deadline_ms', 'read_calls', 'object_id', 'index_id'))
        ident(r['id'])
        need(r['id'] not in ids, 'APPLICABILITY')
        ids.append(r['id'])
        need(r['purpose'] in PURPOSES and r['provider'] in PROVIDERS)
        integer(r['slot'], 0, FIELDS-1, 'APPLICABILITY')
        need(r['provider'] == slots[r['slot']][0], 'APPLICABILITY')
        integer(r['cap'], 1, 4*MIB, 'BUDGET')
        integer(r['reservation'], 1)
        need(r['reservation'] == r['cap'], 'BUDGET')
        integer(r['deadline_ms'], 1000, 30000)
        hash_value(r['object_id'])
        hash_value(r['index_id'])
        if r['purpose'] == 'FIELD':
            need(r['slot'] == len(field_ids), 'APPLICABILITY')
            need(r['cap'] == manifest['provider_caps'][r['provider']], 'BUDGET')
            integer(r['start'], 0, GIB-1, 'BUDGET')
            integer(r['end'], r['start'], GIB-1, 'BUDGET')
            need(r['end']-r['start']+1 <= r['cap'], 'BUDGET')
            field_ids.append(r['id'])
        else:
            need(not field_ids and r['start'] is None and r['end'] is None, 'APPLICABILITY')
            need(r['cap'] <= (3*MIB if r['purpose'] == 'INDEX' else 4*MIB), 'BUDGET')
        integer(r['read_calls'], (r['cap']+65535)//65536, min(r['cap']+1, 65536), 'BUDGET')
        caps.append(r['cap'])
        purpose_plan[r['purpose']][0] += 1
        purpose_plan[r['purpose']][1] += r['cap']
    need(len(field_ids) == FIELDS, 'APPLICABILITY')
    need(tuple(r['deadline_ms'] for r in requests) == timing.deadline_ms, 'BUDGET')
    events = manifest['events']
    need(type(events) is list and 1 <= len(events) <= 2, 'PROJECTION')
    seen = []
    for e in events:
        exact(e, ('event_id', 'kind', 'requested_key', 'trial_key', 'field_ids', 'providers', 'eligible'))
        ident(e['event_id'])
        need(e['kind'] in ('HIGH', 'LOW') and e['kind'] not in seen, 'PROJECTION')
        seen.append(e['kind'])
        need(e['field_ids'] == field_ids and e['providers'] == list(PROVIDERS) and e['eligible'] is False, 'PROJECTION')
        key = e['requested_key']
        need(type(key) is list and len(key) == 5 and key[1] == e['event_id'] and
             key[4] == ('daily_high_temperature' if e['kind'] == 'HIGH' else 'daily_low_temperature') and
             e['trial_key'] == [key[0], key[1], key[3]], 'PROJECTION')
    if len(events) == 2:
        need(all(events[0]['requested_key'][i] == events[1]['requested_key'][i] for i in (0, 2, 3)), 'PROJECTION')

    roles = manifest['roles']
    need(type(roles) is list and len(roles) == FIELDS, 'APPLICABILITY')
    role_edges = []
    proof_cache = {}
    cohort_digest = digest(canonical(slots))
    scoped_uses = {}
    for index, (row, request) in enumerate(zip(roles, requests[-FIELDS:])):
        exact(row, ('field_id',) + ROLES)
        need(row['field_id'] == request['id'], 'DEPENDENCY')
        edges = []
        for role in ROLES:
            use = row[role]
            exact(use, ('proof', 'mode', 'confirmation'))
            ref = Ref.read(use['proof'])
            proof_raw = resolve(ref)
            if ref.sha256 not in proof_cache:
                proof_cache[ref.sha256] = parse(proof_raw)
            proof = proof_cache[ref.sha256]
            exact(proof, ('schema', 'role', 'slots', 'cohort', 'object_id', 'index_id',
                          'observed_upper_ms', 'valid_from_ms', 'valid_until_ms',
                          'rights_until_ms', 'parser', 'source_kind'))
            need(proof['schema'] == 'G3_V5_FC1_SYNTHETIC_ROLE_FACT_1')
            need(proof['role'] == role and index in proof['slots'], 'APPLICABILITY')
            need(proof['slots'] == sorted(set(proof['slots'])) and
                 all(type(i) is int and 0 <= i < FIELDS for i in proof['slots']), 'APPLICABILITY')
            need(proof['cohort'] == cohort_digest, 'APPLICABILITY')
            scoped_uses.setdefault(ref.sha256, set()).add(index)
            need(proof['object_id'] == request['object_id'] and proof['index_id'] == request['index_id'], 'DEPENDENCY')
            need(proof['source_kind'] == ('OFFICIAL_COHORT' if role == ROLES[2] else 'NATIVE'), 'APPLICABILITY')
            need(proof['parser'] == 'SYNTHETIC_ONLY', 'TRUST')
            for k in ('observed_upper_ms', 'valid_from_ms', 'valid_until_ms', 'rights_until_ms'):
                integer(proof[k])
            clock = manifest['clock']
            need(proof['observed_upper_ms'] < clock['review_ms'] and
                 proof['valid_from_ms'] <= clock['review_ms'] and
                 proof['valid_until_ms'] >= clock['end_ms'], 'CLOCK')
            need(proof['rights_until_ms'] >= clock['end_ms'], 'RIGHTS_LINEAGE')
            need(use['mode'] in ('SEALED_OFFLINE', 'SCHEDULED_CONFIRMATION'))
            if use['mode'] == 'SEALED_OFFLINE':
                need(use['confirmation'] is None, 'DEPENDENCY')
            else:
                need(use['confirmation'] in ids[:-FIELDS], 'DEPENDENCY')
                confirmation = requests[ids.index(use['confirmation'])]
                need(confirmation['purpose'] == dict(zip(ROLES, ('INDEX', 'OBJECT_ID', 'METADATA')))[role] and
                     confirmation['object_id'] == request['object_id'] and
                     confirmation['index_id'] == request['index_id'], 'DEPENDENCY')
            edges.append(ref.sha256)
        role_edges.append(edges)

    for h, used in scoped_uses.items():
        need(used == set(proof_cache[h]['slots']), 'APPLICABILITY')

    costs = derive_costs(tuple(caps), manifest['abort_bytes'], timing, allocation, records, len(events))
    _clock(manifest['clock'], costs.elapsed_ms)
    closure = parse(resolve(allocation.closure))
    exact(closure, ('schema', 'nodes', 'artifacts', 'required_allocations'))
    need(closure['schema'] == 'G3_V5_FC1_SYNTHETIC_CLOSURE_1')
    # Every physical object has a node, and every semantic edge survives packing.
    nodes = closure['nodes']
    need(type(nodes) is list and len(nodes) == len(allocation.objects), 'DEPENDENCY')
    need([n.get('id') for n in nodes] == [o.id for o in allocation.objects], 'DEPENDENCY')
    adjacency = {}
    external = edges_count = 0
    for n in nodes:
        exact(n, ('id', 'dependencies', 'external'))
        need(type(n['dependencies']) is list and type(n['external']) is list)
        need(len(n['dependencies']) + len(n['external']) <= 256, 'BOUNDS')
        need(len(set(n['dependencies'])) == len(n['dependencies']) and len(set(n['external'])) == len(n['external']), 'DEPENDENCY')
        for h in n['external']:
            hash_value(h)
            need(h in artifacts, 'DEPENDENCY')
            resolve(Ref.read(reference(artifacts[h])))
        adjacency[n['id']] = n['dependencies']
        external += len(n['external'])
        edges_count += len(n['external']) + len(n['dependencies'])
    need(external <= 8192 and edges_count <= 16384, 'BUDGET')
    nodes_by_id = {n['id']: n for n in nodes}
    for field_id, expected in zip(field_ids, role_edges):
        need(field_id in nodes_by_id, 'DEPENDENCY')
        node = nodes_by_id[field_id]
        need(node['external'] == expected, 'DEPENDENCY')
    done = set()
    active = set()
    # Iterative DFS avoids recursion limits and checks the whole inventory.
    for root in adjacency:
        stack = [(root, False)]
        while stack:
            node, leaving = stack.pop()
            need(node in adjacency, 'DEPENDENCY')
            if leaving:
                active.remove(node)
                done.add(node)
            elif node not in done:
                need(node not in active, 'DEPENDENCY')
                active.add(node)
                stack.append((node, True))
                stack.extend((child, False) for child in reversed(adjacency[node]))
    # Each event's reachable RAW leaves must be the original ordered cohort.
    kinds = {o.id: o.kind for o in allocation.objects}
    for event in events:
        root = 'event_' + event['event_id']
        need(root in adjacency and kinds[root] == 'AGGREGATE', 'DEPENDENCY')
        leaves, stack, visited = [], [root], set()
        while stack:
            node = stack.pop()
            need(node not in visited, 'DEPENDENCY')
            visited.add(node)
            if kinds[node] == 'RAW':
                leaves.append(node)
            else:
                stack.extend(reversed(adjacency[node]))
            need(len(leaves) <= FIELDS and len(stack) <= 16384, 'BOUNDS')
        need(leaves == field_ids, 'DEPENDENCY')
    artifact_refs = tuple(Ref.read(r) for r in closure['artifacts'])
    need(len({r.sha256 for r in artifact_refs}) == len(artifact_refs), 'DEPENDENCY')
    for r in artifact_refs:
        resolve(r)
    imports = [o for o in allocation.objects if o.kind in ('IMPORT', 'CATALOG')]
    need(len(imports) == len(artifact_refs) and
         [o.bytes for o in imports] == [r.byte_length for r in artifact_refs], 'DEPENDENCY')
    # Declared artifacts are actual bytes, including proof/certificate/build
    # closure. Plans and the closure itself are separately held snapshots.
    plan_hashes = {digest(r) for r in plans[5:]} | {allocation.closure.sha256}
    need(set(resolved) - plan_hashes == {r.sha256 for r in artifact_refs}, 'DEPENDENCY')
    need(set(artifacts) == set(resolved), 'DEPENDENCY')
    # Bind every allocation to its exact required purpose, minimum bytes and
    # reviewed domain; no missing source copies, reports, journals or decoder.
    required = closure['required_allocations']
    need(type(required) is list)
    minimums = {'raw': costs.body_bytes, 'temp': max(max(caps), 4*MIB),
                'imports': total(r.byte_length for r in artifact_refs),
                'source_copies': total(map(len, artifacts.values())),
                'outputs': total(o.bytes for o in allocation.objects if o.kind in ('AGGREGATE', 'DECODED')),
                'report': 16*MIB, 'emergency': 65536, 'metadata': 4096,
                'snapshots': 2*total((*map(len, raws), *map(len, artifacts.values()))), 'decoder_parent': 1,
                'decoder_child': 1, 'stream_buffer': 65536, 'parser': MIB,
                'indexes': 1, 'state': 1}
    minimums.update({j.kind+'_journal': j.bounds()[1] for j in records.journals})
    need([a.id for a in allocation.allocations] == list(minimums), 'BUDGET')
    need([r.get('id') for r in required] == list(minimums), 'BUDGET')
    for a, r in zip(allocation.allocations, required):
        exact(r, ('id', 'domain', 'bytes'))
        need(r['domain'] == a.domain and r['bytes'] == a.bytes and a.bytes >= minimums[a.id], 'BUDGET')

    # Seven exact input roles bind original events, role modes and the profile.
    exact(review, ('schema', 'manifest_sha256', 'profile_sha256', 'producer', 'reviewer', 'checkpoint'))
    need(review['schema'] == 'G3_V5_FC1_SYNTHETIC_REVIEW_1')
    need(review['producer'] == 'fixture_producer' and review['reviewer'] == 'fixture_reviewer' and
         review['checkpoint'] == 'fixture_current', 'TRUST')
    need(review['manifest_sha256'] == digest(raws[0]), 'PROJECTION')
    need(review['profile_sha256'] == digest(profile.raw), 'SOURCE_PIN')
    exact(pins, ('schema', 'roles_sha256', 'requests_sha256'))
    need(pins['schema'] == 'G3_V5_FC1_SYNTHETIC_PINS_1')
    need(pins['roles_sha256'] == digest(canonical(roles)), 'DEPENDENCY')
    need(pins['requests_sha256'] == digest(canonical(requests)), 'PROJECTION')
    exact(context, ('schema', 'profile_sha256', 'build'))
    need(context['schema'] == 'G3_V5_FC1_SYNTHETIC_CONTEXT_1')
    need(context['profile_sha256'] == digest(profile.raw) and context['build'] == certs[0]['build'], 'SOURCE_PIN')
    need(precedence == ['PREREQUISITE', 'CLOCK', 'RESOURCE', 'DENIAL', 'VALIDATION', 'SUCCESS', 'UNSCHEDULED'], 'PROJECTION')
    exact(policy, ('schema', 'events_sha256'))
    need(policy['schema'] == 'G3_V5_FC1_SYNTHETIC_EVENTS_1')
    need(policy['events_sha256'] == digest(canonical(events)), 'PROJECTION')
    exact(policy_review, ('schema', 'policy_sha256'))
    need(policy_review['schema'] == 'G3_V5_FC1_SYNTHETIC_EVENT_REVIEW_1')
    need(policy_review['policy_sha256'] == digest(raws[5]), 'PROJECTION')
    projection = canonical({'schema': SYNTHETIC, 'input_refs': {k: reference(v) for k, v in zip(INPUT_ROLES, raws)},
                            'manifest': manifest, 'purpose_plan': purpose_plan,
                            'execution_profile': parse(profile.raw),
                            'event_links': len(events)*FIELDS, 'provider_expansions': 3*len(events),
                            'role_cells': 3*FIELDS, 'qualification_credit': 0,
                            'authority': False, 'feature_eligibility': False})
    need(len(projection) <= 32*MIB, 'BOUNDS')
    return SyntheticSnapshot(projection, profile, timing, allocation, records, costs,
                             tuple(Ref.read(reference(r)) for r in raws), tuple(resolved.values()))


def refusal_rows(reason):
    """Fresh-plan diagnostic only; never edits/replaces runtime receipts/debits."""
    need(reason in ('BOUNDS', 'CANONICAL', 'SCHEMA', 'TRUST', 'SOURCE_PIN',
                    'APPLICABILITY', 'RIGHTS_LINEAGE', 'PROJECTION', 'BUDGET',
                    'CUSTODY', 'CLOCK', 'DEPENDENCY'))
    return tuple((i, reason, ('MISSING',)*3) for i in range(FIELDS))


def check_refusal_rows(rows, reason):
    need(type(rows) is tuple and rows == refusal_rows(reason), 'PROJECTION')
