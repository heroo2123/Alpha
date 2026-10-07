from __future__ import annotations
from dataclasses import dataclass,asdict
from datetime import date
from .evidence import EvidenceError,digest,sha
from .rules import RuleFingerprint

VERSION='alpha_v11_daily_rule_rollforward_envelope_v1'
INVARIANT_KEYS=(
 'version','station','city','timezone','unit','family','statistic','observation_population',
 'precision_rounding','primary_source','source_family','fallback_policy','correction_policy',
 'finality_and_deadline_policy','no_data_outcome','metadata_fingerprint','compiler_version',
 'semantic_profile_version','financial_authority')

@dataclass(frozen=True)
class DailyRuleEnvelope:
    anchor_rule_sha256:str
    invariants:tuple[tuple[str,object],...]
    strict_version:str
    strict_station:str
    strict_location:str
    strict_source:str
    bucket_count:int
    interior_width:float
    version:str=VERSION
    def __post_init__(self):
        sha(self.anchor_rule_sha256)
        if self.version!=VERSION or self.bucket_count<3 or self.interior_width<=0:
            raise EvidenceError('ROLLFORWARD_ENVELOPE_INVALID')
    @property
    def sha256(self): return digest(asdict(self))

def _partition_shape(p):
    if not isinstance(p,list) or len(p)<3: raise EvidenceError('ROLLFORWARD_PARTITION_INVALID')
    if p[0].get('lower') is not None or p[-1].get('upper') is not None:
        raise EvidenceError('ROLLFORWARD_PARTITION_TAILS')
    unit=p[0].get('unit')
    if any(x.get('unit')!=unit for x in p): raise EvidenceError('ROLLFORWARD_PARTITION_UNIT')
    widths=[]
    prev=None
    seen=set()
    for i,x in enumerate(p):
        for k in ('market_id','condition_id','yes_token','no_token'):
            v=x.get(k)
            if not isinstance(v,str) or not v or v in seen: raise EvidenceError('ROLLFORWARD_PARTITION_IDENTITY')
            seen.add(v)
        lo,hi=x.get('lower'),x.get('upper')
        if i and lo is None: raise EvidenceError('ROLLFORWARD_PARTITION_GAP')
        if i<len(p)-1 and hi is None: raise EvidenceError('ROLLFORWARD_PARTITION_GAP')
        if lo is not None and hi is not None:
            if lo>hi: raise EvidenceError('ROLLFORWARD_PARTITION_ORDER')
            widths.append(float(hi-lo+1))
        if prev is not None and lo!=prev+1: raise EvidenceError('ROLLFORWARD_PARTITION_GAP')
        if hi is not None: prev=hi
    if not widths or len(set(widths))!=1: raise EvidenceError('ROLLFORWARD_PARTITION_WIDTH')
    return unit,float(widths[0])

def envelope_from_reviewed(rule:RuleFingerprint)->DailyRuleEnvelope:
    p=rule.payload;s=p.get('strict_contract',{})
    unit,width=_partition_shape(p.get('partition'))
    if p.get('financial_authority') is not False or unit!=p.get('unit'):
        raise EvidenceError('ROLLFORWARD_RULE_AUTHORITY_OR_UNIT')
    inv=tuple((k,p.get(k)) for k in INVARIANT_KEYS)
    return DailyRuleEnvelope(rule.sha256,inv,s.get('version'),s.get('station'),s.get('location'),
        s.get('operative_source'),len(p['partition']),width)

def assess_rollforward(envelope:DailyRuleEnvelope,rule:RuleFingerprint)->dict:
    p=rule.payload;s=p.get('strict_contract',{})
    try:
        unit,width=_partition_shape(p.get('partition'))
        reason=(
          'INVARIANT_CHANGED' if tuple((k,p.get(k)) for k in INVARIANT_KEYS)!=envelope.invariants else
          'STRICT_CONTRACT_IDENTITY_CHANGED' if (s.get('version'),s.get('station'),s.get('location'),s.get('operative_source')) !=
             (envelope.strict_version,envelope.strict_station,envelope.strict_location,envelope.strict_source) else
          'PARTITION_SHAPE_CHANGED' if (len(p['partition'])!=envelope.bucket_count or width!=envelope.interior_width or unit!=p.get('unit')) else
          'TARGET_DATE_INVALID' if not isinstance(p.get('target_date'),str) else
          'RULE_TEMPLATE_MATCH')
    except EvidenceError as exc:
        reason=str(exc)
    return {'passed':reason=='RULE_TEMPLATE_MATCH','reason':reason,'envelope_sha256':envelope.sha256,
            'rule_fingerprint':rule.sha256,'financial_authority':False}
