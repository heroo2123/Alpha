from __future__ import annotations
from .weather_only_operator_state_corrective import OperatorStateCommandController
class OperatorStateCommandControllerV10(OperatorStateCommandController):
 def _maker_performance(self)->dict:
  method=getattr(self.maker_store,'maker_performance_by_evidence',None)
  if not callable(method): return {'settled':0,'capital':0.0,'proceeds':0.0,'pnl':0.0,'roi':None,'legacy_excluded':{'settled':0,'capital':0.0,'proceeds':0.0,'pnl':0.0,'roi':None}}
  grouped=method(); valid=dict(grouped['validated']); valid['legacy_excluded']=dict(grouped['legacy_excluded']); valid['research_excluded']=dict(grouped['research_excluded']); return valid
 def _stats_text(self)->str:
  base=super()._stats_text(); m=self._maker_performance(); legacy=m.get('legacy_excluded') or {}; research=m.get('research_excluded') or {}
  note='\n'.join(['','<b>MAKER EVIDENCE CLASSIFICATION</b>',f"Validated queue-certified maker P&amp;L: <b>${float(m.get('pnl') or 0):+.2f}</b>",f"Excluded LEGACY_QUEUE_UNCERTIFIED: <b>{int(legacy.get('settled') or 0)}</b> settlements | research P&amp;L ${float(legacy.get('pnl') or 0):+.2f}",f"Excluded RESEARCH_ONLY: <b>{int(research.get('settled') or 0)}</b> settlements | research P&amp;L ${float(research.get('pnl') or 0):+.2f}",'Legacy/research maker rows remain auditable but are excluded from validated capital, P&amp;L and ROI.'])
  return base+note
