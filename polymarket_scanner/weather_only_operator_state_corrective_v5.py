from __future__ import annotations
import math,time
from .weather_only_operator_state_corrective import OPERATOR_STATE_CORRECTIVE_VERSION, TERMINAL_VISIBLE_STATUSES, _sha
from .weather_only_live_paper_all_signals_final_v9 import FinalOperatorStatePostReceiptStore
from .weather_only_paper_positions import WeatherPaperPositionError, _payload
OPERATOR_STATE_CORRECTIVE_V5_VERSION="weather_operator_state_v5_atomic_delivered_terminalization"
class OperatorStatePostReceiptStoreV5(FinalOperatorStatePostReceiptStore):
 def terminalize_delivered_signal(self,signal_id:int,*,terminal_status:str,reason:str,decision_id:str,event_id:str,market_id:str|None,side:str|None,recorded_at:float|None=None)->dict:
  sid=int(signal_id); status=str(terminal_status); at=time.time() if recorded_at is None else float(recorded_at)
  if status not in TERMINAL_VISIBLE_STATUSES or not math.isfinite(at) or at<0: raise WeatherPaperPositionError('V5_TERMINAL_ARGUMENT_INVALID')
  with self._conn() as db:
   try:
    db.execute('BEGIN IMMEDIATE'); row=db.execute('SELECT * FROM weather_paper_signals WHERE id=?',(sid,)).fetchone()
    if row is None: raise WeatherPaperPositionError('V5_SIGNAL_NOT_FOUND')
    sig=dict(row); payload=_payload(sig.get('payload_json')); mid=sig.get('telegram_message_id'); sent=sig.get('telegram_sent_at')
    if isinstance(mid,bool) or not isinstance(mid,int) or mid<=0 or isinstance(sent,bool) or not isinstance(sent,(int,float)) or not math.isfinite(float(sent)): raise WeatherPaperPositionError('V5_TERMINAL_TELEGRAM_RECEIPT_INVALID')
    if str(sig.get('event_id') or '')!=str(event_id) or str(sig.get('market_id') or '')!=str(market_id or '') or str(sig.get('side') or '').upper()!=str(side or '').upper(): raise WeatherPaperPositionError('V5_TERMINAL_IDENTITY_MISMATCH')
    if str(payload.get('decision_id') or '') and str(payload.get('decision_id'))!=str(decision_id): raise WeatherPaperPositionError('V5_TERMINAL_DECISION_IDENTITY_MISMATCH')
    db.execute('UPDATE weather_paper_signals SET status=? WHERE id=?',(status,sid))
    db.execute('INSERT INTO weather_paper_decisions(decision_id,event_id,market_id,side,outcome,reason,created_at) VALUES(?,?,?,?,?,?,?)',(str(decision_id),str(event_id),market_id,side,status,str(reason),at))
    title=str(payload.get('event_title') or sig.get('event_id') or 'Weather signal'); lane=str(sig.get('lane') or 'unknown'); former=str(sig.get('side') or 'BASKET')
    text='\n'.join(['⛔ <b>INVALIDATED — DO NOT ACT</b>',f'<b>{title[:180]}</b>',f'Lane: <code>{lane}</code>',f'Former side: <b>{former}</b>','',f'Final state: <b>{status}</b>',f'Reason: <code>{str(reason)[:800]}</code>','','🚫 <b>Do not place a trade from the earlier alert.</b>'])
    base=str(payload.get('operator_retry_base_fingerprint') or sig.get('fingerprint') or '')
    if not base: raise WeatherPaperPositionError('OPERATOR_SYNC_BASE_FINGERPRINT_MISSING')
    db.execute("""INSERT INTO weather_paper_operator_sync(signal_id,version,terminal_status,reason,telegram_message_id,base_fingerprint,message_sha256,state,attempts,last_error,created_at,updated_at,applied_at,fingerprint_released) VALUES(?,?,?,?,?,?,?,?,0,NULL,?,?,NULL,0) ON CONFLICT(signal_id) DO UPDATE SET terminal_status=excluded.terminal_status,reason=excluded.reason,telegram_message_id=excluded.telegram_message_id,message_sha256=excluded.message_sha256,state=CASE WHEN weather_paper_operator_sync.state='APPLIED' THEN weather_paper_operator_sync.state ELSE 'PENDING' END,updated_at=excluded.updated_at""",(sid,OPERATOR_STATE_CORRECTIVE_VERSION,status,str(reason),int(mid),base,_sha(text),'PENDING',at,at))
    db.execute('COMMIT'); return {'signal_id':sid,'telegram_message_id':int(mid),'terminal_status':status,'reason':str(reason)}
   except Exception:
    if db.in_transaction: db.execute('ROLLBACK')
    raise
