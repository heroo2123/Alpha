"""FC01–06/FC19–24 step-2 pure boundaries; unittest works under python -O.

No provider facts, production qualification, scheduler/store/ledger execution.
"""
import builtins
from contextlib import ExitStack
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
import json
import os
from pathlib import Path
import socket
import subprocess
import unittest
from unittest.mock import patch

from tools import v11_gate3_v5_fc1 as fc
from tools import v11_gate3_v5_fc1_synthetic as syn

ROOT = Path(__file__).resolve().parents[1]
IDENTITIES = json.loads((ROOT/'docs/V11_GATE3_V5_FULL_COHORT_EXECUTION_CONTRACT_20261003.verification.json').read_bytes())['inherited_g3l_preservation']
PREDECESSOR = json.loads((ROOT/'docs/V11_GATE3_V5_EVIDENCE_ROLE_CONTRACT_20261003.verification.json').read_bytes())
MATRIX = json.loads((ROOT/'docs/V11_GATE3_V5_FULL_COHORT_EXECUTION_CONTRACT_20261003.verification.json').read_bytes())['future_matrix']


def fixture(events=2, confirmation=False):
    artifacts = {}

    def put(value):
        raw = fc.canonical(value)
        artifacts[fc.digest(raw)] = raw
        return syn.reference(raw)

    build = put({'schema': 'SYNTHETIC_BUILD', 'dependencies': [], 'code': 'offline-fixture'})
    reviews = [put({'schema': 'G3_V5_FC1_SYNTHETIC_COST_CERTIFICATE_1', 'owner': owner,
                    'phases': list(syn.PHASES), 'successful_path': True,
                    'build': build, 'dependencies': []})
               for owner in ('scheduler', 'store', 'ledger', 'stream', 'native')]
    run = 1788480000  # synthetic fixed 00Z; no current-run assertion
    run -= run % 86400
    runs = dict.fromkeys(fc.PROVIDERS, run)
    slots = syn.slot_inventory(runs)
    clock = dict(now_ms=1000, preregistration_ms=1000, review_ms=2000,
                 start_ms=3000, end_ms=10803000, decision_lower_ms=14403000,
                 target_start_ms=18003000, metadata_upper_ms=1999,
                 feature_upper_ms=10803000, uncertainty_ms=1000,
                 restriction_expiry_ms=999, unresolved_denial=False,
                 retry=False, credentials=False, redirect=False)
    proofs = {}
    for provider in fc.PROVIDERS:
        for role in fc.ROLES:
            proofs[provider, role] = put({
                'schema': 'G3_V5_FC1_SYNTHETIC_ROLE_FACT_1', 'role': role,
                'slots': [i for i, s in enumerate(slots) if s[0] == provider],
                'cohort': fc.digest(fc.canonical(slots)),
                'object_id': fc.digest(provider.encode()), 'index_id': fc.digest((provider+'index').encode()),
                'observed_upper_ms': 1999, 'valid_from_ms': 1000,
                'valid_until_ms': 10803000, 'rights_until_ms': 10803000,
                'parser': 'SYNTHETIC_ONLY',
                'source_kind': 'OFFICIAL_COHORT' if role == fc.ROLES[2] else 'NATIVE'})
    requests = [dict(id='f'+str(i), purpose='FIELD', slot=i, provider=s[0],
                     cap=262144, reservation=262144, start=0, end=262143,
                     deadline_ms=2000, read_calls=4, object_id=fc.digest(s[0].encode()),
                     index_id=fc.digest((s[0]+'index').encode())) for i, s in enumerate(slots)]
    ids = [r['id'] for r in requests]
    roles = [{'field_id': r['id'], **{role: {'proof': proofs[r['provider'], role],
               'mode': 'SEALED_OFFLINE', 'confirmation': None} for role in fc.ROLES}} for r in requests]
    if confirmation:
        overhead = {**requests[0], 'id': 'confirm_index', 'purpose': 'INDEX',
                    'cap': 512, 'reservation': 512, 'start': None, 'end': None, 'read_calls': 1}
        requests.insert(0, overhead)
        for row in roles[:775]:
            row[fc.ROLES[0]].update(mode='SCHEDULED_CONFIRMATION', confirmation='confirm_index')
    event_rows = [dict(event_id=kind.lower(), kind=kind,
                       requested_key=['station', kind.lower(), 'rule', '2026-10-04', 'daily_'+kind.lower()+'_temperature'],
                       trial_key=['station', kind.lower(), '2026-10-04'], field_ids=ids[:],
                       providers=list(fc.PROVIDERS), eligible=False) for kind in ('HIGH', 'LOW')[:events]]
    phases = [dict(id=name, owner='LOCAL' if i < 13 else fc.OWNERS[i-12],
                   wall_ms=(3500000-12 if i == 0 else 1) if i < 13 else (60000, 590000, 10000)[i-13],
                   cpu_ms=1 if i < 13 else 0, review=reviews[0]) for i, name in enumerate(syn.PHASES)]
    timing = dict(schema='G3_V5_TIMING_PLAN_1', deadline_ms=[2000]*len(requests),
                  spacing_ms=2000, local_wall_ms=3500000, local_cpu_ms=13,
                  finalization_ms=60000, jitter_ms=590000, clock_guard_ms=10000, phase_bounds=phases)
    nodes = [dict(id=r['id'], dependencies=[], external=([proofs[r['provider'], role]['sha256'] for role in fc.ROLES] if r['purpose'] == 'FIELD' else [])) for r in requests]
    objects = [dict(id=r['id'], kind='RAW', bytes=r['cap'], existing=False) for r in requests]
    for e in event_rows:
        children = []
        for i in range(0, fc.FIELDS, 256):
            name = 'group_'+e['event_id']+'_'+str(i)
            children.append(name)
            nodes.append(dict(id=name, dependencies=ids[i:i+256], external=[]))
            objects.append(dict(id=name, kind='AGGREGATE', bytes=16384, existing=False))
        name = 'event_'+e['event_id']
        nodes.append(dict(id=name, dependencies=children, external=[]))
        objects.append(dict(id=name, kind='AGGREGATE', bytes=16384, existing=False))
    for i in range(2):
        name = 'decoded_'+str(i)
        nodes.append(dict(id=name, dependencies=[], external=[]))
        objects.append(dict(id=name, kind='DECODED', bytes=4096, existing=False))
    support_refs = [syn.reference(raw) for raw in artifacts.values()]
    for i, ref in enumerate(support_refs):
        name = 'import_'+str(i)
        nodes.append(dict(id=name, dependencies=[], external=[]))
        objects.append(dict(id=name, kind='IMPORT', bytes=ref['byte_length'], existing=False))
    journals = []
    for kind, types, width in zip(fc.JOURNALS, fc.RECORD_TYPES, (1024, 2048, 4096, 8192)):
        journals.append(dict(kind=kind, existing_events=0, existing_bytes=0,
                             tail_bytes=16*fc.MIB if kind == 'store' else 0,
                             types=[dict(type=t, max_bytes=(1024 if kind == 'store' and t in ('FAILURE_ANNOTATION', 'LIFECYCLE') else width),
                                         max_remaining=4 if t == 'LIFECYCLE' else (len(objects) if kind == 'store' else len(requests)),
                                         review=reviews[2]) for t in types]))
    record = dict(schema='G3_V5_RECORD_PLAN_1', journals=journals)
    parsed_record = fc.RecordPlan.read(fc.canonical(record))
    imported = sum(r['byte_length'] for r in support_refs)
    needs = dict(raw=sum(r['cap'] for r in requests), temp=4*fc.MIB, imports=imported, source_copies=16*fc.MIB,
                 outputs=sum(o['bytes'] for o in objects if o['kind'] in ('AGGREGATE', 'DECODED')),
                 report=16*fc.MIB, emergency=65536, metadata=4096, snapshots=16*fc.MIB,
                 decoder_parent=fc.MIB, decoder_child=16*fc.MIB, stream_buffer=65536,
                 parser=fc.MIB, indexes=fc.MIB, state=fc.MIB)
    needs.update({j.kind+'_journal': j.bounds()[1] for j in parsed_record.journals})
    allocations = [dict(id=k, domain=fc.digest(('memory' if k in ('snapshots', 'decoder_parent', 'decoder_child', 'stream_buffer', 'parser', 'indexes', 'state') else 'disk').encode()),
                        bytes=v, first_phase=0, last_phase=15, review=reviews[1]) for k, v in needs.items()]
    closure = dict(schema='G3_V5_FC1_SYNTHETIC_CLOSURE_1', nodes=nodes, artifacts=support_refs,
                   required_allocations=[{k: a[k] for k in ('id', 'domain', 'bytes')} for a in allocations])
    allocation = dict(schema='G3_V5_ALLOCATION_PLAN_1', objects=objects, allocations=allocations, closure=put(closure))
    profile = dict(schema=fc.PROFILE, cost_model=fc.COST_MODEL,
                   **dict(zip(('scheduler_review', 'store_review', 'ledger_review', 'stream_review', 'native_review'), reviews)),
                   allocation_plan=put(allocation), record_plan=put(record), timing_plan=put(timing))
    manifest = dict(schema='G3_V5_FC1_SYNTHETIC_MANIFEST_1', runs=runs, slots=slots,
                    requests=requests, provider_caps=dict.fromkeys(fc.PROVIDERS, 262144),
                    events=event_rows, roles=roles, execution_profile=profile, abort_bytes=65536,
                    clock=clock, preservation=dict(identities=IDENTITIES, missing=77, gates=list(syn.GATES),
                                                   credit=0, g3l='NO_GO', g3e='GATED'))
    values = dict(manifest=manifest,
                  plan_review=dict(schema='G3_V5_FC1_SYNTHETIC_REVIEW_1', manifest_sha256='', profile_sha256='',
                                   producer='fixture_producer', reviewer='fixture_reviewer', checkpoint='fixture_current'),
                  supplemental_pins=dict(schema='G3_V5_FC1_SYNTHETIC_PINS_1', roles_sha256='', requests_sha256=''),
                  runtime_context=dict(schema='G3_V5_FC1_SYNTHETIC_CONTEXT_1', profile_sha256='', build=build),
                  terminal_precedence=['PREREQUISITE', 'CLOCK', 'RESOURCE', 'DENIAL', 'VALIDATION', 'SUCCESS', 'UNSCHEDULED'],
                  event_policy=dict(schema='G3_V5_FC1_SYNTHETIC_EVENTS_1', events_sha256=''),
                  event_policy_review=dict(schema='G3_V5_FC1_SYNTHETIC_EVENT_REVIEW_1', policy_sha256=''))
    return seal(values), artifacts


def seal(values):
    m = values['manifest']
    profile_hash = fc.digest(fc.canonical(m['execution_profile']))
    values['plan_review'].update(manifest_sha256=fc.digest(fc.canonical(m)), profile_sha256=profile_hash)
    values['runtime_context']['profile_sha256'] = profile_hash
    values['supplemental_pins'].update(roles_sha256=fc.digest(fc.canonical(m['roles'])), requests_sha256=fc.digest(fc.canonical(m['requests'])))
    values['event_policy']['events_sha256'] = fc.digest(fc.canonical(m['events']))
    values['event_policy_review']['policy_sha256'] = fc.digest(fc.canonical(values['event_policy']))
    return {k: fc.canonical(v) for k, v in values.items()}


def no_io():
    stack = ExitStack()
    for owner, name in ((builtins, 'open'), (os, 'open'), (os, 'system'), (os, 'getenv'),
                        (Path, 'open'), (socket, 'socket'), (socket, 'create_connection'),
                        (subprocess, 'Popen'), (subprocess, 'run')):
        stack.enter_context(patch.object(owner, name, side_effect=AssertionError('FC24 containment')))
    return stack


class FC1Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inputs, cls.artifacts = fixture()
        cls.values = {k: json.loads(v) for k, v in cls.inputs.items()}
        cls.manifest = cls.values['manifest']
        cls.profile = cls.manifest['execution_profile']

    def refuse(self, stage, fn, *args):
        with self.assertRaises(fc.Refusal) as ctx:
            fn(*args)
        self.assertEqual(ctx.exception.stage, stage)

    def mutate(self, change, stage, reseal=True):
        values = deepcopy(self.values)
        change(values)
        inputs = seal(values) if reseal else {k: fc.canonical(v) for k, v in values.items()}
        with no_io():
            self.refuse(stage, syn.inspect_synthetic, inputs, self.artifacts)

    def test_FC01_roundtrip_immutable_and_canonical(self):
        with no_io():
            snap = syn.inspect_synthetic(self.inputs, self.artifacts)
            self.assertEqual(fc.canonical(json.loads(snap.projection)), snap.projection)
            self.assertEqual(snap.profile.raw, fc.canonical(self.profile))
            self.assertEqual(snap.timing.elapsed_ms(), 9586000)
            self.assertEqual(snap.costs.body_plus_abort_bytes, 711262208)
        with self.assertRaises(FrozenInstanceError):
            snap.timing.spacing_ms = 0
        with self.assertRaises(TypeError):
            snap.timing.deadline_ms[0] = 0
        for mutate in (lambda p: p.update(unknown=1), lambda p: p.pop('native_review'),
                       lambda p: p.update(schema='R09_GATE3_LAUNCH_MANIFEST_V4')):
            v = deepcopy(self.profile)
            mutate(v)
            self.refuse('SCHEMA', fc.ExecutionProfile.read, fc.canonical(v))
        self.refuse('CANONICAL', fc.parse, b'{"a":1,"a":1}')
        self.refuse('CANONICAL', fc.parse, b'{ "a":1}')
        self.refuse('BOUNDS', fc.parse, b'{"a":9223372036854775808}')
        self.refuse('SCHEMA', fc.integer, True)
        self.refuse('SCHEMA', fc.integer, 1.0)
        self.refuse('BOUNDS', fc.product, 2**62, 4)
        self.refuse('BOUNDS', fc.total, [2**63-1, 1])

    def test_FC02_inventory_mutations(self):
        for action in ('omit', 'six_hour', 'duplicate', 'reorder', 'interpolate', 'drop_failed'):
            def change(v, action=action):
                m = v['manifest']
                if action == 'omit': m['slots'].pop(776)
                elif action == 'six_hour': m['slots'] = [s for s in m['slots'] if s[0] != 'IFS' or s[3] % 6 == 0]
                elif action == 'duplicate': m['slots'][776] = m['slots'][775]
                elif action == 'reorder': m['slots'][0], m['slots'][1] = m['slots'][1], m['slots'][0]
                elif action == 'interpolate': m['slots'][776][3] = 6
                else: m['roles'].pop()
            with self.subTest(action=action): self.mutate(change, 'APPLICABILITY')
        self.assertEqual(sum(s[0] == 'IFS' and s[3] % 6 != 0 for s in self.manifest['slots']), 612)

    def test_FC03_events_share_captures(self):
        for count in (1, 2):
            inputs, artifacts = fixture(count)
            with no_io(): snap = syn.inspect_synthetic(inputs, artifacts)
            v = json.loads(snap.projection)
            self.assertEqual((v['event_links'], v['provider_expansions']), (count*2713, count*3))
            self.assertEqual(len(v['manifest']['requests']), 2713)
        for key, value in (('field_ids', ['f0']*2713), ('providers', ['GEFS', 'IFS']),
                           ('eligible', True), ('requested_key', ['station', 'wrong', 'rule', 'date', 'HIGH'])):
            self.mutate(lambda v, k=key, x=value: v['manifest']['events'][0].update({k: x}), 'PROJECTION')

    def test_FC04_full_cap_and_abort_bounds(self):
        def full(v):
            m = v['manifest']
            m['provider_caps'] = dict(GEFS=2*fc.MIB, IFS=4*fc.MIB, AIFS=4*fc.MIB)
            for r in m['requests']:
                r['cap'] = r['reservation'] = m['provider_caps'][r['provider']]
                r['read_calls'] = 64
        self.mutate(full, 'BUDGET')
        self.mutate(lambda v: v['manifest'].update(abort_bytes=fc.GIB-711196672+1), 'BUDGET')
        self.mutate(lambda v: v['manifest']['requests'][0].update(end=262144), 'BUDGET')
        self.mutate(lambda v: v['manifest']['requests'][0].update(reservation=1), 'BUDGET')
        self.mutate(lambda v: v['manifest']['requests'][0].update(cap=1, reservation=1), 'BUDGET')
        self.mutate(lambda v: v['manifest'].update(requests=v['manifest']['requests']+[v['manifest']['requests'][0]]*1702), 'BOUNDS')
        self.assertEqual(775*2*fc.MIB+(1275+663)*4*fc.MIB, 9753853952)

    def timing(self):
        return json.loads(self.artifacts[self.profile['timing_plan']['sha256']])

    def test_FC05_FC06_timing_formulas_and_floors(self):
        t = self.timing()
        t['local_wall_ms'] += 1214000
        t['phase_bounds'][0]['wall_ms'] += 1214000
        self.assertEqual(fc.TimingPlan.read(fc.canonical(t)).elapsed_ms(), 10800000)
        t['local_wall_ms'] += 1
        t['phase_bounds'][0]['wall_ms'] += 1
        plan = fc.TimingPlan.read(fc.canonical(t))
        allocation = fc.AllocationPlan.read(self.artifacts[self.profile['allocation_plan']['sha256']])
        records = fc.RecordPlan.read(self.artifacts[self.profile['record_plan']['sha256']])
        self.refuse('BUDGET', fc.derive_costs, (262144,)*2713, 65536, plan, allocation, records, 2)
        for key, value, stage in (('local_wall_ms', 2712999, 'BUDGET'), ('finalization_ms', 59999, 'BUDGET'),
                                  ('spacing_ms', 1999, 'CLOCK'), ('deadline_ms', [30001]*2713, 'BOUNDS'),
                                  ('local_cpu_ms', 3500001, 'BUDGET')):
            t = self.timing(); t[key] = value
            self.refuse(stage, fc.TimingPlan.read, fc.canonical(t))
        t = self.timing(); t['phase_bounds'].pop(5)
        self.refuse('BUDGET', fc.TimingPlan.read, fc.canonical(t))
        self.assertEqual((2713-1)*2000+1000+2713000+60000, 8198000)
        self.assertEqual(20*1938, 38760)

    def test_FC05_trace_checks_without_scheduler(self):
        t = fc.TimingPlan.read(fc.canonical(self.timing()))
        starts = tuple(i*2000 for i in range(2713))
        closed = tuple(i*2000+1000 for i in range(2713))
        fc.check_synthetic_timing_trace(t, starts, closed)
        shifted = list(starts); shifted[1] -= 1
        self.refuse('CLOCK', fc.check_synthetic_timing_trace, t, tuple(shifted), closed)
        shifted[1] = 999
        self.refuse('BUDGET', fc.check_synthetic_timing_trace, t, tuple(shifted), closed)
        lagged = list(closed); lagged[0] = 2001
        self.refuse('BUDGET', fc.check_synthetic_timing_trace, t, starts, tuple(lagged))

    def test_FC19_role_mutations(self):
        # Pin-preserving corruption refuses SOURCE_PIN before interpretation.
        artifacts = dict(self.artifacts)
        h = self.manifest['roles'][0][fc.ROLES[0]]['proof']['sha256']
        artifacts[h] = artifacts[h] + b' '
        self.refuse('SOURCE_PIN', syn.inspect_synthetic, self.inputs, artifacts)
        for mode, confirmation in (('SEALED_OFFLINE', 'failed_confirmation'), ('SCHEDULED_CONFIRMATION', 'f0')):
            self.mutate(lambda v, m=mode, c=confirmation: v['manifest']['roles'][0][fc.ROLES[0]].update(mode=m, confirmation=c), 'DEPENDENCY')

    def test_FC20_seven_inputs_and_no_authority(self):
        inputs = dict(self.inputs); inputs.pop('event_policy_review')
        self.refuse('SCHEMA', syn.inspect_synthetic, inputs, self.artifacts)
        for k, val in (('producer', 'self'), ('reviewer', 'fixture_producer'), ('checkpoint', 'old')):
            self.mutate(lambda v, k=k, val=val: v['plan_review'].update({k: val}), 'TRUST')
        self.mutate(lambda v: v['plan_review'].update(manifest_sha256='0'*64), 'PROJECTION', False)
        self.mutate(lambda v: v['plan_review'].update(profile_sha256='0'*64), 'SOURCE_PIN', False)
        for schema in (fc.MANIFEST, 'R09_GATE3_LAUNCH_MANIFEST_V5', 'R09_GATE3_LAUNCH_MANIFEST_V4'):
            self.mutate(lambda v, s=schema: v['manifest'].update(schema=s), 'SCHEMA')
        with no_io():
            self.refuse('TRUST', fc.consume_accepted_fc1, self.inputs, self.artifacts)

    def test_FC21_exact_preservation(self):
        self.assertEqual(len(IDENTITIES), 79)
        self.assertEqual(fc.digest(fc.canonical(IDENTITIES)), syn.PRESERVATION_SHA256)
        self.assertEqual(IDENTITIES, PREDECESSOR['g3l_preservation'])
        self.mutate(lambda v: v['manifest']['preservation']['identities'][0].update(identity='renamed'), 'PROJECTION')
        self.mutate(lambda v: v['manifest']['preservation']['gates'].remove('A1'), 'PROJECTION')
        for k, val in (('missing', 76), ('credit', 1), ('g3l', 'PASS'), ('g3e', 'PASS')):
            self.mutate(lambda v, k=k, val=val: v['manifest']['preservation'].update({k: val}), 'TRUST')

    def test_FC22_clock_and_restrictions(self):
        for k, val, stage in (('now_ms', 3001, 'CLOCK'), ('metadata_upper_ms', 3000, 'CLOCK'),
                              ('feature_upper_ms', 14403001, 'CLOCK'), ('uncertainty_ms', 1001, 'CLOCK'),
                              ('restriction_expiry_ms', None, 'RIGHTS_LINEAGE'), ('unresolved_denial', True, 'RIGHTS_LINEAGE'),
                              ('retry', True, 'RIGHTS_LINEAGE'), ('credentials', True, 'RIGHTS_LINEAGE'),
                              ('redirect', True, 'RIGHTS_LINEAGE'), ('end_ms', 14403000, 'CLOCK')):
            with self.subTest(k=k):
                self.mutate(lambda v, k=k, val=val: v['manifest']['clock'].update({k: val}), stage)

    def test_FC23_coupled_closure_and_legacy(self):
        with no_io(): snap = syn.inspect_synthetic(self.inputs, self.artifacts)
        self.assertEqual(snap.costs.final_objects, 2754)
        self.assertEqual(snap.costs.peak_objects, 2755)
        self.assertEqual(dict((k, (e, b)) for k, e, b in snap.costs.journal_bounds)['session'], (32560, 66682880))
        legacy = dict(snap.costs.legacy)
        self.assertEqual(legacy['v4_objects'], 5438)
        self.assertEqual(legacy['v4_store_events'], 10853)
        self.assertEqual(legacy['runtime_session_events'], 54261)
        imported = sum(o.bytes for o in snap.allocation.objects if o.kind == 'IMPORT')
        self.assertEqual(legacy['v4_disk_bytes'], 4*64*fc.MIB+16*fc.MIB+5438*4*fc.MIB+8192+711196672+imported)
        self.assertEqual(legacy['runtime_disk_bytes'], 2*711196672+2*24*4*fc.MIB+(94955+8211+20*2713+4*2713+4)*65536+16*fc.MIB+4096+imported)
        artifacts = dict(self.artifacts); artifacts.pop(next(iter(artifacts)))
        self.refuse('DEPENDENCY', syn.inspect_synthetic, self.inputs, artifacts)
        self.assertEqual(snap.costs.domain_bytes, snap.allocation.domain_bytes())


    def artifact_mutation(self, target_hash, change, stage):
        """Rehash changed supplied bytes and dependent refs; never grant trust."""
        artifacts = dict(self.artifacts)
        values = deepcopy(self.values)
        original = json.loads(artifacts[target_hash])
        change(original)
        replacements = {}

        def replace(v):
            if type(v) is dict:
                if set(v) == {'sha256', 'byte_length', 'media_type'} and v['sha256'] in replacements:
                    return deepcopy(replacements[v['sha256']])
                return {k: replace(x) for k, x in v.items()}
            if type(v) is list:
                return [replace(x) for x in v]
            return v

        raw = fc.canonical(original)
        replacements[target_hash] = syn.reference(raw)
        artifacts.pop(target_hash)
        artifacts[fc.digest(raw)] = raw
        # DAG closure: support -> closure -> allocation -> manifest/profile.
        for _ in range(4):
            for h, old in tuple(artifacts.items()):
                new = fc.canonical(replace(json.loads(old)))
                if new != old:
                    replacements[h] = syn.reference(new)
                    artifacts.pop(h)
                    artifacts[fc.digest(new)] = new
        values = replace(values)
        with no_io():
            self.refuse(stage, syn.inspect_synthetic, seal(values), artifacts)

    def test_FC19_interpreted_synthetic_facts(self):
        for role, key, value, stage in (
            (0, 'observed_upper_ms', 2001, 'CLOCK'),
            (0, 'object_id', '0'*64, 'DEPENDENCY'),
            (2, 'source_kind', 'WEATHER_RELEASE', 'APPLICABILITY'),
            (0, 'slots', list(range(1, 775)), 'APPLICABILITY'),
            (0, 'rights_until_ms', 10802999, 'RIGHTS_LINEAGE'),
            (0, 'parser', 'UNKNOWN', 'TRUST')):
            h = self.manifest['roles'][0][fc.ROLES[role]]['proof']['sha256']
            with self.subTest(key=key):
                self.artifact_mutation(h, lambda p, k=key, v=value: p.update({k: v}), stage)

    def test_FC06_missing_cost_certificate(self):
        h = self.profile['scheduler_review']['sha256']
        self.artifact_mutation(h, lambda c: c.update(successful_path=False), 'BUDGET')
        self.artifact_mutation(h, lambda c: c['phases'].remove('fsync'), 'BUDGET')
        h = self.profile['timing_plan']['sha256']
        def zero(t):
            t['local_wall_ms'] -= t['phase_bounds'][5]['wall_ms']
            t['phase_bounds'][5]['wall_ms'] = 0
            t['phase_bounds'][5]['cpu_ms'] = 0
            t['local_cpu_ms'] -= 1
        self.artifact_mutation(h, zero, 'BUDGET')
        def default_decode(t):
            t['phase_bounds'][7]['wall_ms'] = 38760000
            t['local_wall_ms'] += 38759999
        self.artifact_mutation(h, default_decode, 'BUDGET')
        def zero_jitter(t):
            t['jitter_ms'] = 0
            t['phase_bounds'][14]['wall_ms'] = 0
        self.artifact_mutation(h, zero_jitter, 'BUDGET')

    def test_FC23_combined_graph_and_allocations(self):
        allocation = json.loads(self.artifacts[self.profile['allocation_plan']['sha256']])
        h = allocation['closure']['sha256']
        self.artifact_mutation(h, lambda c: c['nodes'][0]['external'].pop(), 'DEPENDENCY')
        self.artifact_mutation(h, lambda c: c['nodes'][0]['dependencies'].append('absent'), 'DEPENDENCY')
        self.artifact_mutation(h, lambda c: c['nodes'][0]['dependencies'].append('f0'), 'DEPENDENCY')
        self.artifact_mutation(h, lambda c: c['artifacts'].pop(), 'DEPENDENCY')
        def graph_overflow(c):
            # Individual node fanout is legal; combined semantic graph is not.
            for n in c['nodes'][:40]:
                n['dependencies'] = ['f'+str(i) for i in range(100, 200)]
        self.artifact_mutation(h, graph_overflow, 'BUDGET')
        h = self.profile['allocation_plan']['sha256']
        self.artifact_mutation(h, lambda a: a['allocations'].pop(3), 'BUDGET')
        self.artifact_mutation(h, lambda a: a['allocations'][0].update(bytes=1), 'BUDGET')

    def test_checked_journal_and_capacity_boundaries(self):
        snap = syn.inspect_synthetic(self.inputs, self.artifacts)
        t, a, r = snap.timing, snap.allocation, snap.records
        exact_abort = fc.GIB-snap.costs.body_bytes
        self.assertEqual(fc.derive_costs((262144,)*2713, exact_abort, t, a, r, 2).body_plus_abort_bytes, fc.GIB)
        self.refuse('BUDGET', fc.derive_costs, (262144,)*2713, exact_abort+1, t, a, r, 2)
        record = json.loads(r.raw)
        j = record['journals'][1]
        j['existing_events'] = 208
        j['existing_bytes'] = 425984
        p = fc.RecordPlan.read(fc.canonical(record)); p.check_remaining(2713, len(a.objects))
        j['existing_bytes'] += 1
        p = fc.RecordPlan.read(fc.canonical(record))
        self.refuse('BUDGET', p.check_remaining, 2713, len(a.objects))
        j['existing_bytes'] -= 1; j['existing_events'] += 1
        p = fc.RecordPlan.read(fc.canonical(record))
        self.refuse('BUDGET', p.check_remaining, 2713, len(a.objects))
        record = json.loads(r.raw); record['journals'][1]['types'][0]['max_remaining'] -= 1
        p = fc.RecordPlan.read(fc.canonical(record))
        self.refuse('BUDGET', p.check_remaining, 2713, len(a.objects))
        self.assertEqual([fc.event_nodes(n) for n in (1, 256, 257, 2713)], [1, 1, 3, 12])
        for overhead in (17, 18):
            candidate = json.loads(r.raw)
            for journal in candidate['journals'][:3]:
                for kind in journal['types'][:-1]: kind['max_remaining'] = 2713+overhead
            candidate = fc.RecordPlan.read(fc.canonical(candidate))
            if overhead == 17: candidate.check_remaining(2730, len(a.objects))
            else: self.refuse('BUDGET', candidate.check_remaining, 2731, len(a.objects))
        additions = tuple(fc.ObjectPlan('old_'+str(i), 'IMPORT', 1, True)
                          for i in range(4095-len(a.objects)))
        boundary = replace(a, objects=a.objects+additions)
        self.assertEqual(fc.derive_costs((262144,)*2713, 65536, t, boundary, r, 2).peak_objects, 4096)
        boundary = replace(boundary, objects=boundary.objects+(fc.ObjectPlan('one_more', 'IMPORT', 1, True),))
        self.refuse('BUDGET', fc.derive_costs, (262144,)*2713, 65536, t, boundary, r, 2)
        legacy = dict(fc.legacy_diagnostics((1,)*2713, (1000,)*2713, 2000, 2713000, 60000, 1, 0))
        self.assertEqual(legacy['additive_elapsed_ms'], 10910000)



    def test_FC19_earlier_charged_confirmation_is_frozen(self):
        inputs, artifacts = fixture(confirmation=True)
        with no_io(): snap = syn.inspect_synthetic(inputs, artifacts)
        self.assertEqual(snap.costs.requests, 2714)
        self.assertEqual(snap.costs.body_bytes, 711196672+512)
        values = {k: json.loads(v) for k, v in inputs.items()}
        values['manifest']['roles'][0][fc.ROLES[0]].update(mode='SEALED_OFFLINE', confirmation=None)
        # Preserve original supplemental pin: a failed confirmation cannot be
        # relabeled offline against the frozen package.
        original_pin = values['supplemental_pins']['roles_sha256']
        changed = seal(values)
        pins = json.loads(changed['supplemental_pins']); pins['roles_sha256'] = original_pin
        changed['supplemental_pins'] = fc.canonical(pins)
        with no_io(): self.refuse('DEPENDENCY', syn.inspect_synthetic, changed, artifacts)

    def test_FC01_legacy_consumers_refuse_fc1(self):
        from tools.v11_r09_gate3_launch import LaunchContractError
        from tools.v11_r09_gate3_launch_v4 import GROUPS, validate_manifest_v4
        from tools.v11_gate3_validated_plan_export_offline import ExportRefusal, consume_accepted_export
        payload = dict.fromkeys(GROUPS, {})
        payload['identity'] = dict(schema=fc.MANIFEST, pilot_id='synthetic_fc1',
                                   purpose='NONFINANCIAL_RESEARCH', capture_mode='BOUNDED_FEASIBILITY',
                                   created_ref={}, financial_authority=False, promotion_authority=False,
                                   host_approved=False, launch_authority=False, mapping_scope='SYNTHETIC_OFFLINE_ONLY')
        with no_io():
            with self.assertRaisesRegex(LaunchContractError, 'IDENTITY_MODE'):
                validate_manifest_v4(fc.canonical(payload), repo='/never', object_root='/never', now_utc=0)
            with self.assertRaisesRegex(ExportRefusal, 'EXTERNAL_TRUST_UNAVAILABLE'):
                consume_accepted_export(export_raw=fc.canonical({'schema': fc.EXPORT}),
                                        custody_raw=fc.canonical({'schema': fc.CUSTODY}),
                                        acceptance_raw=fc.canonical({'schema': fc.REVIEW}))

    def test_FC24_containment_and_denominator_on_refusal(self):
        with no_io():
            snap = syn.inspect_synthetic(self.inputs, self.artifacts)
            self.assertFalse(json.loads(snap.projection)['authority'])
            self.assertEqual(snap.qualification_credit, 0)
            rows = syn.refusal_rows('BUDGET')
            self.assertEqual(len(rows), 2713)
            self.assertEqual(sum(len(r[2]) for r in rows), 8139)
            syn.check_refusal_rows(rows, 'BUDGET')
            self.refuse('PROJECTION', syn.check_refusal_rows, rows[:-1], 'BUDGET')
            for fn, args in ((builtins.open, ('/private/never',)), (socket.socket, ()),
                             (subprocess.run, (['service', 'never'],)), (os.getenv, ('SECRET',))):
                with self.assertRaisesRegex(AssertionError, 'containment'): fn(*args)


if __name__ == '__main__':
    unittest.main()
