#!/usr/bin/env python3
from __future__ import annotations
import asyncio,hashlib,json,math,os,time
from decimal import Decimal
from pathlib import Path
import httpx
from polymarket_scanner.v11.probability import score_vectors

ROOT=Path('/home/alphaadmin/AlphaV11_BrainForwardUniverseS3')
PAIRS=ROOT/'pairs';STATUS=ROOT/'label-score-status.json'
GENERAL_PAIRS=Path('/home/alphaadmin/AlphaV11_BrainForwardUniverse/pairs')

def canonical(v):return json.dumps(v,sort_keys=True,separators=(',',':'),ensure_ascii=True)
def digest(v):return hashlib.sha256(canonical(v).encode()).hexdigest()
def atomic(p,v):
 t=p.with_suffix(p.suffix+'.tmp');t.write_text(json.dumps(v,indent=2,sort_keys=True,default=str)+'\n');os.chmod(t,0o600);os.replace(t,p)

async def event(client,slug):
 r=await client.get('https://gamma-api.polymarket.com/events',params={'slug':slug})
 if r.status_code!=200:return None
 x=r.json()
 return x[0] if isinstance(x,list) and len(x)==1 and isinstance(x[0],dict) else None

def final_label(cap,e):
 if e.get('closed') is not True:return None
 pred=cap['prediction']['buckets'];markets=e.get('markets')
 if not isinstance(markets,list) or len(markets)!=len(pred):return None
 by={str(m.get('id')):m for m in markets if isinstance(m,dict)}
 if len(by)!=len(markets):return None
 labels=[];refs=[]
 for b in pred:
  m=by.get(str(b['market_id']))
  if m is None or m.get('closed') is not True:return None
  try: outs=json.loads(m['outcomes']);prices=json.loads(m['outcomePrices']);tokens=json.loads(m['clobTokenIds'])
  except Exception:return None
  if outs!=['Yes','No'] or tokens!=[str(b['yes_token']),str(b['no_token'])] or len(prices)!=2:return None
  try:y,n=Decimal(str(prices[0])),Decimal(str(prices[1]))
  except Exception:return None
  if y not in (Decimal(0),Decimal(1)) or n not in (Decimal(0),Decimal(1)) or y+n!=1:return None
  labels.append(int(y));refs.append({'market_id':str(b['market_id']),'yes_payout':int(y),'no_payout':int(n)})
 if sum(labels)!=1:return None
 return {'version':'alpha_v11_brain_forward_universe_label_v1','sid':cap['sid'],'event_id':cap['event_id'],
  'slug':cap['slug'],'labels':labels,'winner_index':labels.index(1),'market_payouts':refs,
  'gamma_event_sha256':digest(e),'captured_prediction_sha256':cap['prediction_sha256'],
  'financial_authority':False,'automatic_promotion':False,'labeled_at':time.time()}

async def main():
 by_sid={}
 for p in sorted(PAIRS.glob('*/capture.json')):
  try:c=json.loads(p.read_text())
  except Exception:continue
  if c.get('version')=='alpha_v11_brain_forward_universe_s3_capture_v1' and c.get('sid'):
   by_sid[str(c['sid'])]=(p.parent,c)
 # The original S3 batch predated reviewed KLGA alias support. Accept a missing
 # station-day from the causally equivalent general forward-universe collector,
 # but never replace an existing S3 capture for the same station/day.
 for p in sorted(GENERAL_PAIRS.glob('*/capture.json')):
  try:c=json.loads(p.read_text())
  except Exception:continue
  if c.get('version')!='alpha_v11_brain_forward_universe_capture_v1':continue
  station=str(c.get('station') or '');target=str(c.get('target_date') or '')
  if not station or not target:continue
  sid=station+'|'+target
  if sid in by_sid:continue
  c=dict(c);c['sid']=sid
  by_sid[sid]=(p.parent,c)
 caps=[by_sid[sid] for sid in sorted(by_sid)]
 results=[]
 async with httpx.AsyncClient(timeout=15.,follow_redirects=False,headers={'User-Agent':'Alpha-V11-forward-labels/1'}) as client:
  for root,cap in caps:
   lp=root/'label.json'
   if lp.exists():
    lab=json.loads(lp.read_text());results.append({'sid':cap['sid'],'state':'LABELED','label':lab});continue
   e=await event(client,cap['slug']);lab=final_label(cap,e) if e else None
   if lab is None:results.append({'sid':cap['sid'],'state':'WAITING_FINAL_PAYOUT'});continue
   atomic(lp,lab);results.append({'sid':cap['sid'],'state':'LABELED','label':lab})
 probs=[];outs=[];events=[];citydays=[]
 for root,cap in caps:
  lp=root/'label.json'
  if not lp.exists():continue
  lab=json.loads(lp.read_text())
  probs.append([float(x['point']) for x in cap['prediction']['buckets']]);outs.append(int(lab['winner_index']))
  events.append(str(cap['event_id']));citydays.append(str(cap['sid']))
 score=score_vectors(probs,outs,event_ids=events,city_days=citydays) if probs else None
 state={'version':'alpha_v11_brain_forward_universe_label_score_v1','at':time.time(),'captures_total':len(caps),
  'labels_complete':len(probs),'labels_waiting':len(caps)-len(probs),'state':'COMPLETE' if len(probs)==len(caps) and caps else 'WAITING_LABELS',
  'score':score,'results':[{k:v for k,v in x.items() if k!='label'} for x in results],
  'parent_bundle_sha256':'fd9a32aa12ac7c8018ec524d1b74514bb57c42c0e60241672a4446b95908e641',
  'new_challenger_trained':False,'automatic_promotion':False,'financial_authority':False}
 atomic(STATUS,state);print(json.dumps({k:state[k] for k in ('state','captures_total','labels_complete','labels_waiting')},sort_keys=True))
asyncio.run(main())
