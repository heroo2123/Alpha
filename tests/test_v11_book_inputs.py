from copy import deepcopy
from dataclasses import replace

import pytest

from polymarket_scanner.v11.book_inputs import BookPolicy, ENDPOINT, PROVIDER, book_request, normalize_book_capture
from polymarket_scanner.v11.evidence import EvidenceError, EvidenceStore
from polymarket_scanner.v11.microstructure import MakerMicrostructure, MicrostructurePolicy
from polymarket_scanner.v11.weather_sources import normalize_weather_capture
from test_v11_probability import rule, T


@pytest.fixture
def books(tmp_path):
    tmp_path.chmod(0o700)
    now = [T+100.]
    store = EvidenceStore(tmp_path/'books.sqlite', 'V11_PAPER', clock=lambda:now[0])
    r = rule(); member = r.payload['partition'][0]
    return dict(store=store, now=now, rule=r, member=member, token=member['yes_token'])


def response(rig, token=None):
    token = token or rig['token']
    member = next(b for b in rig['rule'].payload['partition'] if token in (b['yes_token'], b['no_token']))
    return dict(market=member['condition_id'], asset_id=token, timestamp=str(int(rig['now'][0]*1000)),
        hash='opaque-exchange-hash', bids=[dict(price='.1', size='50'),dict(price='.3', size='10')],
        asks=[dict(price='.8', size='50'),dict(price='.4', size='10')], tick_size='.01', min_order_size='5', neg_risk=False)


def raw(rig, key='raw', *, body=None, changes=None, **fields):
    payload = dict(response=response(rig) if body is None else body, endpoint=ENDPOINT,
                   request_params={'token_id':rig['token']}, http_status=200,
                   response_format='JSON', source_time_status='NOT_YET_NORMALIZED')
    payload.update(changes or {})
    return rig['store'].capture(key, **dict(dict(event_id=rig['rule'].payload['event_id'], kind='BOOK',
        provider=PROVIDER, source_identity=rig['token'], revision=key, payload=payload, evidence_class='SYNTHETIC'), **fields))


def normalize(rig, key='raw', **changes):
    kwargs = dict(rule=rig['rule'], token_id=rig['token'], collateral_asset='FIXTURE_COLLATERAL', policy=BookPolicy('fixture'))
    return normalize_book_capture(rig['store'], key, **{**kwargs, **changes})


def test_exact_book_sorted_depth_preserves_receipt_and_does_not_claim_websocket_sequence(books):
    source = raw(books); books['now'][0] += 2
    row = normalize(books); b = row['body']; p = b['payload']
    assert b['received_at'] == source['body']['received_at'] < b['available_at']
    assert b['observed_at'] == source['body']['received_at'] and p['feature_ready_at'] == b['available_at']
    assert p['bids'][0]['price'] == '.3' and p['asks'][0]['price'] == '.4'
    assert p['raw_evidence_sha256'] == source['sha256'] and p['snapshot_type'] == 'FULL'
    assert p['stream_healthy'] and not p['continuous_stream_verified'] and 'book_sequence' not in p
    assert p['fees'] is None and p['network_latency_seconds'] is None and not b['financial_authority']
    assert b['evidence_class'] == 'SYNTHETIC'
    req = book_request(event_id=row['event_id'], token_id=books['token'], revision='test')
    assert req.url == ENDPOINT and req.params == (('token_id', books['token']),)


def test_replay_cannot_renew_receipt_or_feature_time_and_old_raw_cannot_be_reinterpreted(books):
    raw(books); first = normalize(books); books['now'][0] += 10
    assert normalize(books) == first
    with pytest.raises(EvidenceError, match='RAW_SUPERSEDED'):
        normalize(books, policy=BookPolicy('different-policy'))
    raw(books, 'next')
    assert normalize(books) == first  # Immutable replay, explicitly not a current-source attestation.
    assert normalize(books, 'next')['seq'] > first['seq']


@pytest.mark.parametrize('changes', [
    {'market':'other-condition'}, {'asset_id':'other-token'}, {'timestamp':None}, {'timestamp':1},
    {'timestamp':'NaN'}, {'hash':None}, {'tick_size':'.003'}, {'min_order_size':'0'}, {'neg_risk':None},
    {'bids':[dict(price='.4',size='5')]}, {'asks':[dict(price='.3',size='5')]},
    {'bids':[dict(price='.301',size='5')]}, {'bids':[dict(price='.1',size='0')]},
    {'bids':[dict(price='.1',size='5'),dict(price='.10',size='5')]},
    {'bids':[dict(price='1',size='5')]}, {'bids':[dict(price='.1',size='NaN')]},
    {'asks':[dict(price='.4',size=5)]}, {'asks':[dict(price='.4',size='5',unreviewed=1)]},
])
def test_bad_scope_time_tick_or_depth_never_produces_normalized_book(books, changes):
    body = response(books); body.update(changes); raw(books, body=body)
    with pytest.raises(EvidenceError): normalize(books)
    assert len(books['store'].records(kind='BOOK')) == 1


@pytest.mark.parametrize('change', ['old_server', 'future_server', 'late_normalization', 'changed_query', 'historical', 'superseded'])
def test_causal_freshness_and_exact_public_request_are_required(books, change):
    body = response(books); fields = {}; changes = {}
    if change == 'old_server': body['timestamp'] = str(int((books['now'][0]-31)*1000))
    if change == 'future_server': body['timestamp'] = str(int((books['now'][0]+1)*1000))
    if change == 'changed_query': changes['request_params'] = {'token_id':'wrong'}
    if change == 'historical': fields['evidence_class'] = 'HISTORICAL_AVAILABILITY_UNKNOWN'
    raw(books, body=body, changes=changes, **fields)
    if change == 'late_normalization': books['now'][0] += 31
    if change == 'superseded': raw(books, 'new')
    with pytest.raises(EvidenceError): normalize(books)


def test_empty_side_is_archived_as_absence_of_liquidity_and_cannot_make_microstructure_features(books):
    body = response(books); body['asks'] = []; raw(books, body=body); row = normalize(books)
    policy = MicrostructurePolicy('fixture','FIXTURE_COLLATERAL',30.,60.,10.,.1,2)
    d = MakerMicrostructure(books['store']).evaluate('micro', rule=books['rule'], market_id=books['member']['market_id'],
        side='YES',book_ids=(row['id'],),trade_ids=(),policy=policy)['body']['details']
    assert d['outcome'] == 'GATED' and d['reason'] == 'MICROSTRUCTURE_BOOK_SIDE_MISSING'


def test_rest_history_never_manufactures_continuous_microstructure_or_fills(books):
    raw(books); one = normalize(books); books['now'][0] += 1; raw(books, 'next'); two = normalize(books, 'next')
    policy = MicrostructurePolicy('fixture','FIXTURE_COLLATERAL',30.,60.,10.,.1,2)
    d = MakerMicrostructure(books['store']).evaluate('micro', rule=books['rule'], market_id=books['member']['market_id'],
        side='YES',book_ids=(one['id'],two['id']),trade_ids=(),policy=policy)['body']['details']
    assert d['outcome'] == 'MEASURED_RESEARCH_FEATURES' and d['temporal']['status'] == 'UNKNOWN'
    assert d['temporal']['resets'][0]['reason'] == 'SEQUENCE_UNAVAILABLE' and d['fill_probability'] is None
    assert not books['store'].records(kind='TRADE')


def test_book_head_racing_normalization_requires_new_evidence_without_partial_output(books, monkeypatch):
    raw(books); original = books['store']._append
    def race(*args, **kw):
        monkeypatch.setattr(books['store'], '_append', original)
        raw(books, 'new')
        return original(*args, **kw)
    monkeypatch.setattr(books['store'], '_append', race)
    with pytest.raises(EvidenceError, match='AUDIT_STATE_CHANGED'): normalize(books)
    assert len(books['store'].records(kind='BOOK')) == 2


def test_weather_normalization_keeps_original_receipt_and_replay_never_refreshes_it(books):
    store = books['store']; at = books['now'][0]
    source = store.capture('awc', event_id=books['rule'].payload['event_id'], kind='OFFICIAL_OBSERVATION', provider='NOAA_AWC',
        source_identity='KATL', revision='one', evidence_class='SYNTHETIC',
        payload={'response':[dict(icaoId='KATL',obsTime=at-1,temp=25)]})
    books['now'][0] += 5
    one = normalize_weather_capture(store, source['id'], record_id='normalized', station='KATL')
    assert one['body']['received_at'] == at and one['body']['available_at'] == at+5
    assert one['body']['observed_at'] == at-1 and one['body']['payload']['observed_time_scope']=='LATEST_ACCEPTED_PROVIDER_OBSERVATION'
    books['now'][0] += 5
    assert normalize_weather_capture(store, source['id'], record_id='normalized', station='KATL') == one
    with pytest.raises(EvidenceError, match='RAW_SUPERSEDED'):
        normalize_weather_capture(store, source['id'], record_id='again', station='KATL')
    with pytest.raises(EvidenceError, match='ID_CONFLICT'):
        normalize_weather_capture(store, source['id'], record_id='normalized', station='KSEA')
