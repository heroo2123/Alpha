"""Read-only inference access to separately approved model epochs.

No live pointer writer or approval installer is exposed to the learner/runtime.
The standalone host authority must be independently commissioned first.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import stat

from .certification import _root_custody
from .evidence import EvidenceError, canonical, digest, finite, sha
from .model_artifacts import ArtifactStore, MAX_BYTES, PinnedBundle, parse_data


STATE_PATH=Path('/var/lib/alpha-v11/model-authority/state.json')
OBJECT_ROOT=Path('/var/lib/alpha-v11/model-authority/objects')


def protected_state() -> dict:
    try:
        before=_root_custody(STATE_PATH)
        fd=os.open(STATE_PATH,os.O_RDONLY|os.O_NOFOLLOW)
        with os.fdopen(fd,'rb') as stream:
            after=os.fstat(stream.fileno())
            if (before.st_dev,before.st_ino)!=(after.st_dev,after.st_ino):
                raise EvidenceError('MODEL_STATE_READ_RACE')
            raw=stream.read(1024*1024+1)
        # State is allowed a larger bound than an individual numeric artifact.
        if len(raw)>1024*1024:
            raise EvidenceError('MODEL_STATE_BYTES_BOUND')
        envelope=parse_data(raw,max_bytes=1024*1024)
        if set(envelope)!={'state','sha256'} or digest(envelope['state'])!=envelope['sha256']:
            raise EvidenceError('MODEL_STATE_INTEGRITY')
        value=envelope['state']
        if (value.get('version')!='alpha_v11_model_authority_v1' or value.get('financial_authority') is not False
                or value.get('mode') not in {'V11_PAPER','V11_SHADOW'}
                or type(value.get('epoch')) is not int or not 1<=value['epoch']<=1000
                or not isinstance(value.get('events'),list) or len(value['events'])!=value['epoch']):
            raise EvidenceError('MODEL_STATE_SCHEMA_OR_UNCOMMISSIONED')
        sha(value['active_bundle_sha256'])
        sha(value['scope_key'])
        overlay=value['overlay']
        if (set(overlay)!={'size_multiplier','require_manual_review'}
                or not 0<=finite(overlay['size_multiplier'])<=1 or type(overlay['require_manual_review']) is not bool):
            raise EvidenceError('MODEL_OVERLAY_INVALID')
        previous=None
        for i,event in enumerate(value['events'],1):
            if event['epoch']!=i or event['previous_event_sha256']!=previous or event['financial_authority'] is not False:
                raise EvidenceError('MODEL_STATE_HISTORY')
            previous=digest(event)
        last=value['events'][-1]
        if last['active_bundle_sha256']!=value['active_bundle_sha256'] or last['overlay']!=overlay:
            raise EvidenceError('MODEL_STATE_HISTORY')
        return envelope
    except (OSError,ValueError,KeyError,TypeError) as exc:
        raise EvidenceError('PROTECTED_MODEL_STATE_UNAVAILABLE') from exc


class ApprovedArtifactReader(ArtifactStore):
    def __init__(self):
        self.root=OBJECT_ROOT
        for p in (self.root,*self.root.parents):
            info=p.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid!=0 or info.st_mode & 0o022:
                raise EvidenceError('APPROVED_MODEL_DIRECTORY_CUSTODY')

    def read(self, object_sha256):
        path=self._path(object_sha256)
        before=_root_custody(path)
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
        with os.fdopen(fd,'rb') as stream:
            after=os.fstat(stream.fileno())
            if (before.st_dev,before.st_ino)!=(after.st_dev,after.st_ino):
                raise EvidenceError('APPROVED_MODEL_READ_RACE')
            raw=stream.read(MAX_BYTES+1)
        value=parse_data(raw)
        if digest(value)!=object_sha256 or canonical(value).encode()!=raw:
            raise EvidenceError('APPROVED_MODEL_HASH_MISMATCH')
        return value

    def _write(self, value):
        raise EvidenceError('APPROVED_MODEL_READER_CANNOT_WRITE')


@dataclass(frozen=True)
class DecisionModelPin:
    epoch: int
    state_sha256: str
    scope_key: str
    mode: str
    bundle: PinnedBundle
    size_multiplier: float
    require_manual_review: bool


class ActiveModelRegistry:
    def pin(self, *, scope_key: str, mode: str) -> DecisionModelPin:
        envelope=protected_state()
        state=envelope['state']
        if state['scope_key']!=scope_key or state['mode']!=mode:
            raise EvidenceError('MODEL_SCOPE_OR_LEDGER_MISMATCH')
        bundle=ApprovedArtifactReader().pin(state['active_bundle_sha256'])
        reviews=[e['reviewed_bundle'] for e in state['events'] if e['action'] in {'PROMOTE','ROLLBACK'}
                 and e['active_bundle_sha256']==bundle.sha256]
        if not reviews or reviews[-1]['artifact_refs']!=bundle.payload['bundle']['artifacts']:
            raise EvidenceError('MODEL_BUNDLE_REVIEW_BINDING')
        overlay=state['overlay']
        return DecisionModelPin(state['epoch'],envelope['sha256'],scope_key,mode,bundle,
                                 overlay['size_multiplier'],overlay['require_manual_review'])

    def revalidate(self, pinned: DecisionModelPin) -> dict:
        envelope=protected_state()
        state=envelope['state']
        matched=(envelope['sha256']==pinned.state_sha256 and state['epoch']==pinned.epoch
                 and state['active_bundle_sha256']==pinned.bundle.sha256
                 and state['scope_key']==pinned.scope_key and state['mode']==pinned.mode
                 and state['overlay']['size_multiplier']==pinned.size_multiplier
                 and state['overlay']['require_manual_review']==pinned.require_manual_review)
        if matched:
            # Recheck immutable bytes before final decision use. A changed object
            # cannot be hidden behind an unchanged pointer or cached prediction.
            ApprovedArtifactReader().pin(pinned.bundle.sha256)
        return {'passed':matched and not state['overlay']['require_manual_review'],
                'reason':'PINNED_MODEL_VALID' if matched and not state['overlay']['require_manual_review'] else
                         'MODEL_MANUAL_REVIEW' if matched else 'MODEL_EPOCH_CHANGED_RECOMPUTE',
                'epoch':state['epoch'],'financial_authority':False}
