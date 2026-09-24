"""Bounded strategy adapters sharing one runtime claim and common PAPER account.

Adapters produce evidence and proposals only. Queue completion and all account
admission/CAS gates remain in PaperRuntime and PaperCoordinator.
"""
from base64 import urlsafe_b64encode

from .evidence import EvidenceError, digest, identity
from .paper_runtime import Evaluation, TemperatureEventAdapter, VERSION as RUNTIME_VERSION
from .relative_value import DiscoveryRequest, RelativeValueStrategies
from .runtime_health import admission_heads


def _gated(store,key,claim,reason,lane):
    return store.audit(key,event_id=claim['event_id'],kind='RUNTIME_STATUS',details=dict(
        version=RUNTIME_VERSION,outcome='GATED',reason=reason,strategy_adapter=lane,financial_authority=False))


class RelativeValueEventAdapter:
    def __init__(self,store,requests_for_event):
        self.store,self.requests_for_event=store,requests_for_event

    def evaluate(self,claim,prefix):
        requests=self.requests_for_event(claim)
        if (type(requests) is not tuple or not 1 <= len(requests) <= 6
                or any(not isinstance(r,DiscoveryRequest) for r in requests)
                or sum(r.maximum_proposals for r in requests)>6):
            raise EvidenceError('RUNTIME_RELATIVE_VALUE_REQUEST_BOUND')
        engine=RelativeValueStrategies(self.store);outputs=[];proposals=[]
        for index,request in enumerate(requests):
            # Preserve all 256 digest bits inside the existing 60-character
            # strategy identity bound, including its later basket/leg suffixes.
            key='rv:'+urlsafe_b64encode(bytes.fromhex(digest([prefix,index]))).decode().rstrip('=')
            try:
                pin=self.store.get(request.admission_id)['body']['details']['request']
                context,strategy=pin['context'],pin['scope']['strategy']
                if context['event_id']!=claim['event_id']:raise EvidenceError('RUNTIME_EVALUATION_EVENT_MISMATCH')
                admission_heads(self.store,account_id=context['account_id'],event_id=context['event_id'],strategies=(strategy,))
                row=engine.evaluate(key,request)
            except EvidenceError as exc:
                outputs.append(_gated(self.store,key,claim,str(exc),'RELATIVE_VALUE')['id']);continue
            if row['event_id']!=claim['event_id']:raise EvidenceError('RUNTIME_EVALUATION_EVENT_MISMATCH')
            details=row['body']['details']
            outputs.extend(details['queue_result_ids'])
            if details['outcome']=='CANDIDATES':proposals.extend(engine.proposals(key))
        return Evaluation(tuple(outputs),tuple(proposals))


class MultiStrategyEventAdapter:
    def __init__(self,store,adapters):
        if (type(adapters) is not tuple or not 1 <= len(adapters) <= 6
                or any(type(pair) is not tuple or len(pair)!=2 for pair in adapters)):
            raise EvidenceError('RUNTIME_STRATEGY_ADAPTER_BOUND')
        names=[]
        for name,adapter in adapters:
            identity(name);names.append(name)
            if not isinstance(adapter,(TemperatureEventAdapter,RelativeValueEventAdapter)) or adapter.store is not store:
                raise EvidenceError('RUNTIME_STRATEGY_ADAPTER_SCOPE')
        if len(set(names))!=len(names):raise EvidenceError('RUNTIME_STRATEGY_ADAPTER_DUPLICATE')
        self.store,self.adapters=store,adapters

    def evaluate(self,claim,prefix):
        outputs=[];proposals=[]
        for name,adapter in self.adapters:
            key='strategy-runtime:'+digest([prefix,name])
            try:
                result=adapter.evaluate(claim,key)
                if (not isinstance(result,Evaluation) or type(result.result_ids) is not tuple
                        or not 1 <= len(result.result_ids) <= 6 or type(result.proposals) is not tuple
                        or len(result.proposals)>6):raise EvidenceError('RUNTIME_STRATEGY_OUTPUT_BOUND')
                # Validate event scope before exposing any child to queue completion.
                if any(self.store.get(r)['event_id']!=claim['event_id'] for r in result.result_ids):
                    raise EvidenceError('RUNTIME_EVALUATION_EVENT_MISMATCH')
            except (EvidenceError,TimeoutError) as exc:
                outputs.append(_gated(self.store,key,claim,str(exc) if isinstance(exc,EvidenceError) else 'ADAPTER_TIMEOUT',name)['id'])
                continue
            outputs.extend(result.result_ids);proposals.extend(result.proposals)
        if len(outputs)>16 or len(proposals)>6 or len(set(outputs))!=len(outputs):
            # Preserve all child evidence, but do not arbitrarily trim a basket,
            # favor the first lane, or present an omitted suffix as evaluated.
            row=_gated(self.store,'strategy-bound:'+digest(prefix),claim,'RUNTIME_COMBINED_STRATEGY_BOUND','MULTI_STRATEGY')
            return Evaluation((row['id'],))
        return Evaluation(tuple(outputs),tuple(proposals))
