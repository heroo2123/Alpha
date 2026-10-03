"""Pure FC1 step-2 supplied-byte schemas and arithmetic, never admission.

No filesystem, network, clock, environment, provider or runtime imports. Parsed
plans are immutable values, not capabilities. Synthetic checks cannot authenticate
reviews, qualify serializers/native decoders, or issue accepted exports.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re

MIB = 1024**2
GIB = 1024**3
MAX_INT = 2**63 - 1
FIELDS = 2713
ROLES = ('INDEX_SELECTION', 'OBJECT_IDENTITY', 'COHORT_METADATA')
PROVIDERS = ('GEFS', 'IFS', 'AIFS')
PURPOSES = ('FIELD', 'INDEX', 'OBJECT_ID', 'METADATA', 'PROBE')
INPUT_ROLES = ('manifest', 'plan_review', 'supplemental_pins', 'runtime_context',
               'terminal_precedence', 'event_policy', 'event_policy_review')
PROFILE = 'G3_V5_FULL_COHORT_EXECUTION_1_IA1'
COST_MODEL = 'G3_V5_FULL_COHORT_COST_1_IA1'
MANIFEST = 'R09_GATE3_LAUNCH_MANIFEST_V5_FC1_IA1'
PROJECTION = 'G3_V5_PROJECTION_1_FC1_IA1'
EXPORT = 'G3_V5_EXPORT_1_FC1_IA1'
CUSTODY = 'G3_V5_EXPORT_CUSTODY_1_FC1_IA1'
REVIEW = 'G3_V5_EXPORT_REVIEW_1_FC1_IA1'
TRUSTED_PINS = 'G3_V5_EXPORT_TRUSTED_PINS_1_FC1_IA1'
SYNTHETIC = 'G3_V5_FC1_SYNTHETIC_PROJECTION_1_IA1'
OWNERS = ('LOCAL', 'FINALIZATION', 'JITTER', 'CLOCK_GUARD')
JOURNALS = ('budget', 'session', 'denial', 'store')
EVENT_LIMITS = (131072, 32768, 32768, 10000)
# This is the closed synthetic certificate vocabulary, not enrollment of a
# production serializer. Four lifecycle records include the one reopen reserve.
RECORD_TYPES = (
    ('RESERVE', 'KNOWN_ACCOUNT', 'TERMINAL', 'FAILURE_ANNOTATION', 'LIFECYCLE'),
    ('INTENT', 'BUDGET_RESERVED', 'DEADLINE_FIXED', 'DISPATCH_INTENT', 'DENIAL',
     'TRANSPORT_CLOSED', 'ACCOUNTED', 'OBJECT_WITNESSED', 'TERMINAL',
     'CAPTURE_RECEIPT', 'CLOCK_CHECK', 'FAILURE_ANNOTATION', 'LIFECYCLE'),
    ('INTENT_OPEN', 'DENIAL', 'RESTRICTION_UNRESOLVED', 'INTENT_CLOSE', 'LIFECYCLE'),
    ('PREPARE', 'COMMIT', 'FAILURE_ANNOTATION', 'LIFECYCLE'),
)


class Refusal(ValueError):
    """Bounded stage-only error: never embeds evidence or drops report rows."""
    def __init__(self, stage):
        self.stage = stage
        super().__init__('FC1_' + stage)


def need(condition, stage='SCHEMA'):
    if not condition:
        raise Refusal(stage)


def integer(value, low=0, high=MAX_INT, stage='BOUNDS'):
    need(type(value) is int)
    need(low <= value <= high, stage)
    return value


def total(values):
    result = 0
    for value in values:
        result = integer(result + integer(value))
    return result


def product(a, b):
    return integer(integer(a) * integer(b))


def exact(value, keys):
    need(type(value) is dict and set(value) == set(keys))


def ident(value):
    need(type(value) is str and re.fullmatch(r'[A-Za-z0-9_-]{1,80}', value) is not None)
    return value


def digest(raw):
    need(type(raw) is bytes)
    return hashlib.sha256(raw).hexdigest()


def hash_value(value):
    need(type(value) is str and re.fullmatch(r'[0-9a-f]{64}', value) is not None)
    return value


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=True, allow_nan=False).encode('utf-8')


def parse(raw, limit=32*MIB):
    """Bound before materialization, then require exact canonical JSON bytes."""
    need(type(raw) is bytes and 0 < len(raw) <= limit, 'BOUNDS')
    depth = nodes = token = 0
    quoted = escaped = False
    for b in raw:
        if quoted:
            token += 1
            need(token <= 24576, 'BOUNDS')
            if escaped:
                escaped = False
            elif b == 92:
                escaped = True
            elif b == 34:
                quoted = False
        elif b == 34:
            quoted, token = True, 0
            nodes += 1
        elif b in (91, 123):
            depth += 1
            nodes += 1
            need(depth <= 16, 'BOUNDS')
        elif b in (93, 125):
            depth -= 1
        elif b not in b' \r\n\t,:':
            nodes += 1
        need(nodes <= 500000, 'BOUNDS')
    need(depth == 0 and not quoted, 'CANONICAL')

    def pairs(items):
        result = {}
        for k, v in items:
            need(k not in result, 'CANONICAL')
            result[k] = v
        return result

    def walk(v):
        if type(v) is dict:
            need(len(v) <= 64, 'BOUNDS')
            for k, child in v.items():
                walk(k)
                walk(child)
        elif type(v) is list:
            need(len(v) <= 3600, 'BOUNDS')
            for child in v:
                walk(child)
        elif type(v) is str:
            need(not any(0xD800 <= ord(c) <= 0xDFFF for c in v), 'CANONICAL')
            need(len(v.encode('utf-8')) <= 4096, 'BOUNDS')
        elif type(v) is int:
            integer(v)
        else:
            need(v is None or type(v) is bool)

    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs,
                           parse_constant=lambda _: need(False, 'CANONICAL'))
        walk(value)
        need(canonical(value) == raw, 'CANONICAL')
        return value
    except Refusal:
        raise
    except (ValueError, UnicodeError, RecursionError, OverflowError):
        raise Refusal('CANONICAL') from None


@dataclass(frozen=True)
class Ref:
    sha256: str
    byte_length: int
    media_type: str

    @classmethod
    def read(cls, v):
        exact(v, ('sha256', 'byte_length', 'media_type'))
        hash_value(v['sha256'])
        integer(v['byte_length'], 1, GIB)
        need(type(v['media_type']) is str and 0 < len(v['media_type'].encode()) <= 4096)
        return cls(**v)

    def verify(self, raw):
        need(type(raw) is bytes and len(raw) == self.byte_length and
             digest(raw) == self.sha256, 'SOURCE_PIN')


@dataclass(frozen=True)
class Phase:
    id: str
    owner: str
    wall_ms: int
    cpu_ms: int
    review: Ref


@dataclass(frozen=True)
class TimingPlan:
    deadline_ms: tuple[int, ...]
    start_bound_ms: tuple[int, ...]
    spacing_ms: int
    local_wall_ms: int
    local_cpu_ms: int
    finalization_ms: int
    jitter_ms: int
    clock_guard_ms: int
    phase_bounds: tuple[Phase, ...]
    raw: bytes

    @classmethod
    def read(cls, raw):
        v = parse(raw)
        keys = ('deadline_ms', 'start_bound_ms', 'spacing_ms', 'local_wall_ms', 'local_cpu_ms',
                'finalization_ms', 'jitter_ms', 'clock_guard_ms', 'phase_bounds')
        exact(v, ('schema',) + keys)
        need(v['schema'] == 'G3_V5_TIMING_PLAN_1_IA1')
        need(type(v['deadline_ms']) is list and 0 < len(v['deadline_ms']) <= 3600)
        deadlines = tuple(integer(d, 1000, 30000) for d in v['deadline_ms'])
        need(type(v['start_bound_ms']) is list and len(v['start_bound_ms']) == len(deadlines), 'BUDGET')
        bounds = tuple(integer(a) for a in v['start_bound_ms'])
        need(all(d % 1000 == 0 for d in deadlines))
        for key in keys[2:-1]:
            integer(v[key])
        need(v['spacing_ms'] >= 2000, 'CLOCK')
        need(v['local_wall_ms'] >= 1000*FIELDS and v['finalization_ms'] >= 60000 and
             v['local_cpu_ms'] <= v['local_wall_ms'], 'BUDGET')
        need(type(v['phase_bounds']) is list)
        phases = []
        for p in v['phase_bounds']:
            exact(p, ('id', 'owner', 'wall_ms', 'cpu_ms', 'review'))
            ident(p['id'])
            need(p['owner'] in OWNERS)
            integer(p['wall_ms'])
            integer(p['cpu_ms'])
            need(p['cpu_ms'] <= p['wall_ms'], 'BUDGET')
            phases.append(Phase(p['id'], p['owner'], p['wall_ms'], p['cpu_ms'], Ref.read(p['review'])))
        need(len({p.id for p in phases}) == len(phases))
        for owner, key in zip(OWNERS, ('local_wall_ms', 'finalization_ms', 'jitter_ms', 'clock_guard_ms')):
            need(total(p.wall_ms for p in phases if p.owner == owner) == v[key], 'BUDGET')
        need(total(p.cpu_ms for p in phases if p.owner == 'LOCAL') == v['local_cpu_ms'], 'BUDGET')
        a = total(bounds)
        for phase_id in ('START_BOUND_TOTAL', 'DISPATCH_BOUND_TOTAL'):
            selected = [p for p in phases if p.id == phase_id]
            need(len(selected) == 1 and selected[0].owner == 'JITTER' and
                 selected[0].wall_ms == a and selected[0].cpu_ms == 0, 'BUDGET')
        need(total((a, a)) <= v['jitter_ms'], 'BUDGET')
        return cls(deadlines, bounds, *(v[k] for k in keys[2:-1]), tuple(phases), raw)

    def elapsed_ms(self):
        return total((*[max(self.spacing_ms, d) for d in self.deadline_ms[:-1]],
                      self.deadline_ms[-1], self.local_wall_ms, self.finalization_ms,
                      self.jitter_ms, self.clock_guard_ms))


@dataclass(frozen=True)
class ObjectPlan:
    id: str
    kind: str
    bytes: int
    existing: bool


@dataclass(frozen=True)
class Allocation:
    id: str
    domain: str
    bytes: int
    first_phase: int
    last_phase: int
    review: Ref


@dataclass(frozen=True)
class AllocationPlan:
    objects: tuple[ObjectPlan, ...]
    allocations: tuple[Allocation, ...]
    closure: Ref
    raw: bytes

    @classmethod
    def read(cls, raw):
        v = parse(raw)
        exact(v, ('schema', 'objects', 'allocations', 'closure'))
        need(v['schema'] == 'G3_V5_ALLOCATION_PLAN_1')
        need(type(v['objects']) is list and type(v['allocations']) is list)
        objects, allocations = [], []
        for o in v['objects']:
            exact(o, ('id', 'kind', 'bytes', 'existing'))
            ident(o['id'])
            need(o['kind'] in ('RAW', 'IMPORT', 'CATALOG', 'AGGREGATE', 'DECODED'))
            integer(o['bytes'], 1, 4*MIB)
            need(type(o['existing']) is bool)
            objects.append(ObjectPlan(**o))
        for a in v['allocations']:
            exact(a, ('id', 'domain', 'bytes', 'first_phase', 'last_phase', 'review'))
            ident(a['id'])
            hash_value(a['domain'])
            integer(a['bytes'], 1)
            integer(a['first_phase'], 0, 3599)
            integer(a['last_phase'], a['first_phase'], 3599)
            allocations.append(Allocation(**{**a, 'review': Ref.read(a['review'])}))
        for rows in (objects, allocations):
            need(len({r.id for r in rows}) == len(rows))
        return cls(tuple(objects), tuple(allocations), Ref.read(v['closure']), raw)

    def domain_bytes(self):
        """Unqualified lifetimes never earn overlap credit: sum per domain."""
        domains = dict.fromkeys(a.domain for a in self.allocations)
        return tuple((d, total(a.bytes for a in self.allocations if a.domain == d)) for d in domains)


@dataclass(frozen=True)
class RecordType:
    type: str
    max_bytes: int
    max_remaining: int
    review: Ref


@dataclass(frozen=True)
class Journal:
    kind: str
    existing_events: int
    existing_bytes: int
    tail_bytes: int
    types: tuple[RecordType, ...]

    def bounds(self):
        return (total((self.existing_events, *(t.max_remaining for t in self.types))),
                total((self.existing_bytes, self.tail_bytes,
                       *(product(t.max_remaining, t.max_bytes) for t in self.types))))


@dataclass(frozen=True)
class RecordPlan:
    journals: tuple[Journal, ...]
    raw: bytes

    @classmethod
    def read(cls, raw):
        v = parse(raw)
        exact(v, ('schema', 'journals'))
        need(v['schema'] == 'G3_V5_RECORD_PLAN_1_IA1' and type(v['journals']) is list)
        need(len(v['journals']) == 4)
        journals = []
        for j, kind, names in zip(v['journals'], JOURNALS, RECORD_TYPES):
            exact(j, ('kind', 'existing_events', 'existing_bytes', 'tail_bytes', 'types'))
            need(j['kind'] == kind and type(j['types']) is list and len(j['types']) == len(names))
            for key in ('existing_events', 'existing_bytes', 'tail_bytes'):
                integer(j[key])
            types = []
            for t, name in zip(j['types'], names):
                exact(t, ('type', 'max_bytes', 'max_remaining', 'review'))
                need(t['type'] == name)
                integer(t['max_bytes'], 1, 65536)
                integer(t['max_remaining'])
                types.append(RecordType(name, t['max_bytes'], t['max_remaining'], Ref.read(t['review'])))
            journals.append(Journal(kind, j['existing_events'], j['existing_bytes'], j['tail_bytes'], tuple(types)))
        return cls(tuple(journals), raw)

    def check_remaining(self, requests, new_objects):
        """Fresh remaining schedule, no inherited unfinished transitions allowed."""
        integer(requests, 1, 3600)
        integer(new_objects, 0, 4096)
        for j, limit in zip(self.journals, EVENT_LIMITS):
            count = new_objects if j.kind == 'store' else requests
            need(all(t.max_remaining == (4 if t.type == 'LIFECYCLE' else count)
                     for t in j.types), 'BUDGET')
            events, size = j.bounds()
            need(events <= limit and size <= 64*MIB, 'BUDGET')


@dataclass(frozen=True)
class ExecutionProfile:
    scheduler_review: Ref
    store_review: Ref
    ledger_review: Ref
    stream_review: Ref
    native_review: Ref
    allocation_plan: Ref
    record_plan: Ref
    timing_plan: Ref
    raw: bytes

    @classmethod
    def read(cls, raw):
        v = parse(raw)
        names = tuple(cls.__dataclass_fields__)[:-1]
        exact(v, ('schema', 'cost_model') + names)
        need(v['schema'] == PROFILE and v['cost_model'] == COST_MODEL)
        return cls(*(Ref.read(v[n]) for n in names), raw)

    def refs(self):
        return tuple(getattr(self, n) for n in tuple(self.__dataclass_fields__)[:-1])


def event_nodes(fields):
    integer(fields, 1, FIELDS)
    result, width = 1, fields
    while width > 256:
        width = (width + 255) // 256
        result += width
    return result


def legacy_diagnostics(caps, deadlines, spacing, local, finalization, events,
                       decoded_bytes, added_disk_bytes=0, added_memory_bytes=0):
    """Both old estimates, with new work added to each; never admission."""
    n, body = len(caps), total(caps)
    integer(n, 1, 3600)
    need(len(deadlines) == n)
    integer(events, 1, 2)
    integer(decoded_bytes)
    integer(spacing)
    a = event_nodes(n) if n <= FIELDS else _legacy_aggregate(n)
    runtime_a = product(event_nodes(FIELDS), events)
    objects = total((product(2, n), a))
    records = total((total(min(integer(c, 1), 32) for c in caps), product(3, n)))
    runtime_events = product(3, n + runtime_a)
    v4_disk = total((4*64*MIB, 16*MIB, product(objects, 4*MIB), decoded_bytes, body, added_disk_bytes))
    runtime_disk = total((product(2, body), product(2*4*MIB, runtime_a),
                          product(total((records, runtime_events, 20*n, 4*n, 4)), 65536),
                          16*MIB, 4096, added_disk_bytes))
    return (('additive_elapsed_ms', total((*deadlines, product(n-1, spacing), local, finalization))),
            ('v4_objects', objects), ('v4_store_events', 4*n+1),
            ('v4_disk_bytes', v4_disk), ('runtime_disk_bytes', runtime_disk),
            ('runtime_memory_bytes', total((product(2, max(caps)), 4*65536, added_memory_bytes))),
            ('runtime_budget_records', records), ('runtime_session_events', 20*n+1),
            ('historical_estimate_not_current_demand', 1469234173))


def _legacy_aggregate(n):
    count = 0
    while n > 1:
        n = (n + 255)//256
        count += n
    return count


@dataclass(frozen=True)
class CostBounds:
    requests: int
    body_bytes: int
    body_plus_abort_bytes: int
    elapsed_ms: int
    final_objects: int
    peak_objects: int
    store_add_events: int
    journal_bounds: tuple[tuple[str, int, int], ...]
    domain_bytes: tuple[tuple[str, int], ...]
    legacy: tuple[tuple[str, int], ...]


def derive_costs(caps, abort_bytes, timing, allocation, records, events):
    """Necessary bounds only. Does not qualify any supplied review/cost fact."""
    need(type(caps) is tuple and all(type(x) is int for x in caps))
    integer(len(caps), FIELDS, 3600, 'BUDGET')
    integer(events, 1, 2)
    integer(abort_bytes, 1, GIB)
    need(type(timing) is TimingPlan and type(allocation) is AllocationPlan and type(records) is RecordPlan)
    need(len(timing.deadline_ms) == len(caps), 'BUDGET')
    need(timing.jitter_ms > 0 and timing.clock_guard_ms > 0, 'BUDGET')
    body, elapsed = total(caps), timing.elapsed_ms()
    need(total((body, abort_bytes)) <= GIB and elapsed <= 10800000, 'BUDGET')
    raws = tuple(o for o in allocation.objects if o.kind == 'RAW')
    need(len(raws) == len(caps) and tuple(o.bytes for o in raws) == caps, 'BUDGET')
    need(sum(o.kind == 'AGGREGATE' for o in allocation.objects) >= events*event_nodes(FIELDS), 'BUDGET')
    final_objects = len(allocation.objects)
    need(final_objects + 1 <= 4096, 'BUDGET')
    new = sum(not o.existing for o in allocation.objects)
    records.check_remaining(sum(not o.existing for o in raws), new)
    journal_bounds = tuple((j.kind, *j.bounds()) for j in records.journals)
    need(records.journals[3].bounds()[0] == records.journals[3].existing_events + 3*new+4, 'BUDGET')
    # Conservative sum fallback: no physical qualification or aliasing credit.
    domains = allocation.domain_bytes()
    decoded = total(o.bytes for o in allocation.objects if o.kind == 'DECODED')
    extra = total(o.bytes for o in allocation.objects if o.kind in ('IMPORT', 'CATALOG'))
    legacy = legacy_diagnostics(caps, timing.deadline_ms, timing.spacing_ms,
                                timing.local_wall_ms, timing.finalization_ms,
                                events, decoded, extra)
    return CostBounds(len(caps), body, body+abort_bytes, elapsed, final_objects,
                      final_objects+1, 3*new+4, journal_bounds, domains, legacy)


def consume_accepted_fc1(*args, **kwargs):
    """No enrolled producer/reviewer/custodian channel exists in step 2."""
    raise Refusal('TRUST')


def check_synthetic_timing_trace(timing, bounds, permission_lower_ms):
    """Necessary IA1 arithmetic on supplied observations, never producer proof.

    A real producer must prove and durably record the actual transport boundary.
    These caller supplied records cannot authenticate a clock, fsync or adapter.
    """
    need(type(timing) is TimingPlan and type(bounds) is tuple and
         type(permission_lower_ms) is tuple)
    need(len(bounds) == len(permission_lower_ms) == len(timing.deadline_ms), 'BUDGET')
    previous = None
    for i, (b, permission, deadline, allowance) in enumerate(zip(
            bounds, permission_lower_ms, timing.deadline_ms, timing.start_bound_ms)):
        exact(b, ('schema', 'request_id', 'context_sha256', 'boot_id', 'lower_clock',
                  'upper_clock', 'lower_ms', 'actual_start_ms', 'upper_ms',
                  'closed_ms', 'deadline_origin_ms', 'deadline_fixed_ms',
                  'dispatch_persisted_ms', 'boundary_identity',
                  'durable_close', 'receipt_complete'))
        need(b['schema'] == 'G3_V5_FC1_START_BOUND_IA1')
        need(b['boundary_identity'] == 'FIRST_TRANSPORT_ACTIVITY')
        ident(b['request_id']); ident(b['boot_id']); hash_value(b['context_sha256'])
        for name, sample in (('lower_clock', b['lower_ms']), ('upper_clock', b['upper_ms'])):
            clock = b[name]
            exact(clock, ('schema', 'boot_id', 'monotonic_ms', 'offset_lower_ms',
                          'offset_upper_ms', 'measured_utc_ms'))
            need(clock['schema'] == 'G3_V5_FC1_ORIGINAL_CLOCK_IA1' and
                 clock['boot_id'] == b['boot_id'])
            integer(clock['monotonic_ms']); integer(clock['offset_lower_ms'])
            integer(clock['offset_upper_ms']); integer(clock['measured_utc_ms'])
            need(clock['offset_lower_ms'] <= clock['offset_upper_ms'] and
                 clock['monotonic_ms'] == sample and
                 total((clock['monotonic_ms'], clock['offset_lower_ms'])) <= clock['measured_utc_ms'] <=
                 total((clock['monotonic_ms'], clock['offset_upper_ms'])), 'CLOCK')
        need(max(b['lower_clock']['offset_lower_ms'], b['upper_clock']['offset_lower_ms']) <=
             min(b['lower_clock']['offset_upper_ms'], b['upper_clock']['offset_upper_ms']), 'CLOCK')
        for name in ('lower_ms', 'actual_start_ms', 'upper_ms', 'closed_ms',
                     'deadline_origin_ms', 'deadline_fixed_ms', 'dispatch_persisted_ms'):
            integer(b[name])
        integer(permission)
        need(type(b['durable_close']) is bool and type(b['receipt_complete']) is bool)
        need(b['durable_close'] and b['receipt_complete'], 'CUSTODY')
        need(permission <= b['lower_ms'] <= b['actual_start_ms'] <= b['upper_ms'] <= b['closed_ms'], 'CLOCK')
        need(b['upper_ms'] - b['lower_ms'] <= allowance, 'CLOCK')
        need(b['deadline_origin_ms'] <= b['dispatch_persisted_ms'] <= permission, 'CLOCK')
        need(b['closed_ms'] <= b['deadline_fixed_ms'] and
             b['deadline_fixed_ms'] <= total((b['deadline_origin_ms'], deadline)), 'BUDGET')
        if previous is not None:
            need(b['boot_id'] == previous['boot_id'] and
                 b['context_sha256'] == previous['context_sha256'], 'CLOCK')
            need(permission >= previous['closed_ms'], 'BUDGET')
            need(permission >= total((previous['upper_ms'], timing.spacing_ms)), 'CLOCK')
        previous = b
    need(bounds[-1]['closed_ms'] - bounds[0]['actual_start_ms'] <= 10800000, 'CLOCK')


@dataclass(frozen=True)
class CaptureCustody:
    outcome: str | None
    terminal_head: str | None
    receipt_head: str | None
    complete: bool


def check_synthetic_capture_custody(records, acknowledged_heads, request_id, context_sha256):
    """Replay a supplied IA1 pair; absent or unknown durability keeps custody held.

    The acknowledgement set is an untrusted synthetic input, not store proof.
    """
    need(type(records) is tuple and type(acknowledged_heads) is tuple)
    need(len(records) <= 2, 'CUSTODY')
    ident(request_id); hash_value(context_sha256)
    for head in acknowledged_heads: hash_value(head)
    need(len(set(acknowledged_heads)) == len(acknowledged_heads), 'CUSTODY')
    if not records:
        return CaptureCustody(None, None, None, False)
    terminal = parse(records[0], 65536)
    exact(terminal, ('schema', 'type', 'request_id', 'context_sha256', 'outcome',
                     'prior_hash', 'closed', 'accounted', 'witnessed', 'denied',
                     'overdelivery', 'intent_recorded'))
    need(terminal['schema'] == 'G3_V5_FC1_SESSION_IA1' and terminal['type'] == 'TERMINAL')
    need(terminal['request_id'] == request_id and terminal['context_sha256'] == context_sha256, 'DEPENDENCY')
    hash_value(terminal['prior_hash'])
    outcome = terminal['outcome']
    need(outcome in ('SUCCESS', 'FAILED', 'REFUSED'))
    for name in ('closed', 'accounted', 'witnessed', 'denied', 'overdelivery', 'intent_recorded'):
        need(type(terminal[name]) is bool)
    need(terminal['intent_recorded'], 'CUSTODY')
    if outcome == 'SUCCESS':
        need(terminal['closed'] and terminal['accounted'] and terminal['witnessed'] and
             not terminal['denied'] and not terminal['overdelivery'], 'CUSTODY')
    elif outcome == 'FAILED':
        need(terminal['closed'] and terminal['accounted'] and not terminal['overdelivery'], 'CUSTODY')
    else:
        need(not terminal['closed'] and not terminal['accounted'] and
             not terminal['witnessed'], 'CUSTODY')
    terminal_head = digest(records[0])
    if len(records) == 1:
        return CaptureCustody(outcome, terminal_head, None, False)
    receipt = parse(records[1], 65536)
    exact(receipt, ('schema', 'type', 'request_id', 'context_sha256', 'outcome',
                    'prior_hash', 'session_terminal_head', 'store_head', 'budget_head',
                    'denial_head', 'shared_head'))
    need(receipt['schema'] == 'G3_V5_FC1_CAPTURE_RECEIPT_IA1' and
         receipt['type'] == 'CAPTURE_RECEIPT')
    need(receipt['request_id'] == request_id and receipt['context_sha256'] == context_sha256 and
         receipt['outcome'] == outcome, 'DEPENDENCY')
    for name in ('prior_hash', 'session_terminal_head', 'store_head', 'budget_head',
                 'denial_head', 'shared_head'):
        hash_value(receipt[name])
    need(receipt['prior_hash'] == terminal_head and
         receipt['session_terminal_head'] == terminal_head, 'DEPENDENCY')
    receipt_head = digest(records[1])
    return CaptureCustody(outcome, terminal_head, receipt_head,
                          terminal_head in acknowledged_heads and receipt_head in acknowledged_heads)


@dataclass(frozen=True)
class StreamTrace:
    read_calls: int
    received_bytes: int
    eof: bool
    complete: bool
    poisoned: bool
    debit_bytes: int


def check_synthetic_stream_trace(cap, expected_bytes, k, reads, close_confirmed,
                                 account_durable, canceled=False, prior_uncertain_debit=0):
    """Account every supplied read/queue/late observation without clipping excess.

    A real adapter must expose these observations; this pure check cannot do so.
    """
    integer(cap, 1, 4*MIB); integer(expected_bytes, 1, cap)
    integer(k, (cap + 65535)//65536 + 1, min(cap + 1, 65536), 'BUDGET')
    integer(prior_uncertain_debit)
    need(prior_uncertain_debit == 0 or prior_uncertain_debit >= cap+65536, 'BUDGET')
    need(type(reads) is tuple and type(close_confirmed) is bool and
         type(account_durable) is bool and type(canceled) is bool)
    count = received = payload = 0
    eof = poisoned = stopped = False
    for read in reads:
        exact(read, ('kind', 'requested_max', 'body_bytes', 'eager_bytes',
                     'queued_bytes', 'late_bytes', 'framing_complete', 'queue_exhausted'))
        need(read['kind'] in ('DATA', 'EOF', 'EMPTY', 'ERROR'))
        for name in ('requested_max', 'body_bytes', 'eager_bytes', 'queued_bytes', 'late_bytes'):
            integer(read[name])
        need(type(read['framing_complete']) is bool and type(read['queue_exhausted']) is bool)
        count += 1  # charged before interpretation, including errors and EOF
        received = total((received, read['body_bytes'], read['eager_bytes'],
                          read['queued_bytes'], read['late_bytes']))
        if count > k or stopped or canceled:
            poisoned = True
        maximum = 1 if payload == expected_bytes else min(65536, expected_bytes - payload)
        if not 1 <= read['requested_max'] <= maximum:
            poisoned = True
        if read['body_bytes'] > read['requested_max'] or read['eager_bytes'] or read['queued_bytes'] or read['late_bytes']:
            poisoned = True
        if read['kind'] == 'DATA':
            if read['body_bytes'] == 0: poisoned = True
            payload = total((payload, read['body_bytes']))
            if payload > expected_bytes: poisoned = True
        elif read['kind'] == 'EOF':
            if read['body_bytes'] or payload != expected_bytes or not read['framing_complete'] or not read['queue_exhausted']:
                poisoned = True
            else:
                eof = True
            stopped = True
        else:
            poisoned = True
            stopped = True
        if received > cap + 65536:
            poisoned = True
    complete = eof and close_confirmed and account_durable and not poisoned
    # Uncertain accounting retains the full reservation and one global G_rx.
    debit = max(prior_uncertain_debit, received if close_confirmed and account_durable
                else max(received, total((cap, 65536))))
    return StreamTrace(count, received, eof, complete, poisoned, debit)
