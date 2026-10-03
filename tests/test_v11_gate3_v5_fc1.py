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

    source_refs = []
    for name, size, sha in syn.CONTRACT_PINS:
        raw = (ROOT/'docs'/name).read_bytes()
        if len(raw) != size or fc.digest(raw) != sha:
            raise ValueError('contract source pin mismatch: ' + name)
        artifacts[sha] = raw
        source_refs.append(dict(sha256=sha, byte_length=size,
                                media_type='text/markdown' if name.endswith('.md') else 'application/json'))

    build = put({'schema': 'SYNTHETIC_BUILD', 'dependencies': [], 'code': 'offline-fixture'})
    reviews = [put({'schema': 'G3_V5_FC1_SYNTHETIC_COST_CERTIFICATE_1_IA1', 'owner': owner,
                    'phases': list(syn.PHASES), 'successful_path': True,
                    'build': build, 'dependencies': source_refs if owner == 'scheduler' else [],
                    'interface': ('G3_V5_FC1_START_BOUND_IA1' if owner == 'scheduler' else
                                  'G3_V5_FC1_CAPTURE_RECEIPT_IA1' if owner == 'ledger' else
                                  'G3_V5_FC1_COUNTED_EOF_STREAM_IA1' if owner == 'stream' else None),
                    'max_read_calls_at_4mib': 65 if owner == 'stream' else None})
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
                'schema': 'G3_V5_FC1_SYNTHETIC_ROLE_FACT_1_IA1', 'role': role,
                'slots': [i for i, s in enumerate(slots) if s[0] == provider],
                'cohort': fc.digest(fc.canonical(slots)),
                'object_id': fc.digest(provider.encode()), 'index_id': fc.digest((provider+'index').encode()),
                'observed_upper_ms': 1999, 'valid_from_ms': 1000,
                'valid_until_ms': 10803000, 'rights_until_ms': 10803000,
                'parser': 'SYNTHETIC_ONLY',
                'source_kind': 'OFFICIAL_COHORT' if role == fc.ROLES[2] else 'NATIVE'})
    requests = [dict(id='f'+str(i), purpose='FIELD', slot=i, provider=s[0],
                     cap=262144, reservation=262144, start=0, end=262143,
                     deadline_ms=2000, read_calls=5, object_id=fc.digest(s[0].encode()),
                     index_id=fc.digest((s[0]+'index').encode())) for i, s in enumerate(slots)]
    ids = [r['id'] for r in requests]
    roles = [{'field_id': r['id'], **{role: {'proof': proofs[r['provider'], role],
               'mode': 'SEALED_OFFLINE', 'confirmation': None} for role in fc.ROLES}} for r in requests]
    if confirmation:
        overhead = {**requests[0], 'id': 'confirm_index', 'purpose': 'INDEX',
                    'cap': 512, 'reservation': 512, 'start': None, 'end': None, 'read_calls': 2}
        requests.insert(0, overhead)
        for row in roles[:775]:
            row[fc.ROLES[0]].update(mode='SCHEDULED_CONFIRMATION', confirmation='confirm_index')
    event_rows = [dict(event_id=kind.lower(), kind=kind,
                       requested_key=['station', kind.lower(), 'rule', '2026-10-04', 'daily_'+kind.lower()+'_temperature'],
                       trial_key=['station', kind.lower(), '2026-10-04'], field_ids=ids[:],
                       providers=list(fc.PROVIDERS), eligible=False) for kind in ('HIGH', 'LOW')[:events]]
    phases = [dict(id=name, owner='LOCAL' if i < 13 else ('FINALIZATION' if name == 'finalize' else
                   'CLOCK_GUARD' if name == 'clock_guard' else 'JITTER'),
                   wall_ms=(3500000-12 if i == 0 else 1) if i < 13 else
                   (60000 if name == 'finalize' else 590000-100*len(requests) if name == 'jitter' else
                    50*len(requests) if name in ('START_BOUND_TOTAL', 'DISPATCH_BOUND_TOTAL') else 10000),
                   cpu_ms=1 if i < 13 else 0, review=reviews[0]) for i, name in enumerate(syn.PHASES)]
    timing = dict(schema='G3_V5_TIMING_PLAN_1_IA1', deadline_ms=[2000]*len(requests),
                  start_bound_ms=[50]*len(requests),
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
    source_by_hash = {ref['sha256']: ref for ref in source_refs}
    support_refs = [source_by_hash.get(fc.digest(raw), syn.reference(raw))
                    for raw in artifacts.values()]
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
    record = dict(schema='G3_V5_RECORD_PLAN_1_IA1', journals=journals)
    parsed_record = fc.RecordPlan.read(fc.canonical(record))
    imported = sum(r['byte_length'] for r in support_refs)
    needs = dict(raw=sum(r['cap'] for r in requests), temp=4*fc.MIB, imports=imported, source_copies=16*fc.MIB,
                 outputs=sum(o['bytes'] for o in objects if o['kind'] in ('AGGREGATE', 'DECODED')),
                 report=16*fc.MIB, emergency=65536, metadata=4096, snapshots=16*fc.MIB,
                 decoder_parent=fc.MIB, decoder_child=16*fc.MIB, stream_buffer=65536,
                 parser=fc.MIB, indexes=fc.MIB, state=fc.MIB)
    needs.update({j.kind+'_journal': j.bounds()[1] for j in parsed_record.journals})
    allocations = [dict(id=k, domain=fc.digest(('memory' if k in ('snapshots', 'decoder_parent', 'decoder_child', 'stream_buffer', 'parser', 'indexes', 'state') else 'disk').encode()),
                        bytes=v, first_phase=0, last_phase=len(syn.PHASES)-1, review=reviews[1]) for k, v in needs.items()]
    closure = dict(schema='G3_V5_FC1_SYNTHETIC_CLOSURE_1_IA1', nodes=nodes, artifacts=support_refs,
                   required_allocations=[{k: a[k] for k in ('id', 'domain', 'bytes')} for a in allocations])
    allocation = dict(schema='G3_V5_ALLOCATION_PLAN_1', objects=objects, allocations=allocations, closure=put(closure))
    profile = dict(schema=fc.PROFILE, cost_model=fc.COST_MODEL,
                   **dict(zip(('scheduler_review', 'store_review', 'ledger_review', 'stream_review', 'native_review'), reviews)),
                   allocation_plan=put(allocation), record_plan=put(record), timing_plan=put(timing))
    manifest = dict(schema='G3_V5_FC1_SYNTHETIC_MANIFEST_1_IA1', runs=runs, slots=slots,
                    requests=requests, provider_caps=dict.fromkeys(fc.PROVIDERS, 262144),
                    events=event_rows, roles=roles, execution_profile=profile, abort_bytes=65536,
                    clock=clock, preservation=dict(identities=IDENTITIES, missing=77, gates=list(syn.GATES),
                                                   credit=0, g3l='NO_GO', g3e='GATED'))
    values = dict(manifest=manifest,
                  plan_review=dict(schema='G3_V5_FC1_SYNTHETIC_REVIEW_1_IA1', manifest_sha256='', profile_sha256='',
                                   producer='fixture_producer', reviewer='fixture_reviewer', checkpoint='fixture_current',
                                   contract_sources=source_refs),
                  supplemental_pins=dict(schema='G3_V5_FC1_SYNTHETIC_PINS_1_IA1', roles_sha256='', requests_sha256=''),
                  runtime_context=dict(schema='G3_V5_FC1_SYNTHETIC_CONTEXT_1_IA1', profile_sha256='', build=build),
                  terminal_precedence=['PREREQUISITE', 'CLOCK', 'RESOURCE', 'DENIAL', 'VALIDATION', 'SUCCESS', 'UNSCHEDULED'],
                  event_policy=dict(schema='G3_V5_FC1_SYNTHETIC_EVENTS_1_IA1', events_sha256=''),
                  event_policy_review=dict(schema='G3_V5_FC1_SYNTHETIC_EVENT_REVIEW_1_IA1', policy_sha256=''))
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
                r['read_calls'] = 65
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
        self.assertEqual(sum(t['deadline_ms'])+(2713-1)*2000+3500000+60000+590000+10000,
                         15010000)  # inherited close-plus-spacing diagnostic
        self.assertEqual(10800000-fc.TimingPlan.read(fc.canonical(self.timing())).elapsed_ms(),
                         1214000)

    def test_FC05_trace_checks_without_scheduler(self):
        t = fc.TimingPlan.read(fc.canonical(self.timing()))
        def clock(ms):
            return dict(schema='G3_V5_FC1_ORIGINAL_CLOCK_IA1', boot_id='boot',
                        monotonic_ms=ms, offset_lower_ms=0, offset_upper_ms=0,
                        measured_utc_ms=ms)
        bounds = []
        permissions = []
        for i in range(2713):
            permission = i*2050
            permissions.append(permission)
            bounds.append(dict(schema='G3_V5_FC1_START_BOUND_IA1', request_id='f'+str(i),
                               context_sha256='1'*64, boot_id='boot',
                               lower_clock=clock(permission), upper_clock=clock(permission+50),
                               lower_ms=permission, actual_start_ms=permission+25,
                               upper_ms=permission+50, closed_ms=permission+1025,
                               deadline_origin_ms=permission, dispatch_persisted_ms=permission,
                               deadline_fixed_ms=permission+2000,
                               boundary_identity='FIRST_TRANSPORT_ACTIVITY',
                               durable_close=True, receipt_complete=True))
        fc.check_synthetic_timing_trace(t, tuple(bounds), tuple(permissions))
        self.refuse('SCHEMA', fc.check_synthetic_timing_trace, t,
                    tuple(i*2000 for i in range(2713)), tuple(i*2000+1000 for i in range(2713)))
        changed = deepcopy(bounds); changed[1]['lower_ms'] += 1
        self.refuse('CLOCK', fc.check_synthetic_timing_trace, t, tuple(changed), tuple(permissions))
        changed = deepcopy(bounds); changed[1]['durable_close'] = False
        self.refuse('CUSTODY', fc.check_synthetic_timing_trace, t, tuple(changed), tuple(permissions))
        changed = deepcopy(bounds); changed[1]['upper_ms'] += 1
        self.refuse('CLOCK', fc.check_synthetic_timing_trace, t, tuple(changed), tuple(permissions))
        changed = deepcopy(bounds); changed[0]['closed_ms'] = 2001
        self.refuse('BUDGET', fc.check_synthetic_timing_trace, t, tuple(changed), tuple(permissions))
        delayed = list(permissions); delayed[1] -= 1
        changed = deepcopy(bounds); changed[1]['dispatch_persisted_ms'] = delayed[1]
        changed[1]['deadline_origin_ms'] = delayed[1]
        changed[1]['deadline_fixed_ms'] = delayed[1]+2000
        self.refuse('CLOCK', fc.check_synthetic_timing_trace, t, tuple(changed), tuple(delayed))
        changed = deepcopy(bounds); changed[1]['deadline_origin_ms'] -= 1000
        self.refuse('BUDGET', fc.check_synthetic_timing_trace, t, tuple(changed), tuple(permissions))
        changed = deepcopy(bounds); changed[1]['boot_id'] = 'reboot'
        changed[1]['lower_clock']['boot_id'] = 'reboot'
        changed[1]['upper_clock']['boot_id'] = 'reboot'
        self.refuse('CLOCK', fc.check_synthetic_timing_trace, t, tuple(changed), tuple(permissions))

        # Replayed same-boot records must retain one intersection across the
        # whole session, including records that preceded a later reopen.
        changed = deepcopy(bounds)
        for sample in ('lower_clock', 'upper_clock'):
            changed[1][sample]['offset_lower_ms'] = 1000000
            changed[1][sample]['offset_upper_ms'] = 1000000
            changed[1][sample]['measured_utc_ms'] += 1000000
        self.refuse('CLOCK', fc.check_synthetic_timing_trace, t, tuple(changed), tuple(permissions))

        # Adjacent intersections can each be nonempty while the cumulative
        # intersection becomes empty at a later replayed request.
        changed = deepcopy(bounds)
        for row, low, high in ((0, 0, 2), (1, 1, 3), (2, 3, 4)):
            for sample in ('lower_clock', 'upper_clock'):
                changed[row][sample]['offset_lower_ms'] = low
                changed[row][sample]['offset_upper_ms'] = high
                changed[row][sample]['measured_utc_ms'] += low
        self.refuse('CLOCK', fc.check_synthetic_timing_trace, t, tuple(changed), tuple(permissions))

        # The lower clock itself must precede permission. Moving both clock
        # records coherently cannot hide delayed dispatch inside a 50 ms bracket.
        changed = deepcopy(bounds)
        changed[-1]['lower_ms'] += 900
        changed[-1]['actual_start_ms'] += 900
        changed[-1]['upper_ms'] += 900
        changed[-1]['closed_ms'] += 900
        for sample, advance in (('lower_clock', 900), ('upper_clock', 900)):
            changed[-1][sample]['monotonic_ms'] += advance
            changed[-1][sample]['measured_utc_ms'] += advance
        self.refuse('CLOCK', fc.check_synthetic_timing_trace, t, tuple(changed), tuple(permissions))

        # A small gate delay is inside the bracket; intent persistence after
        # the original lower sample is not a valid preauthorization bracket.
        delayed_permission = list(permissions)
        delayed_permission[1] += 10
        fc.check_synthetic_timing_trace(t, tuple(bounds), tuple(delayed_permission))
        changed = deepcopy(bounds)
        changed[1]['dispatch_persisted_ms'] += 5
        self.refuse('CLOCK', fc.check_synthetic_timing_trace, t,
                    tuple(changed), tuple(delayed_permission))

    def test_IA1_wide_start_bracket_preserves_cross_request_clock_order(self):
        plan = self.timing()
        plan['deadline_ms'][1] = 3000
        plan['start_bound_ms'][1] = 2100  # Larger than the 2000 ms spacing.
        for phase in plan['phase_bounds']:
            if phase['id'] in ('START_BOUND_TOTAL', 'DISPATCH_BOUND_TOTAL'):
                phase['wall_ms'] += 2050
            elif phase['id'] == 'jitter':
                phase['wall_ms'] -= 4100
        timing = fc.TimingPlan.read(fc.canonical(plan))
        self.assertEqual(timing.elapsed_ms(), 9587000)

        def clock(ms):
            return dict(schema='G3_V5_FC1_ORIGINAL_CLOCK_IA1', boot_id='boot',
                        monotonic_ms=ms, offset_lower_ms=0, offset_upper_ms=0,
                        measured_utc_ms=ms)

        permissions = tuple(i*2050 for i in range(fc.FIELDS))
        bounds = [dict(schema='G3_V5_FC1_START_BOUND_IA1', request_id='f'+str(i),
                       context_sha256='1'*64, boot_id='boot',
                       lower_clock=clock(permission), upper_clock=clock(permission+50),
                       lower_ms=permission, actual_start_ms=permission+25,
                       upper_ms=permission+50, closed_ms=permission+1025,
                       deadline_origin_ms=permission, dispatch_persisted_ms=permission,
                       deadline_fixed_ms=permission+2000,
                       boundary_identity='FIRST_TRANSPORT_ACTIVITY',
                       durable_close=True, receipt_complete=True)
                  for i, permission in enumerate(permissions)]
        bounds[0]['closed_ms'] = 525
        bounds[1]['closed_ms'] = 2575
        bounds[1]['deadline_fixed_ms'] = 3000
        bounds[1]['deadline_origin_ms'] = 0

        # Prior custody closes at 525; a broad, ordered bracket remains valid.
        bounds[1]['lower_ms'] = bounds[1]['dispatch_persisted_ms'] = 525
        bounds[1]['lower_clock'] = clock(525)
        fc.check_synthetic_timing_trace(timing, tuple(bounds), permissions)

        # The same valid plan previously admitted original samples 0, 50, 0, 2100.
        reversed_bounds = deepcopy(bounds)
        reversed_bounds[1]['lower_ms'] = reversed_bounds[1]['dispatch_persisted_ms'] = 0
        reversed_bounds[1]['lower_clock'] = clock(0)
        self.refuse('CLOCK', fc.check_synthetic_timing_trace,
                    timing, tuple(reversed_bounds), permissions)

        # Sampling after close cannot redeem an intent persisted before it.
        preclose_intent = deepcopy(bounds)
        preclose_intent[1]['dispatch_persisted_ms'] = 524
        self.refuse('CLOCK', fc.check_synthetic_timing_trace,
                    timing, tuple(preclose_intent), permissions)

    def test_IA1_terminal_receipt_custody_adverse(self):
        context = '1'*64
        terminal = dict(schema='G3_V5_FC1_SESSION_IA1', type='TERMINAL',
                        request_id='f0', context_sha256=context, outcome='SUCCESS',
                        prior_hash='2'*64, closed=True, accounted=True, witnessed=True,
                        denied=False, overdelivery=False, intent_recorded=True)
        raw_terminal = fc.canonical(terminal)
        head = fc.digest(raw_terminal)
        receipt = dict(schema='G3_V5_FC1_CAPTURE_RECEIPT_IA1', type='CAPTURE_RECEIPT',
                       request_id='f0', context_sha256=context, outcome='SUCCESS',
                       prior_hash=head, session_terminal_head=head, store_head='3'*64,
                       budget_head='4'*64, denial_head='5'*64, shared_head='6'*64)
        raw_receipt = fc.canonical(receipt)
        receipt_head = fc.digest(raw_receipt)
        check = fc.check_synthetic_capture_custody
        self.assertFalse(check((), (), 'f0', context).complete)
        self.assertFalse(check((raw_terminal,), (head,), 'f0', context).complete)
        self.assertFalse(check((raw_terminal, raw_receipt), (head,), 'f0', context).complete)
        self.assertFalse(check((raw_terminal, raw_receipt), (receipt_head,), 'f0', context).complete)
        self.assertTrue(check((raw_terminal, raw_receipt), (head, receipt_head), 'f0', context).complete)
        self.refuse('CUSTODY', check, (raw_terminal, raw_receipt, raw_receipt),
                    (head, receipt_head), 'f0', context)
        for key, value in (('session_terminal_head', '0'*64), ('outcome', 'FAILED'),
                           ('request_id', 'f1'), ('prior_hash', '0'*64)):
            changed = dict(receipt, **{key: value})
            self.refuse('DEPENDENCY', check, (raw_terminal, fc.canonical(changed)),
                        (head, fc.digest(fc.canonical(changed))), 'f0', context)
        failed = dict(terminal, witnessed=False, outcome='FAILED')
        self.assertFalse(check((fc.canonical(failed),), (), 'f0', context).complete)
        invalid = dict(terminal, accounted=False)
        self.refuse('CUSTODY', check, (fc.canonical(invalid),), (), 'f0', context)
        refused = dict(terminal, outcome='REFUSED', closed=False, accounted=False,
                       witnessed=False, overdelivery=True)
        refused_raw = fc.canonical(refused)
        refused_head = fc.digest(refused_raw)
        refused_receipt = dict(receipt, outcome='REFUSED', prior_hash=refused_head,
                               session_terminal_head=refused_head)
        receipt_raw = fc.canonical(refused_receipt)
        self.refuse('CUSTODY', check, (refused_raw, receipt_raw),
                    (refused_head, fc.digest(receipt_raw)), 'f0', context)

    def test_IA1_counted_eof_and_all_byte_paths(self):
        def read(kind='DATA', body=65536, maximum=65536, eager=0, queued=0, late=0,
                 framed=False, exhausted=False):
            return dict(kind=kind, requested_max=maximum, body_bytes=body,
                        eager_bytes=eager, queued_bytes=queued, late_bytes=late,
                        framing_complete=framed, queue_exhausted=exhausted)
        cap = 4*fc.MIB
        payload = tuple(read() for _ in range(64))
        eof = read('EOF', 0, 1, framed=True, exhausted=True)
        inspect = fc.check_synthetic_stream_trace
        self.refuse('BUDGET', inspect, cap, cap, 64, payload, True, True)
        self.assertFalse(inspect(cap, cap, 65, payload, True, True).complete)
        good = inspect(cap, cap, 65, payload+(eof,), True, True)
        self.assertEqual((good.read_calls, good.received_bytes, good.eof, good.complete),
                         (65, cap, True, True))
        self.assertTrue(inspect(cap, cap, 65, payload+(eof,), False, False).debit_bytes == cap+65536)
        for tail in (read('EOF', 0, 1, queued=1, framed=True, exhausted=False),
                     read('DATA', 1, 1), read('EMPTY', 0, 1),
                     read('ERROR', 0, 1, late=7)):
            result = inspect(cap, cap, 65, payload+(tail,), True, True)
            self.assertFalse(result.complete)
            self.assertEqual(result.received_bytes, cap+tail['body_bytes']+
                             tail['eager_bytes']+tail['queued_bytes']+tail['late_bytes'])
        extra = inspect(cap, cap, 65, payload+(eof, read('DATA', 1, 1)), True, True)
        self.assertTrue(extra.poisoned)
        self.assertEqual((extra.read_calls, extra.received_bytes), (66, cap+1))
        short = tuple(read(body=1) for _ in range(65))
        self.assertFalse(inspect(cap, cap, 65, short, True, True).complete)
        eager = inspect(cap, cap, 65, (read(body=65536, eager=cap),), False, False)
        self.assertTrue(eager.poisoned)
        self.assertEqual(eager.received_bytes, cap+65536)
        excess = inspect(cap, cap, 65, (read(body=65536, eager=cap+1),), False, False)
        self.assertEqual((excess.received_bytes, excess.debit_bytes),
                         (cap+65537, cap+65537))
        retained = inspect(cap, cap, 65, payload+(eof,), True, True,
                           prior_uncertain_debit=cap+65536)
        self.assertEqual(retained.debit_bytes, cap+65536)
        self.assertTrue(retained.eof)
        self.assertFalse(retained.complete)
        self.assertTrue(retained.poisoned)

    def test_IA1_cost_certificate_and_versions(self):
        h = self.profile['stream_review']['sha256']
        self.artifact_mutation(h, lambda c: c.update(max_read_calls_at_4mib=64), 'BUDGET')
        h = self.profile['scheduler_review']['sha256']
        self.artifact_mutation(h, lambda c: c.update(interface=None), 'BUDGET')
        t = self.timing()
        t['start_bound_ms'][0] += 1
        self.refuse('BUDGET', fc.TimingPlan.read, fc.canonical(t))
        t = self.timing(); t.pop('start_bound_ms')
        self.refuse('SCHEMA', fc.TimingPlan.read, fc.canonical(t))
        t = self.timing(); t['schema'] = 'G3_V5_TIMING_PLAN_1'
        self.refuse('SCHEMA', fc.TimingPlan.read, fc.canonical(t))
        self.mutate(lambda v: v['plan_review']['contract_sources'][0].update(sha256='0'*64),
                    'SOURCE_PIN')

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
        self.assertEqual(snap.costs.final_objects, 2761)
        self.assertEqual(snap.costs.peak_objects, 2762)
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
                if h in {sha for _, _, sha in syn.CONTRACT_PINS}:
                    continue  # Reviewed public documents are exact opaque pins.
                try:
                    document = json.loads(old)
                except (ValueError, UnicodeError):
                    continue  # Pinned markdown is opaque supplied evidence.
                new = fc.canonical(replace(document))
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
