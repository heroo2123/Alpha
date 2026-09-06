from __future__ import annotations
import math, re, time
from collections import defaultdict
from datetime import datetime, timezone
from .config import settings
from .detectors import market_url
from .macro import MacroClient
from .models import Book, Market, Signal
from .polymarket import taker_fee_per_share
from .streams import CryptoRTDS


def _meta(m, outcome, ask, reason, extra=None):
    x={"trade_outcome":outcome,"ask":ask,"action_steps":["Tap OPEN MARKET below.",f"Select {outcome.upper()}.",f"Place a LIMIT buy at {ask:.3f} or lower; if the ask is higher, SKIP rather than chase.","Hold to resolution unless you deliberately exit earlier."],"risk_note":reason,"links":[{"label":"OPEN MARKET","url":market_url(m)}]}
    if extra: x.update(extra)
    return x


def _num(s, suffix=''):
    x=float(s.replace(',','')); return x*({'k':1e3,'m':1e6,'b':1e9}.get(suffix.lower(),1))


def threshold(text):
    q=text.replace('≥',' above ').replace('≤',' below '); n=r"\$?(-?\d[\d,]*(?:\.\d+)?)\s*([kKmMbB]?)\s*%?"
    pats=[('above',rf"\b(?:above|over|higher\s+than|more\s+than|at\s+least)\s*{n}"),('below',rf"\b(?:below|under|lower\s+than|less\s+than|at\s+most)\s*{n}"),('above',rf"{n}\s*(?:%\s*)?(?:or\s+)?(?:higher|above|more)\b"),('below',rf"{n}\s*(?:%\s*)?(?:or\s+)?(?:lower|below|less)\b")]
    for d,p in pats:
        m=re.search(p,q,re.I)
        if m:
            v=_num(m.group(m.lastindex-1),m.group(m.lastindex)); t=(q[:m.start()]+f' {d} {{x}} '+q[m.end():]).lower(); t=re.sub(r'\b20\d{2}\b','{year}',t); t=re.sub(r'\s+',' ',re.sub(r'[^a-z0-9{}% ]+',' ',t)).strip(); return d,v,t
    return None


def _norm_rule(value):
    return ' '.join(str(value or '').lower().split())


def _threshold_rule_signature(m):
    """Keep the optimized search from letting a cheap but rule-incompatible leg hide a valid pair."""
    q=m.question.lower()
    currencies=tuple(x for x in ('usd','eur','gbp','jpy','btc','eth','sol','xrp') if re.search(rf'\b{x}\b',q))
    return (
        str(m.end_date or ''),
        _norm_rule(m.resolution_source),
        _norm_rule(m.description),
        tuple(sorted(str(x).strip().lower() for x in m.outcomes)),
        '$' in m.question,
        '%' in m.question,
        currencies,
    )


def nested_threshold_arbitrage(markets, books):
    """Find nested-threshold underrounds without quadratic pair explosion.

    For a fixed event/template/rule signature, any looser YES with a higher ask is
    dominated by the cheapest looser YES already seen. Sorting thresholds therefore
    lets us inspect every potentially best stricter leg in O(n log n) rather than
    combinations(rows, 2). Equal thresholds are never paired with each other.
    """
    groups=defaultdict(list); out=[]
    for m in markets:
        p=threshold(m.question)
        if p and m.yes_token and m.no_token:
            groups[(m.event_id,p[0],p[2],_threshold_rule_signature(m))].append((m,p[1]))

    for (_,d,_,_),rows in groups.items():
        if len(rows)<2: continue
        # Process from looser toward stricter: lower thresholds are looser for
        # 'above'; higher thresholds are looser for 'below'.
        ordered=sorted(rows,key=lambda row: row[1],reverse=(d=='below'))
        best_looser=None  # (market, best_yes_ask)
        i=0
        while i<len(ordered):
            threshold_value=ordered[i][1]
            j=i+1
            while j<len(ordered) and math.isclose(ordered[j][1],threshold_value,rel_tol=1e-12,abs_tol=1e-12):
                j+=1
            same_threshold=ordered[i:j]

            # Current threshold is stricter than every market considered earlier.
            # Only the cheapest prior YES can produce the best underround for a
            # given stricter NO; any more expensive looser YES is dominated.
            if best_looser is not None:
                lm,ay=best_looser
                for sm,_ in same_threshold:
                    n=books.get(sm.no_token or '')
                    if not n or n.best_ask is None: continue
                    an=float(n.best_ask); fees=taker_fee_per_share(ay)+taker_fee_per_share(an); cost=ay+an+fees; edge=1-cost
                    if edge<settings.actionable_min_edge: continue
                    links=[{"label":"OPEN LOOSER","url":market_url(lm)},{"label":"OPEN STRICTER","url":market_url(sm)}]
                    meta={"yes_ask":ay,"no_ask":an,"immediate_settlement":True,"fingerprint_key":f"{lm.id}:{sm.id}","links":links,"action_steps":[f"Open LOOSER and buy YES at {ay:.3f} or lower: {lm.question}",f"Open STRICTER and buy NO at {an:.3f} or lower: {sm.question}","Use the SAME share count on both legs; never take only one leg.","If either quoted ask is now higher, SKIP."],"risk_note":"Glance at both Rules first. The locked payoff only holds if the stricter condition really implies the looser condition and both legs fill."}
                    out.append(Signal('nested_threshold_arb','ACTIONABLE',lm.event_id,lm.id,'Logical threshold arbitrage',f"Looser YES {ay:.3f} + stricter NO {an:.3f}; est. fees {fees:.4f}; edge {edge:.2%}.",market_url(lm),edge,cost,1.0,[lm.yes_token,sm.no_token],meta))

            # Equal thresholds become eligible only for later, strictly tighter
            # thresholds. Pick the cheapest YES among everything seen so far.
            for candidate,_ in same_threshold:
                y=books.get(candidate.yes_token or '')
                if not y or y.best_ask is None: continue
                ask=float(y.best_ask)
                if best_looser is None or ask<best_looser[1]:
                    best_looser=(candidate,ask)
            i=j
    return out


def _score(v):
    xs=re.findall(r'\d+(?:\.\d+)?',str(v or '')); return (float(xs[0]),float(xs[1])) if len(xs)>=2 else None


def sports_result_lag(markets,books,cache):
    out=[]
    for m in markets:
        ev=m.raw.get('_event') or {}; u=cache.get(m.event_slug) or (ev if ev.get('ended') else None)
        if not u or not u.get('ended'): continue
        sc=_score(u.get('score'))
        if not sc: continue
        home,away=sc; q=m.question.lower(); title=m.event_title; parts=re.split(r'\s+(?:vs\.?|v\.?|@)\s+',title,maxsplit=1,flags=re.I); truth=None; why=''
        if len(parts)==2:
            a,b=parts[0].strip(),parts[1].strip()
            if 'draw' in q: truth=home==away; why=f"final score {home:g}-{away:g}; draw={truth}"
            elif a.lower() in q and home!=away: truth=home>away; why=f"final score {home:g}-{away:g}; {a} {'won' if truth else 'did not win'}"
            elif b.lower() in q and home!=away: truth=away>home; why=f"final score {home:g}-{away:g}; {b} {'won' if truth else 'did not win'}"
        if truth is None:
            mm=re.search(r'\b(over|under)\s+(\d+(?:\.\d+)?)',q)
            if mm:
                total=home+away; line=float(mm.group(2)); truth=total>line if mm.group(1)=='over' else total<line; why=f"final total {total:g} vs {mm.group(1)} {line:g}"
        if truth is None: continue
        outcome='YES' if truth else 'NO'; token=m.yes_token if truth else m.no_token; b=books.get(token or '')
        if not b or b.best_ask is None or b.best_ask>settings.known_outcome_max_ask: continue
        ask=b.best_ask; cost=ask+taker_fee_per_share(ask); edge=1-cost
        if edge<settings.actionable_min_edge: continue
        meta=_meta(m,outcome,ask,'Confirm the final result and any overtime/shootout/postponement rule in the market Rules before buying.',{"fingerprint_key":f"{m.id}:{outcome}","sports_reason":why}); meta['action_steps'].insert(1,f"Confirm official final result: {why}.")
        out.append(Signal('sports_result_lag','ACTIONABLE',m.event_id,m.id,'Sports result known, market still discounted',f"ENDED; {why}. {outcome} ask {ask:.3f}; post-fee edge {edge:.2%}.",market_url(m),edge,cost,1.0,[token],meta))
    return out

ASSETS={'bitcoin':('btc','btc/usd','btcusdt'),'btc':('btc','btc/usd','btcusdt'),'ethereum':('eth','eth/usd','ethusdt'),'eth':('eth','eth/usd','ethusdt'),'solana':('sol','sol/usd','solusdt'),'sol':('sol','sol/usd','solusdt'),'xrp':('xrp','xrp/usd','xrpusdt')}
def _asset(m):
    txt=f"{m.event_title} {m.question}".lower()
    for k,v in ASSETS.items():
        if re.search(rf'\b{re.escape(k)}\b',txt): return v
    return None
def _topic(m):
    s=f"{m.description} {m.resolution_source}".lower()
    if '30-second' in s or '30 second' in s:return 'crypto_prices_twap_thirty'
    if '60-second' in s or '60 second' in s:return 'crypto_prices_twap_sixty'
    if 'chainlink' in s:return 'crypto_prices_chainlink'
    return None
def _end(s):
    try:return datetime.fromisoformat((s or '').replace('Z','+00:00')).timestamp()
    except:return None


def crypto_resolution_lag(markets,books,rtds:CryptoRTDS,now_ts=None):
    now_ts=now_ts or time.time(); out=[]
    for m in markets:
        a=_asset(m); topic=_topic(m)
        if not a or not topic: continue
        _,sym,_=a; winner=None; detail=''; sm=re.search(r'(?:btc|eth|sol|xrp)-updown-(5m|15m|4h)-(\d+)',m.event_slug.lower())
        if sm:
            dur={'5m':300,'15m':900,'4h':14400}[sm.group(1)]; st=float(sm.group(2)); en=st+dur
            if now_ts<en+1: continue
            x=rtds.nearest(topic,sym,st,settings.crypto_boundary_tolerance_seconds); y=rtds.nearest(topic,sym,en,settings.crypto_boundary_tolerance_seconds)
            if not x or not y or math.isclose(x.price,y.price): continue
            winner='Up' if y.price>x.price else 'Down'; detail=f"start {x.price:,.4f}, end {y.price:,.4f} ({topic})"
        else:
            p=threshold(m.question); en=_end(m.end_date)
            if not p or en is None or now_ts<en+1: continue
            tick=rtds.nearest(topic,sym,en,settings.crypto_boundary_tolerance_seconds)
            if not tick: continue
            truth=tick.price>=p[1] if p[0]=='above' else tick.price<=p[1]; winner='Yes' if truth else 'No'; detail=f"reference {tick.price:,.4f} vs {p[0]} {p[1]:,.4f} ({topic})"
        token=m.token_for_outcome(winner); b=books.get(token or '')
        if not token or not b or b.best_ask is None or b.best_ask>settings.known_outcome_max_ask: continue
        ask=b.best_ask; cost=ask+taker_fee_per_share(ask); edge=1-cost
        if edge<settings.actionable_min_edge: continue
        meta=_meta(m,winner,ask,'Verify the market Rules use the same source and boundary time before trading.',{"fingerprint_key":f"{m.id}:{winner}","reference_topic":topic}); meta['action_steps'].insert(1,f"Confirm Rules/source/time; captured {detail}.")
        out.append(Signal('crypto_resolution_lag','ACTIONABLE',m.event_id,m.id,'Crypto resolution value captured',f"Outcome {winner}; {detail}; ask {ask:.3f}; post-fee edge {edge:.2%}.",market_url(m),edge,cost,1.0,[token],meta))
    return out


def crypto_crossfeed_divergence(markets,rtds:CryptoRTDS):
    out=[]; seen=set()
    for m in markets:
        a=_asset(m)
        if not a or a[0] in seen: continue
        c=rtds.latest('crypto_prices_chainlink',a[1]); b=rtds.latest('crypto_prices',a[2]) or rtds.latest('crypto_prices',a[2].replace('usdt','/usdt'))
        if not c or not b: continue
        bp=abs(b.price-c.price)/c.price*10000
        if bp<settings.crypto_crossfeed_watch_bps: continue
        seen.add(a[0]); out.append(Signal('crypto_crossfeed_divergence','WATCH',m.event_id,m.id,f"{a[0].upper()} reference feeds diverged",f"Chainlink {c.price:,.4f} vs Binance-backed {b.price:,.4f}: {bp:.1f} bps. Read Rules to identify the source that actually settles the contract.",market_url(m),None,None,None,[],{"fingerprint_key":f"{a[0]}:{int(bp/5)}","links":[{"label":"OPEN MARKET","url":market_url(m)}],"action_steps":["Open the market and read its resolution source.","Judge the contract only against that source; do not trade merely because feeds differ."],"risk_note":"Cross-feed divergence is a discovery signal, not an arbitrage."}))
    return out

MONTHS={n.lower():i for i,n in enumerate(['','January','February','March','April','May','June','July','August','September','October','November','December']) if n}; MONTHS.update({n[:3]:i for n,i in list(MONTHS.items())})
def _ym(text,now):
    m=re.search(r'\b(January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\s*(20\d{2})?',text,re.I)
    return (int(m.group(2)) if m and m.group(2) else now.year,MONTHS.get(m.group(1).lower(),MONTHS.get(m.group(1)[:3].lower()))) if m else None


def official_macro_release_lag(markets,books,macro:MacroClient,now=None):
    if not macro.enabled or not macro.values:return []
    now=now or datetime.now(timezone.utc); out=[]
    for m in markets:
        src=f"{m.resolution_source} {m.description}".lower()
        if 'bls.gov' not in src and 'bureau of labor statistics' not in src: continue
        low=f"{m.event_title} {m.question}".lower(); metric='core_cpi_yoy' if 'core' in low and ('cpi' in low or 'inflation' in low) else 'cpi_yoy' if ('cpi' in low or 'inflation' in low) else 'unemployment' if 'unemployment' in low else 'payroll_change_k' if ('nonfarm' in low or 'payroll' in low or 'jobs added' in low) else None
        ym=_ym(low,now); p=threshold(m.question)
        if not metric or not ym or not p: continue
        v=macro.get(metric,*ym)
        if not v: continue
        truth=v.value>=p[1] if p[0]=='above' else v.value<=p[1]; outcome='YES' if truth else 'NO'; token=m.yes_token if truth else m.no_token; b=books.get(token or '')
        if not b or b.best_ask is None or b.best_ask>settings.known_outcome_max_ask: continue
        ask=b.best_ask; cost=ask+taker_fee_per_share(ask); edge=1-cost
        if edge<settings.actionable_min_edge: continue
        reason=f"BLS {metric}={v.value:.3f} for {ym[0]}-{ym[1]:02d}; threshold {p[0]} {p[1]:g}"; meta=_meta(m,outcome,ask,'Confirm the BLS release definition matches the market Rules before buying.',{"fingerprint_key":f"{m.id}:{ym[0]}-{ym[1]}:{v.value:.4f}"}); meta['action_steps'].insert(1,f"Confirm official BLS value/definition: {reason}.")
        out.append(Signal('official_macro_release_lag','ACTIONABLE',m.event_id,m.id,'Official BLS value published, market still discounted',f"{reason}; implies {outcome}; ask {ask:.3f}; post-fee edge {edge:.2%}.",market_url(m),edge,cost,1.0,[token],meta))
    return out
