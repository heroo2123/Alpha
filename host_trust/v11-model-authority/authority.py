#!/usr/bin/env python3
"""Standalone, separately installed nonfinancial model-state authority.

Never imports candidate application code. The learner cannot install this helper,
write its root-custodied approvals/state, or grant a financial mode. This file is
preparation only until independently reviewed and installed by the owner.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import time


APPROVALS = Path('/etc/alpha-v11/approvals/model-bundles.json')
STATE = Path('/var/lib/alpha-v11/model-authority/state.json')
OBJECTS = Path('/var/lib/alpha-v11/model-authority/objects')
VERSION = 'alpha_v11_model_authority_v1'
MAX_BYTES = 1024*1024
COMPONENTS = {'FEATURES','PROBABILITY','CALIBRATION','EXECUTION_COST','STRATEGY_QUALITY'}


class AuthorityError(RuntimeError):
    pass


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def _sha(value, length=64):
    if not isinstance(value,str) or len(value)!=length or any(c not in '0123456789abcdef' for c in value):
        raise AuthorityError('DIGEST_INVALID')


def _number(value):
    import math
    if type(value) not in (float,int) or not math.isfinite(value) or value<0:
        raise AuthorityError('NUMBER_INVALID')
    return value


def _identity(value):
    if not isinstance(value,str) or not 1<=len(value)<=160 or any(ord(c)<32 for c in value):
        raise AuthorityError('IDENTITY_INVALID')


def parse(raw):
    if len(raw)>MAX_BYTES:
        raise AuthorityError('MODEL_AUTHORITY_BYTES_BOUND')
    def pairs(rows):
        out={}
        for k,v in rows:
            if k in out:
                raise AuthorityError('DUPLICATE_KEY')
            out[k]=v
        return out
    try:
        result=json.loads(raw,object_pairs_hook=pairs)
        canonical(result)
        return result
    except (ValueError,UnicodeError,RecursionError):
        raise AuthorityError('MODEL_AUTHORITY_JSON') from None


def custody(path, *, directory=False):
    if not path.is_absolute() or '..' in path.parts:
        raise AuthorityError('ABSOLUTE_PROTECTED_PATH_REQUIRED')
    for p in path.parents:
        info=p.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid!=0 or info.st_mode & 0o022:
            raise AuthorityError('MODEL_AUTHORITY_PARENT_CUSTODY')
    info=path.lstat()
    kind=stat.S_ISDIR if directory else stat.S_ISREG
    if not kind(info.st_mode) or info.st_uid!=0 or info.st_mode & 0o022:
        raise AuthorityError('MODEL_AUTHORITY_FILE_CUSTODY')
    return info


def read_protected(path, *, canonical_required=False):
    before=custody(path)
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    with os.fdopen(fd,'rb') as stream:
        current=os.fstat(stream.fileno())
        if (before.st_dev,before.st_ino)!=(current.st_dev,current.st_ino):
            raise AuthorityError('MODEL_AUTHORITY_READ_RACE')
        raw=stream.read(MAX_BYTES+1)
    value=parse(raw)
    if canonical_required and raw!=canonical(value).encode():
        raise AuthorityError('APPROVED_OBJECT_BYTES_NONCANONICAL')
    return value


def empty_state(scope_key, mode):
    _sha(scope_key)
    if mode not in {'V11_PAPER','V11_SHADOW'}:
        raise AuthorityError('FINANCIAL_MODE_FORBIDDEN')
    return {'version':VERSION,'epoch':0,'scope_key':scope_key,'mode':mode,
            'active_bundle_sha256':None,'previous_bundle_sha256':None,
            'overlay':{'size_multiplier':1.0,'require_manual_review':False},
            'events':[],'financial_authority':False}


def validate_state(state):
    required=set(empty_state('0'*64,'V11_PAPER'))
    if not isinstance(state,dict) or set(state)!=required or state['version']!=VERSION:
        raise AuthorityError('MODEL_STATE_SCHEMA')
    if state['mode'] not in {'V11_PAPER','V11_SHADOW'} or state['financial_authority'] is not False:
        raise AuthorityError('FINANCIAL_MODE_FORBIDDEN')
    _sha(state['scope_key'])
    if type(state['epoch']) is not int or not 0<=state['epoch']<=1000 or len(state['events'])!=state['epoch']:
        raise AuthorityError('MODEL_EPOCH_HISTORY_MISMATCH')
    for key in ('active_bundle_sha256','previous_bundle_sha256'):
        if state[key] is not None:
            _sha(state[key])
    overlay=state['overlay']
    if (set(overlay)!={'size_multiplier','require_manual_review'}
            or not 0<=_number(overlay['size_multiplier'])<=1 or type(overlay['require_manual_review']) is not bool):
        raise AuthorityError('MODEL_OVERLAY_SCHEMA')
    previous=None
    for i,event in enumerate(state['events'],1):
        if event['epoch']!=i or event['previous_event_sha256']!=previous:
            raise AuthorityError('MODEL_HISTORY_CHAIN')
        previous=digest(event)
    if state['events']:
        last=state['events'][-1]
        if last['active_bundle_sha256']!=state['active_bundle_sha256'] or last['overlay']!=overlay:
            raise AuthorityError('MODEL_CURRENT_HISTORY_MISMATCH')
    elif state!=empty_state(state['scope_key'],state['mode']):
        raise AuthorityError('UNREVIEWED_INITIAL_MODEL_STATE')


def transition(state, *, action, expected_state_sha256, now, reason, review=None,
               requested_bundle=None, size_multiplier=None):
    """Pure state transition, used only by the separate protected publisher."""
    validate_state(state)
    if digest(state)!=expected_state_sha256:
        raise AuthorityError('MODEL_STATE_CHANGED_RECOMPUTE')
    _number(now)
    _identity(reason)
    if state['events'] and now<state['events'][-1]['at']:
        raise AuthorityError('MODEL_AUTHORITY_CLOCK_REGRESSION')
    if state['epoch']>=1000:
        raise AuthorityError('MODEL_HISTORY_BOUND')
    result=json.loads(canonical(state))
    review_sha=None
    if action=='DEMOTE':
        if requested_bundle is not None or not 0<=_number(size_multiplier)<=state['overlay']['size_multiplier']:
            raise AuthorityError('AUTOMATIC_RECOVERY_FORBIDDEN')
        result['overlay']={'size_multiplier':size_multiplier,'require_manual_review':True}
    elif action in {'PROMOTE','ROLLBACK','RESTORE_OVERLAY'}:
        required={'review_id','reviewer','action','scope_key','mode','expected_epoch','parent_bundle_sha256',
                  'candidate_bundle_sha256','approved_at','expires_at','artifact_refs','runtime_contract',
                  'feature_schema_sha256','target','size_multiplier','approval_kind'}
        if not isinstance(review,dict) or set(review)!=required:
            raise AuthorityError('EXPLICIT_MODEL_REVIEW_REQUIRED')
        _identity(review['review_id'])
        _identity(review['reviewer'])
        if (review['approval_kind']!='NONFINANCIAL_MODEL_ONLY' or review['action']!=action
                or review['scope_key']!=state['scope_key'] or review['mode']!=state['mode']
                or review['expected_epoch']!=state['epoch']
                or review['parent_bundle_sha256']!=state['active_bundle_sha256']
                or not _number(review['approved_at'])<=now<_number(review['expires_at'])):
            raise AuthorityError('MODEL_REVIEW_SCOPE_PARENT_OR_TIME')
        if any(e.get('review_id')==review['review_id'] for e in state['events']):
            raise AuthorityError('MODEL_REVIEW_ALREADY_USED')
        if review['runtime_contract']!='alpha_v11_numeric_inference_v1' or set(review['artifact_refs'])!=COMPONENTS:
            raise AuthorityError('REVIEWED_BUNDLE_COMPATIBILITY')
        for value in review['artifact_refs'].values():
            _sha(value)
        _sha(review['feature_schema_sha256'])
        _sha(review['candidate_bundle_sha256'])
        expected_bundle={'version':'alpha_v11_compatible_bundle_v1','runtime_contract':review['runtime_contract'],
                         'target':review['target'],'feature_schema_sha256':review['feature_schema_sha256'],
                         'artifacts':review['artifact_refs'],'financial_authority':False}
        if digest(expected_bundle)!=review['candidate_bundle_sha256']:
            raise AuthorityError('REVIEWED_BUNDLE_PREIMAGE_MISMATCH')
        if not 0<=_number(review['size_multiplier'])<=1:
            raise AuthorityError('REVIEWED_OVERLAY_BOUND')
        if action=='RESTORE_OVERLAY':
            if requested_bundle is not None or review['candidate_bundle_sha256']!=state['active_bundle_sha256']:
                raise AuthorityError('RECOVERY_CANNOT_SWITCH_MODEL')
            result['overlay']={'size_multiplier':review['size_multiplier'],'require_manual_review':False}
        else:
            if requested_bundle!=review['candidate_bundle_sha256']:
                raise AuthorityError('REVIEWED_BUNDLE_MISMATCH')
            if action=='ROLLBACK' and requested_bundle!=state['previous_bundle_sha256']:
                raise AuthorityError('ROLLBACK_PREVIOUS_COMPATIBLE_REQUIRED')
            if requested_bundle==state['active_bundle_sha256']:
                raise AuthorityError('MODEL_ALREADY_ACTIVE')
            result['previous_bundle_sha256']=state['active_bundle_sha256']
            result['active_bundle_sha256']=requested_bundle
            # Promotion/rollback never clears a safety overlay as a side effect.
        review_sha=digest(review)
    else:
        raise AuthorityError('MODEL_ACTION_UNSUPPORTED')
    epoch=state['epoch']+1
    event={'epoch':epoch,'action':action,'at':now,'reason':reason,
           'previous_state_sha256':expected_state_sha256,
           'previous_event_sha256':digest(state['events'][-1]) if state['events'] else None,
           'active_bundle_sha256':result['active_bundle_sha256'],'overlay':result['overlay'],
           'review_id':review['review_id'] if review_sha else None,'review_sha256':review_sha,
           'reviewed_bundle':review if review_sha else None,'financial_authority':False}
    result['epoch']=epoch
    result['events'].append(event)
    validate_state(result)
    if len(canonical(result).encode())>MAX_BYTES:
        raise AuthorityError('MODEL_AUTHORITY_BYTES_BOUND')
    return result


def publish(*, action, expected_state_sha256, reason, review_id=None, requested_bundle=None, size_multiplier=None):
    if os.geteuid()!=0:
        raise AuthorityError('SEPARATE_ROOT_AUTHORITY_REQUIRED')
    custody(STATE.parent,directory=True)
    lock=STATE.parent/'authority.lock'
    fd=os.open(lock,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
    try:
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_mode & 0o077:
            raise AuthorityError('MODEL_AUTHORITY_LOCK_CUSTODY')
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        envelope=read_protected(STATE)
        if set(envelope)!={'state','sha256'} or digest(envelope['state'])!=envelope['sha256']:
            raise AuthorityError('MODEL_STATE_ENVELOPE_INTEGRITY')
        review=None
        if action!='DEMOTE':
            approvals=read_protected(APPROVALS)
            if (set(approvals)!={'version','reviews'} or approvals['version']!='alpha_v11_model_reviews_v1'
                    or not isinstance(approvals['reviews'],list) or len(approvals['reviews'])>1000):
                raise AuthorityError('MODEL_APPROVAL_MANIFEST_SCHEMA')
            matches=[r for r in approvals['reviews'] if r.get('review_id')==review_id]
            if len(matches)!=1:
                raise AuthorityError('MODEL_REVIEW_NOT_FOUND_OR_AMBIGUOUS')
            review=matches[0]
            # Approvals do not substitute for installed compatible bytes. Missing
            # or changed objects must leave the old pointer untouched.
            custody(OBJECTS,directory=True)
            object_ids=[review.get('candidate_bundle_sha256'),*review.get('artifact_refs',{}).values()]
            for key in object_ids:
                _sha(key)
                obj=read_protected(OBJECTS/(key+'.json'),canonical_required=True)
                if digest(obj)!=key:
                    raise AuthorityError('APPROVED_OBJECT_HASH_MISMATCH')
        result=transition(envelope['state'],action=action,expected_state_sha256=expected_state_sha256,
                          now=time.time(),reason=reason,review=review,requested_bundle=requested_bundle,
                          size_multiplier=size_multiplier)
        raw=canonical({'state':result,'sha256':digest(result)}).encode()
        previous=custody(STATE)
        out,temp=tempfile.mkstemp(prefix='model-state-',dir=STATE.parent)
        try:
            with os.fdopen(out,'wb') as stream:
                stream.write(raw)
                stream.flush()
                os.fchown(stream.fileno(),0,previous.st_gid)
                os.fchmod(stream.fileno(),previous.st_mode & 0o660 & ~0o022)
                os.fsync(stream.fileno())
            # Pointer, epoch, overlay and complete history commit in one rename.
            os.replace(temp,STATE)
            directory=os.open(STATE.parent,os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            Path(temp).unlink(missing_ok=True)
        return {'epoch':result['epoch'],'state_sha256':digest(result),'financial_authority':False}
    finally:
        os.close(fd)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['PROMOTE','ROLLBACK','DEMOTE','RESTORE_OVERLAY'])
    parser.add_argument('--expected-state-sha256',required=True)
    parser.add_argument('--reason',required=True)
    parser.add_argument('--review-id')
    parser.add_argument('--bundle')
    parser.add_argument('--size-multiplier',type=float)
    args=parser.parse_args()
    result=publish(action=args.action,expected_state_sha256=args.expected_state_sha256,reason=args.reason,
                   review_id=args.review_id,requested_bundle=args.bundle,size_multiplier=args.size_multiplier)
    print(canonical(result))


if __name__=='__main__':
    main()
