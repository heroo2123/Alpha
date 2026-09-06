from datetime import datetime, timezone

from polymarket_scanner.detectors import binary_buy_both, neg_risk_underround
from polymarket_scanner.detectors_v02 import crypto_resolution_lag, nested_threshold_arbitrage, sports_result_lag, threshold
from polymarket_scanner.models import Book, Market
from polymarket_scanner.streams import CryptoRTDS, PriceTick


def mkt(mid='1', eid='e', question='Q?', yes='y', no='n', *, neg=False, title='Event', slug='q', event_slug='event', outcomes=None, tokens=None, raw=None, description='', resolution_source='', end_date=None):
    outcomes = outcomes or ['Yes','No']; tokens = tokens or [yes,no]
    return Market(mid,eid,event_slug,title,neg,question,slug,'c',outcomes,tokens,[0.5 for _ in outcomes],0.4,0.5,1000,5000,True,False,end_date,description,resolution_source,'',[],raw or {})


def test_binary_arb_detected():
    m=mkt(); books={'y': Book('y', [], [(0.40,100)]), 'n': Book('n', [], [(0.40,100)])}; s=binary_buy_both([m],books)
    assert len(s)==1 and s[0].edge > .15 and s[0].metadata['yes_ask'] == 0.40


def test_neg_risk_underround():
    rows=[mkt(str(i),'e',f'Outcome {i}?',f'y{i}',f'n{i}',neg=True) for i in range(3)]
    books={f'y{i}': Book(f'y{i}', [], [(0.25,100)]) for i in range(3)}
    s=neg_risk_underround(rows,books); assert len(s)==1 and s[0].edge > .20


def test_threshold_parser_and_nested_arb():
    p=threshold('Will BTC be above $100,000 on Dec 31?'); assert p and p[0]=='above' and p[1]==100000
    lo=mkt('lo','e','Will BTC be above $100,000 on Dec 31?', 'ly','ln'); hi=mkt('hi','e','Will BTC be above $120,000 on Dec 31?', 'hy','hn')
    books={'ly':Book('ly',[],[(0.35,20)]),'hn':Book('hn',[],[(0.45,20)])}; s=nested_threshold_arbitrage([lo,hi],books)
    assert len(s)==1 and s[0].token_ids == ['ly','hn']


def test_nested_arb_uses_cheapest_dominating_looser_without_pair_explosion():
    rows=[]; books={}
    for i,threshold_value in enumerate(range(100, 2100, 10)):
        mid=f'm{i}'; yes=f'y{i}'; no=f'n{i}'
        rows.append(mkt(mid,'e',f'Will BTC be above ${threshold_value} on Dec 31?',yes,no))
        # Make the 100-threshold YES the cheapest looser, and the final NO cheap.
        yes_ask=0.10 if i==0 else 0.60
        no_ask=0.20 if i==len(range(100, 2100, 10))-1 else 0.95
        books[yes]=Book(yes,[],[(yes_ask,100)])
        books[no]=Book(no,[],[(no_ask,100)])
    signals=nested_threshold_arbitrage(rows,books)
    assert any(s.token_ids == ['y0', f'n{len(rows)-1}'] for s in signals)


def test_sports_final_result_lag():
    raw={'_event': {'slug':'team-a-vs-team-b','ended': True, 'score':'3-1'}, 'sportsMarketType':'moneyline'}
    m=mkt('sp','se','Will Team A win?','sy','sn',title='Team A vs Team B',event_slug='team-a-vs-team-b',raw=raw)
    s=sports_result_lag([m],{'sy':Book('sy',[],[(0.80,50)])},{'team-a-vs-team-b': {'slug':'team-a-vs-team-b','ended':True,'score':'3-1'}})
    assert len(s)==1 and s[0].metadata['trade_outcome']=='YES'


def test_crypto_updown_boundary_result():
    r=CryptoRTDS(); start=2_000_000_000.0; topic='crypto_prices_chainlink'; symbol='btc/usd'
    r.history[(topic,symbol)].append(PriceTick(topic,symbol,100.0,start)); r.history[(topic,symbol)].append(PriceTick(topic,symbol,101.0,start+300))
    m=mkt('cr','ce','Bitcoin Up or Down?', title='Bitcoin Up or Down', event_slug=f'btc-updown-5m-{int(start)}', outcomes=['Up','Down'], tokens=['up','down'], description='Resolves using Chainlink BTC/USD.', end_date=datetime.fromtimestamp(start+300, timezone.utc).isoformat())
    s=crypto_resolution_lag([m],{'up':Book('up',[],[(0.80,100)])},r,now_ts=start+305)
    assert len(s)==1 and s[0].metadata['trade_outcome']=='Up'
