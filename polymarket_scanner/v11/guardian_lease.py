"""Linux process/lease fences for the independent, trusted PAPER guardian.

This is local failure isolation, not separate-UID custody or live cancel authority.
Only current, original records can satisfy a lease; replay never renews it.
"""
from dataclasses import asdict, dataclass
from pathlib import Path

from .evidence import EvidenceError, Limits, digest, finite, identity, sha
from .runtime_health import host_stamp


VERSION = 'alpha_v11_paper_guardian_v1'


@dataclass(frozen=True)
class GuardianPolicy:
    version: str
    lease_seconds: float = 2.
    interval_seconds: float = .25
    maximum_wall_step_seconds: float = .5
    maximum_intents: int = 8

    def __post_init__(self):
        identity(self.version)
        if (not .1 <= finite(self.interval_seconds) <= 2
                or not 2*self.interval_seconds <= finite(self.lease_seconds) <= 10
                or not 0 < finite(self.maximum_wall_step_seconds) <= 2
                or type(self.maximum_intents) is not int or not 1 <= self.maximum_intents <= 16):
            raise EvidenceError('GUARDIAN_POLICY_BOUND')


def journal_key(account_id):
    return 'paper-guardian:'+digest(identity(account_id))


def process_identity(pid):
    """Bind PID reuse to Linux boot/start ticks/UID; no command-line disclosure."""
    if type(pid) is not int or pid <= 0: raise EvidenceError('GUARDIAN_PROCESS_IDENTITY')
    try:
        path = Path('/proc')/str(pid)
        raw = (path/'stat').read_text()
        fields = raw[raw.rindex(')')+2:].split()
        boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        result = dict(pid=pid, start_ticks=int(fields[19]), uid=path.stat().st_uid, boot_id=boot)
        state = fields[0]
    except (OSError, ValueError, IndexError):
        raise EvidenceError('GUARDIAN_PROCESS_UNAVAILABLE') from None
    validate_process_identity(result)
    if state in {'T', 't', 'Z', 'X', 'x'}: raise EvidenceError('GUARDIAN_PROCESS_NOT_RUNNING')
    return result


def validate_process_identity(value):
    if (type(value) is not dict or set(value) != {'pid','start_ticks','uid','boot_id'}
            or any(type(value[k]) is not int or value[k] < (0 if k == 'uid' else 1)
                   for k in ('pid','start_ticks','uid'))
            or type(value['boot_id']) is not str or len(value['boot_id']) != 36
            or any(c not in '0123456789abcdef-' for c in value['boot_id'])):
        raise EvidenceError('GUARDIAN_PROCESS_IDENTITY')


def config_digest(*, policy, account_policy_sha, health_config, worker, archive_limits):
    if not isinstance(policy, GuardianPolicy): raise EvidenceError('GUARDIAN_POLICY_REQUIRED')
    sha(account_policy_sha); sha(health_config); validate_process_identity(worker)
    if not isinstance(archive_limits, Limits): raise EvidenceError('GUARDIAN_ARCHIVE_LIMITS_REQUIRED')
    return digest(dict(policy=asdict(policy), account=account_policy_sha, health=health_config,
                       worker=worker, archive_limits=asdict(archive_limits)))


def validate_details(event_id, d):
    """Exact nonauthorizing telemetry schema, including its original configuration."""
    keys = {'version','config_sha256','account_id','account_policy_sha256','health_config',
            'policy','worker','process','stamp','generation','status','reasons','cursor','archive_limits',
            'cancel_intents','account_snapshot_id','pending_trigger','financial_authority','independent_guardian_commissioned'}
    if (type(d) is not dict or set(d) not in (keys, keys|{'broker_process'}) or d.get('version') != VERSION
            or event_id != journal_key(d.get('account_id'))
            or d.get('financial_authority') is not False or d.get('independent_guardian_commissioned') is not False
            or d.get('status') not in {'READY','GATED','STOPPED'}):
        raise EvidenceError('GUARDIAN_STATUS_SCHEMA')
    policy = GuardianPolicy(**d['policy'])
    if policy.version.startswith('broker-policy:') and d['status']=='READY' and 'broker_process' not in d:
        raise EvidenceError('GUARDIAN_BROKER_IDENTITY_REQUIRED')
    expected = config_digest(policy=policy, account_policy_sha=d['account_policy_sha256'],
                             health_config=d['health_config'], worker=d['worker'], archive_limits=Limits(**d['archive_limits']))
    validate_process_identity(d['process']); identity(d['generation']); identity(d['cursor'] or '-')
    if 'broker_process' in d:
        validate_process_identity(d['broker_process'])
        if (d['broker_process'] in (d['process'], d['worker'])
                or d['broker_process']['boot_id'] != d['process']['boot_id']):
            raise EvidenceError('GUARDIAN_BROKER_IDENTITY')
    if (d['config_sha256'] != expected or d['process'] == d['worker']
            or type(d['stamp']) is not dict or set(d['stamp']) != {'wall','monotonic','boot_id'}
            or d['stamp']['boot_id'] != d['process']['boot_id']
            or type(d['reasons']) is not list or len(d['reasons']) > 16
            or any(not isinstance(r,str) or not r or len(r)>160 for r in d['reasons'])
            or (not d['reasons']) != (d['status'] == 'READY')
            or type(d['cancel_intents']) is not dict or len(d['cancel_intents']) > policy.maximum_intents
            or bool(d['cancel_intents']) != (d['pending_trigger'] is not None)
            or d['cancel_intents'] and d['account_snapshot_id'] is None
            or d['status'] == 'READY' and d['cancel_intents']):
        raise EvidenceError('GUARDIAN_STATUS_BINDING')
    finite(d['stamp']['wall']); finite(d['stamp']['monotonic'])
    if d['account_snapshot_id'] is not None: identity(d['account_snapshot_id'])
    if d['pending_trigger'] is not None: identity(d['pending_trigger'])
    for key,value in d['cancel_intents'].items(): identity(key); sha(value)
    return policy


def check_lease(store, row, *, account_id, account_policy_sha, required_config=None):
    d = row['body']['details']; p = validate_details(row['event_id'], d)
    if (d['account_id'] != account_id or d['account_policy_sha256'] != account_policy_sha
            or required_config is not None and d['config_sha256'] != required_config):
        raise EvidenceError('GUARDIAN_ACCOUNT_OR_CONFIG_CHANGED')
    stamp = host_stamp(store); saved = d['stamp']
    if (d['status'] != 'READY' or stamp['boot_id'] != saved['boot_id']
            or not 0 <= stamp['wall']-saved['wall'] < p.lease_seconds
            or not 0 <= stamp['monotonic']-saved['monotonic'] < p.lease_seconds
            or abs((stamp['wall']-saved['wall'])-(stamp['monotonic']-saved['monotonic'])) > p.maximum_wall_step_seconds
            or process_identity(d['process']['pid']) != d['process']
            or 'broker_process' in d and process_identity(d['broker_process']['pid']) != d['broker_process']):
        raise EvidenceError('GUARDIAN_LEASE_GATED')


def admission_heads(store, *, account_id, account_policy_sha, required_config=None):
    if required_config is not None: sha(required_config)
    key = journal_key(account_id)
    row = store.latest(kind='RUNTIME_STATUS', event_id=key)
    if row is None:
        if required_config is not None: raise EvidenceError('GUARDIAN_REQUIRED_BEFORE_OPENING')
    else:
        check_lease(store, row, account_id=account_id, account_policy_sha=account_policy_sha,
                    required_config=required_config)
    return (('RUNTIME_STATUS', key, row['seq'] if row else 0),)


def check_transaction(store, db, kind, event_id, body, heads):
    """After SQLite acquired its write lock, recheck time without another connection.

    Only opening paths pass guardian heads. Safety cancellation/retirement and
    receipt reconciliation deliberately do not take this lease dependency.
    """
    d = body.get('details', {}); request = d.get('request', {})
    opening = (kind == 'COORDINATOR_EVENT' and event_id == 'v11-paper-account-state'
               and (request.get('action') == 'COORDINATE'
                    or request.get('action') == 'TRANSITION' and request.get('status') == 'SUBMITTING'))
    maker = kind == 'MEASUREMENT' and d.get('version') == 'alpha_v11_maker_research_v1' and request.get('action') != 'RETIRE'
    if not opening and not maker: return
    for k,e,seq in heads:
        if k == 'RUNTIME_STATUS' and e.startswith('paper-guardian:') and seq:
            row = store._decode(db.execute('SELECT * FROM v11_records WHERE seq=?', (seq,)).fetchone())
            saved = row['body']['details']
            check_lease(store, row, account_id=saved['account_id'], account_policy_sha=saved['account_policy_sha256'])
