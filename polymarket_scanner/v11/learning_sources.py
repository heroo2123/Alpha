"""Bounded read-only learning snapshots and receipt-level derivation manifests.

No schema/WAL/checkpoint pragma, source update or source-head substitution is
performed. Offline source expansion has a separate bound; runtime decision and
feature-DAG limits remain unchanged.
"""
from contextlib import contextmanager, closing
import copy
import sqlite3
import time

from .evidence import EvidenceError, canonical, digest, identity, sha


class LearningSourceView:
    def __init__(self, store, db, deadline, monotonic):
        self.namespace = store.namespace
        self.path = store.path
        self._store, self._db = store, db
        self._deadline, self._clock = deadline, monotonic
        self._cache = {}
        self._bytes = 0
        self.derivation_cache = {}
        self.snapshot_seq = db.execute('SELECT COALESCE(MAX(seq),0) FROM v11_records').fetchone()[0]

    def check(self):
        if self._clock() >= self._deadline:
            raise EvidenceError('LEARNING_SOURCE_VIEW_TIME_BOUND')

    def get(self, record_id):
        identity(record_id)
        self.check()
        if record_id not in self._cache:
            row = self._db.execute('SELECT * FROM v11_records WHERE record_id=?', (record_id,)).fetchone()
            if row is None:
                raise EvidenceError('EVIDENCE_MISSING')
            self._bytes += len(row['body'].encode())
            if len(self._cache) >= 8192 or self._bytes > 8*1024**2:
                raise EvidenceError('LEARNING_SOURCE_VIEW_READ_BOUND')
            self._cache[record_id] = self._store._decode(row)
        self.check()
        return copy.deepcopy(self._cache[record_id])

    def latest_source(self, *, kind, event_id, provider, source_identity, as_of):
        """Exact source revision within this same read snapshot and cutoff."""
        from .evidence import finite
        for value in (kind,event_id,provider,source_identity): identity(value)
        self.check()
        # Source identities live in the canonical body; this uses the existing
        # bounded archive, read-only transaction and SQLite progress deadline.
        row=self._db.execute('SELECT record_id FROM v11_records WHERE kind=? AND event_id=? AND available_at<=? '
            'AND json_extract(body,\'$.provider\')=? AND json_extract(body,\'$.source_identity\')=? '
            'ORDER BY seq DESC LIMIT 1',(kind,event_id,finite(as_of),provider,source_identity)).fetchone()
        return self.get(row['record_id']) if row is not None else None


@contextmanager
def learning_source_view(store, *, deadline=None, monotonic=time.monotonic):
    started = monotonic()
    deadline = started+10. if deadline is None else min(deadline, started+10.)
    with closing(sqlite3.connect(store.path.as_uri()+'?mode=ro', uri=True, timeout=1.)) as db:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        db.set_progress_handler(lambda: int(monotonic() >= deadline), 1000)
        try:
            yield LearningSourceView(store, db, deadline, monotonic)
        except sqlite3.OperationalError as exc:
            if 'interrupted' in str(exc).lower():
                raise EvidenceError('LEARNING_SOURCE_VIEW_TIME_BOUND') from None
            raise
        finally:
            db.set_progress_handler(None, 0)
            db.rollback()


def _references(row):
    p = row['body'].get('payload', {})
    if not isinstance(p, dict):
        raise EvidenceError('SOURCE_DERIVATION_PAYLOAD')
    raw = 'raw_evidence_id' in p or 'raw_evidence_sha256' in p
    fields = 'field_references' in p
    derived = 'dependencies' in p
    qc = row['kind'] == 'PWS_OBSERVATION' and row['body']['provider'] == 'ALPHA_PWS_QC'
    version = p.get('version')
    if sum((raw,fields,derived,qc))>1:
        raise EvidenceError('SOURCE_DERIVATION_AMBIGUOUS_RELATION')
    # These are archived format contracts, not permissions or source authority.
    if version == 'alpha_v11_gefs_linear_day_v1' and not fields:
        raise EvidenceError('SOURCE_DERIVATION_MISSING')
    if version in {'alpha_v11_gefs_field_v1', 'alpha_v11_archived_gefs_normalization_v1'} and not raw:
        raise EvidenceError('SOURCE_DERIVATION_MISSING')
    if version in {'alpha_v11_physical_model_input_v1', 'alpha_v11_gefs_remaining_path_v1'} and not derived:
        raise EvidenceError('SOURCE_DERIVATION_MISSING')
    if fields:
        refs = p['field_references']
        if (raw or version != 'alpha_v11_gefs_linear_day_v1' or row['kind'] != 'MODEL'
                or type(refs) is not list or not 1 <= len(refs) <= 341
                or any(type(r) is not dict or set(r) != {'id','sha256','source_identity'} for r in refs)):
            raise EvidenceError('SOURCE_DERIVATION_FIELDS_INVALID')
    elif raw:
        if 'raw_evidence_id' not in p or 'raw_evidence_sha256' not in p:
            raise EvidenceError('SOURCE_DERIVATION_RAW_BINDING_REQUIRED')
        refs = [dict(id=p['raw_evidence_id'], sha256=p['raw_evidence_sha256'])]
    elif derived or qc:
        refs = p.get('dependencies') if derived else p.get('source_captures')
        if (row['kind'] not in {'MODEL','FEATURES','PWS_OBSERVATION'}
                or type(refs) is not list or not (0 if qc else 1) <= len(refs) <= 64
                or any(type(r) is not dict or set(r) != {'id','sha256'} for r in refs)):
            raise EvidenceError('SOURCE_DERIVATION_DEPENDENCIES_INVALID')
    elif version == 'alpha_v11_archived_remaining_coverage_v1':
        if row['kind'] != 'FEATURES':
            raise EvidenceError('SOURCE_DERIVATION_COVERAGE_INVALID')
        refs = [dict(id=p.get('observation_id'), sha256=p.get('observation_sha256'))]
    else:
        return []
    for ref in refs:
        identity(ref['id']); sha(ref['sha256'])
        if 'source_identity' in ref:
            identity(ref['source_identity'])
    if len({r['id'] for r in refs}) != len(refs):
        raise EvidenceError('SOURCE_DERIVATION_DUPLICATE')
    return refs


def source_derivation(store, roots, *, event_id, cutoff):
    """Verify original hashes/times down to raw receipts, retaining every version.

The existing 256-node feature DAG is untouched. This additional source graph is
bounded to 1024 records, 2048 edges, 512 KiB of metadata and two seconds. It can
cover one complete 341-field GEFS path plus its raw records without giving the
runtime a larger decision/CAS budget. Larger multi-model jobs remain gated.
"""
    roots = [r for r in roots if _references(r)]
    if not roots:
        return None
    key = digest([sorted((r['id'],r['sha256']) for r in roots),event_id,cutoff])
    cache = getattr(store, 'derivation_cache', {})
    if key in cache:
        if hasattr(store, 'check'): store.check()
        return copy.deepcopy(cache[key])
    deadline = time.monotonic()+2.
    pending, seen, nodes, edges = list(roots), set(), [], 0
    while pending:
        if time.monotonic() >= deadline:
            raise EvidenceError('SOURCE_DERIVATION_TIME_BOUND')
        row = pending.pop()
        if row['id'] in seen:
            continue
        if len(seen) >= 1024:
            raise EvidenceError('SOURCE_DERIVATION_RECORD_BOUND')
        seen.add(row['id']); body = row['body']
        if (row['event_id'] != event_id or row['kind'] not in {'MODEL','FEATURES','OFFICIAL_OBSERVATION','PWS_OBSERVATION','BOOK','TRADE','STATION_METADATA','RULES'}
                or body['available_at'] > cutoff or body['recorded_at'] > cutoff
                or body.get('evidence_class') not in {'PUBLIC_OBSERVED','SYNTHETIC'}):
            raise EvidenceError('NONCAUSAL_SOURCE_DERIVATION')
        refs = _references(row); edges += len(refs)
        if edges > 2048:
            raise EvidenceError('SOURCE_DERIVATION_EDGE_BOUND')
        nodes.append(dict(id=row['id'], sha256=row['sha256'], seq=row['seq'], kind=row['kind'],
            **{k:body[k] for k in ('provider','source_identity','revision','observed_at','issued_at','published_at',
                                  'received_at','available_at','recorded_at','evidence_class')}, dependencies=refs))
        for ref in refs:
            child = store.get(ref['id'])
            raw_relation = 'raw_evidence_id' in body['payload'] or 'field_references' in body['payload']
            if (child['sha256'] != ref['sha256'] or child['seq'] >= row['seq']
                    or raw_relation and (child['kind'] != row['kind'] or child['body']['provider'] != body['provider'])
                    or child['body']['available_at'] > body['available_at']
                    or body['evidence_class']=='PUBLIC_OBSERVED' and child['body'].get('evidence_class')!='PUBLIC_OBSERVED'
                    or ('source_identity' in ref and child['body']['source_identity'] != ref['source_identity'])):
                raise EvidenceError('SOURCE_DERIVATION_BINDING')
            pending.append(child)
    manifest = dict(version='alpha_v11_learning_source_derivation_v1',
        roots=sorted(r['id'] for r in roots), nodes=sorted(nodes,key=lambda r:r['id']),
        node_count=len(nodes), edge_count=edges, source_truth_independently_attested=False)
    if len(canonical(manifest).encode()) > 512*1024:
        raise EvidenceError('SOURCE_DERIVATION_METADATA_BOUND')
    result = dict(manifest=manifest, sha256=digest(manifest))
    cache[key] = result
    return copy.deepcopy(result)
