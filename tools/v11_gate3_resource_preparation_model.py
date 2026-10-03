"""Pure supplied-byte preparation REFUSAL model. No allocator or runtime adapter.

Only emergency intent and a same-inode refusal report are simulated. Every
constructor is forbidden: the remainder's inode/lifetime map is unimplemented.
Caller pins/facts are fabricated assertions, never independent custody evidence.
"""

from dataclasses import dataclass, field, fields
import hashlib
import json

MAX_BYTES = 32768
MAX_EVENTS = 16
MAX_INT = 2**63 - 1
DISK_FLOOR = 2 * 1024**3
MEMORY_FLOOR = 512 * 1024**2
REPORT_BYTES = 16 * 1024**2
INTENT_BYTES = 4096
TERMINAL_BYTES = 4096
# Intent, partial-acquisition, refusal and recovery records, retained together.
EMERGENCY_BYTES = 4 * 4096


class Invalid(ValueError):
    pass


def _require(ok):
    if not ok:
        raise Invalid('closed bounded input required')


def _uint(value):
    _require(type(value) is int and 0 <= value <= MAX_INT)
    return value


def _sum(*values):
    return _uint(sum(values))


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result)
        result[key] = value
    return result


def _bad(_):
    raise Invalid('noninteger number')


def _integer(value):
    _require(len(value) <= 19)
    return _uint(int(value))


def _parse(raw):
    _require(type(raw) is bytes and 0 < len(raw) <= MAX_BYTES)
    # Bound nesting before json allocates nested objects; quoted braces ignored.
    depth = 0
    quoted = escaped = False
    for byte in raw:
        if quoted:
            if escaped:
                escaped = False
            elif byte == 92:
                escaped = True
            elif byte == 34:
                quoted = False
        elif byte == 34:
            quoted = True
        elif byte in (91, 123):
            depth += 1
            _require(depth <= 6)
        elif byte in (93, 125):
            depth -= 1
            _require(depth >= 0)
    value = json.loads(raw, object_pairs_hook=_pairs, parse_int=_integer,
                       parse_float=_bad, parse_constant=_bad)
    _require(json.dumps(value, sort_keys=True, separators=(',', ':'),
                        ensure_ascii=True).encode('ascii') == raw)
    return value


def _shape(value, names):
    _require(type(value) is dict and set(value) == set(names))


def _text(value):
    _require(type(value) is str and 0 < len(value) <= 96 and value.isascii())
    return value


def _digest(value):
    _require(type(value) is str and len(value) == 64 and
             all(c in '0123456789abcdef' for c in value))
    return value


def _record(cls, value):
    _shape(value, (f.name for f in fields(cls)))
    for f in fields(cls):
        item = value[f.name]
        if f.type is int:
            _uint(item)
        elif f.type is str:
            _text(item)
        else:
            _require(f.type is bool and type(item) is bool)
    return cls(**value)


@dataclass(frozen=True, slots=True)
class Projection:
    # Exact narrow projection supplied by caller, not a new validated-plan export.
    source_budget_sha256: str
    manifest_sha256: str
    runtime_disk_bytes: int
    runtime_memory_bytes: int
    v4_quota_formula_bytes: int
    report_bytes: int
    disk_floor_bytes: int
    memory_floor_bytes: int
    uncovered_disk_bytes: int
    uncovered_memory_bytes: int
    new_inodes: int
    fresh_ceiling_checks: bool


@dataclass(frozen=True, slots=True)
class Ownership:
    creator_process: str  # Synthetic PID + start identity, never PID alone.
    current_process: str
    boot: str
    recorded_boot: str
    independent_head: str
    journal_head: str
    history: str  # Only RECONCILED; missing history is never genesis.
    predecessor: str  # Only EXITED_REAPED_ALL_HOLDERS, not lease expiration.
    inherited_handles: bool
    exclusive_owner: bool


@dataclass(frozen=True, slots=True)
class Domain:
    namespace: str
    mount: str
    device: str
    pool: str
    quota: str
    root: str
    backing: str
    scope: str
    ancestry: str
    aliases: str
    quota_visibility: str
    monitoring_bound: str
    free_disk_bytes: int
    available_memory_bytes: int
    quota_headroom_bytes: int
    pool_headroom_bytes: int
    free_inodes: int
    ancestor_headroom_bytes: int
    foreign_disk_bytes: int
    foreign_memory_bytes: int
    outstanding_disk_bytes: int
    outstanding_memory_bytes: int


@dataclass(frozen=True, slots=True)
class Emergency:
    identity: str
    backing: str
    persistence: str
    available_bytes: int
    memory_bytes: int
    metadata_bytes: int
    retained: bool


@dataclass(frozen=True, slots=True)
class SyscallResult:
    """A syscall double's DATA, never an executable callback or real syscall."""
    op: str
    outcome: str
    identity: str
    backing: str
    count: int
    logical_size: int


@dataclass(frozen=True, slots=True)
class Refusal:
    reason: str
    state: str
    trace: tuple[str, ...] = ()
    projection_sha256: str = ''
    scenario_sha256: str = ''
    held_report_bytes: int = 0
    outstanding_disk_bytes: int = 0
    original_reason: str = 'CONSTRUCTOR_ALLOCATION_MAP_UNIMPLEMENTED'
    mode: str = field(default='SYNTHETIC_ONLY', init=False)
    status: str = field(default='UNQUALIFIED', init=False)
    g3l: str = field(default='NO_GO', init=False)
    qualification_credit: int = field(default=0, init=False)
    execution_authority: bool = field(default=False, init=False)
    provider_authority: bool = field(default=False, init=False)
    host_authority: bool = field(default=False, init=False)
    resource_qualification: bool = field(default=False, init=False)
    allocation_authority: bool = field(default=False, init=False)
    shadow_authority: bool = field(default=False, init=False)
    constructors_allowed: bool = field(default=False, init=False)
    released_bytes: int = field(default=0, init=False)
    dispatch_calls: int = field(default=0, init=False)
    denominator: int = field(default=2713, init=False)
    accounting: str = field(default='NO_RELEASE_UNKNOWN_CLAIMS_RETAINED', init=False)


def _scope(d):
    return (d.backing == 'LOCAL_PERSISTENT_NATIVE' and
            d.scope == 'ALL_CONSUMERS_BOUNDED' and
            d.ancestry == 'COMPLETE_SINGLE_ANCESTOR' and
            d.aliases == 'SINGLE_MOUNT_NO_ALIASES' and
            d.quota_visibility == 'KNOWN' and
            d.monitoring_bound == 'ENFORCED_NOT_SAMPLED')


def _identity(d):
    return (d.namespace, d.mount, d.device, d.pool, d.quota, d.root)


def _fit(d, disk, memory, p):
    disk_debit = _sum(disk, d.foreign_disk_bytes, d.outstanding_disk_bytes)
    memory_debit = _sum(memory, d.foreign_memory_bytes,
                        d.outstanding_memory_bytes)
    return (d.free_disk_bytes >= _sum(disk_debit, p.disk_floor_bytes) and
            d.available_memory_bytes >= _sum(memory_debit, p.memory_floor_bytes) and
            d.quota_headroom_bytes >= disk_debit and
            d.pool_headroom_bytes >= _sum(disk_debit, p.disk_floor_bytes) and
            d.free_inodes >= p.new_inodes and
            d.ancestor_headroom_bytes >= memory_debit)


def evaluate(projection_raw, scenario_raw, expected_projection_sha256):
    """Deterministic bounded replay; returns a refusal even for a complete trace.

    Both inputs must be exact canonical JSON bytes. The separate digest pins
    bytes only; caller-supplied source/manifest pins are not verified provenance.
    No imports of runtime, host probes, allocator, clocks, locks or transports.
    """
    try:
        return _evaluate(projection_raw, scenario_raw, expected_projection_sha256)
    except (Invalid, ValueError, TypeError, UnicodeError, RecursionError):
        return Refusal('INPUT_INVALID', 'UNCERTAIN_HELD')


def _evaluate(projection_raw, scenario_raw, expected):
    _digest(expected)
    p = _record(Projection, _parse(projection_raw))
    digest = hashlib.sha256(projection_raw).hexdigest()
    _require(digest == expected)
    _digest(p.source_budget_sha256)
    _digest(p.manifest_sha256)
    _require(p.report_bytes == REPORT_BYTES and
             p.runtime_disk_bytes >= REPORT_BYTES and
             p.disk_floor_bytes >= DISK_FLOOR and
             p.memory_floor_bytes >= MEMORY_FLOOR and p.new_inodes > 0)
    s = _parse(scenario_raw)
    _shape(s, ('version', 'owner', 'before', 'after', 'emergency',
               'report_identity', 'events'))
    _require(type(s['version']) is int and s['version'] == 1)
    o = _record(Ownership, s['owner'])
    before = _record(Domain, s['before'])
    after = _record(Domain, s['after'])
    emergency = _record(Emergency, s['emergency'])
    report_identity = _text(s['report_identity'])
    # Directory fsync and the two writable files require distinct inode roles.
    _require(len({before.root, report_identity, emergency.identity}) == 3)
    events = s['events']
    _require(type(events) is list and len(events) <= MAX_EVENTS)
    events = tuple(_record(SyscallResult, e) for e in events)
    for value in (o.creator_process, o.current_process, o.boot, o.recorded_boot,
                  o.independent_head, o.journal_head, emergency.identity, report_identity,
                  *_identity(before), *_identity(after)):
        _require(value.upper() not in ('UNKNOWN', 'NONE', 'UNQUALIFIED', 'MISSING'))
    trace = []
    held = 0
    # Deliberately sum both estimates: no overlap proof is supplied in this slice.
    disk = _sum(p.runtime_disk_bytes, p.v4_quota_formula_bytes,
                p.uncovered_disk_bytes, emergency.metadata_bytes)
    memory = _sum(p.runtime_memory_bytes, p.uncovered_memory_bytes,
                  emergency.memory_bytes)

    # Validate arithmetic for both supplied observations before simulating any
    # write. Malformed post-state must never erase a previously simulated hold.
    before_fits = _fit(before, disk, memory, p)
    after_fits = _fit(after, disk - REPORT_BYTES, memory, p)

    def refuse(reason, state='UNCERTAIN_HELD'):
        return Refusal(reason, state, tuple(trace), digest,
                       hashlib.sha256(scenario_raw).hexdigest(), held, disk - held)

    # Before even a modeled lock acquisition: a fork inherits locked mutexes.
    if o.creator_process != o.current_process or o.inherited_handles:
        return refuse('INHERITED_OWNER')
    if (o.boot != o.recorded_boot or not o.exclusive_owner or
            o.predecessor != 'EXITED_REAPED_ALL_HOLDERS'):
        return refuse('PREDECESSOR_UNFENCED')
    if o.history != 'RECONCILED' or o.independent_head != o.journal_head:
        return refuse('HISTORY_UNRECONCILED')
    trace.append('READ_ONLY_RECOVERY_FENCED')
    if not _scope(before):
        return refuse('DOMAIN_SCOPE_UNKNOWN')
    if not p.fresh_ceiling_checks:
        return refuse('BUDGET_CEILING')
    if not before_fits:
        return refuse('PROSPECTIVE_FLOOR_QUOTA_OR_INODE')
    trace.append('PROSPECTIVE_RESOURCES_CHECKED')
    # Pre-existing emergency bytes are already absent from free space. No refund
    # or double debit; their memory/metadata costs above remain prospective.
    if (emergency.backing != 'NATIVE_KEEP_SIZE_OWNED' or
            emergency.persistence != 'SUPPLIED_DURABLE' or
            not emergency.retained or emergency.available_bytes < EMERGENCY_BYTES or
            emergency.memory_bytes < EMERGENCY_BYTES or
            emergency.metadata_bytes < INTENT_BYTES):
        return refuse('EMERGENCY_UNPROVEN')
    trace.append('PREEXISTING_EMERGENCY_CHECKED')

    # This is a fixed command script interpreted only against supplied doubles.
    # No generic callback, path opening, unlink/reallocate or constructor exists.
    script = (
        ('INTENT_WRITE', emergency.identity, 'NATIVE_KEEP_SIZE_OWNED', INTENT_BYTES, INTENT_BYTES),
        ('INTENT_FSYNC', emergency.identity, 'UNCHANGED', 0, INTENT_BYTES),
        ('REPORT_ALLOCATE', report_identity, 'NATIVE_EXCLUSIVE', REPORT_BYTES, REPORT_BYTES),
        ('REPORT_FSYNC', report_identity, 'UNCHANGED', 0, REPORT_BYTES),
        ('REPORT_DIR_FSYNC', before.root, 'UNCHANGED', 0, 0),
        ('REFUSAL_WRITE', report_identity, 'IN_PLACE', TERMINAL_BYTES, REPORT_BYTES),
        ('REFUSAL_TRUNCATE', report_identity, 'SHRINK_SAME_INODE', 0, TERMINAL_BYTES),
        ('REFUSAL_FSYNC', report_identity, 'UNCHANGED', 0, TERMINAL_BYTES),
        ('REFUSAL_LINK', report_identity, 'SAME_INODE_NO_REPLACE', 0, TERMINAL_BYTES),
        ('REFUSAL_DIR_FSYNC', before.root, 'UNCHANGED', 0, 0),
    )
    for index, expected_event in enumerate(script):
        if index == 5:
            if (_identity(after) != _identity(before) or not _scope(after) or
                    not after_fits):
                return refuse('POST_ALLOCATION_CUSTODY_LOST')
            trace.append('POST_ALLOCATION_CHECKED_CONSTRUCTORS_REFUSED')
        if index >= len(events):
            return refuse('INCOMPLETE_SYSCALL_TRACE')
        event = events[index]
        if event.op != expected_event[0]:
            return refuse('OPERATION_ORDER_OR_CONSTRUCTOR_REFUSED')
        trace.append(event.op + ':ATTEMPTED')
        if event.outcome != 'OK':
            return refuse('SYSCALL_' + event.op + '_FAILED')
        actual = (event.op, event.identity, event.backing, event.count, event.logical_size)
        if actual != expected_event:
            return refuse('ALLOCATION_OR_PERSISTENCE_SEMANTICS')
        # Native allocation alone is not durable custody; both fsyncs required.
        if index == 4:
            held = REPORT_BYTES
        trace.append(event.op + ':SUPPLIED_OK')
    if len(events) != len(script):
        return refuse('TRAILING_OPERATION_REFUSED')
    # Truncation/linking never recredits backing. Both names and all claims held.
    return refuse('CONSTRUCTOR_ALLOCATION_MAP_UNIMPLEMENTED',
                  'SYNTHETIC_REFUSAL_PERSISTED_HELD')
