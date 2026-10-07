from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from polymarket_scanner.v11.evidence import EvidenceError, EvidenceStore, canonical, digest
from polymarket_scanner.v11.weather_sources import normalize_weather_capture
from tools.v11_brain_label_attestation import NAMESPACE, attest_day

NOW = 2_000_000_100.0
DAY = '2026-10-05'
STATION = 'KATL'
EVENT = 'gamma-event'
PARTITION = [
    {'market_id': 'm0', 'condition_id': 'c0', 'yes_token': 'yes0', 'no_token': 'no0', 'lower': None, 'upper': 69},
    {'market_id': 'm1', 'condition_id': 'c1', 'yes_token': 'yes1', 'no_token': 'no1', 'lower': 70, 'upper': 74},
    {'market_id': 'm2', 'condition_id': 'c2', 'yes_token': 'yes2', 'no_token': 'no2', 'lower': 75, 'upper': None},
]


def observations(peak=72):
    return [{'icaoId': STATION,
             'obsTime': datetime(2026, 10, 5, hour, tzinfo=ZoneInfo('America/New_York')).timestamp(),
             'temp': ((peak if hour == 14 else 50) - 32) * 5 / 9}
            for hour in range(24)]


def fixture(tmp_path, *, wrong_gamma_payout=False, gamma_form='market', gamma_event_id=EVENT):
    root = tmp_path / DAY
    root.mkdir()
    root.chmod(0o700)
    store = EvidenceStore(root / 'source.sqlite', NAMESPACE, clock=lambda: NOW)
    rule = {'event_id': EVENT, 'station': STATION, 'city': 'atlanta', 'target_date': DAY,
            'timezone': 'America/New_York', 'statistic': 'DAILY_HIGHEST_TEMP',
            'unit': 'F', 'precision_rounding': 'WHOLE_DEGREE_F', 'partition': PARTITION}
    fp = digest(rule)
    prediction_sha = digest({'prediction': EVENT, 'day': DAY})
    targets = {b['market_id']: {'market_id': b['market_id'], 'condition_id': b['condition_id'],
                                'token_id': b['yes_token'], 'side': 'YES'} for b in PARTITION}
    rows = []
    for mid, target in targets.items():
        decision = store._append('decision-' + mid, 'DECISION', EVENT,
                                 {'target': 'FINAL_CONTRACT_PAYOUT',
                                  'binding': {'rule_fingerprint': fp},
                                  'explanation': {'target_identity': target}}, NOW, NOW)
        rows.append({'target_identity': target, 'decision_id': decision['id'],
                     'decision_sha256': decision['sha256']})
    capture = store.audit('capture', event_id=EVENT, kind='MEASUREMENT', details={
        'complete_event_vector': True, 'parent_feature_contract_verified': True,
        'prediction_sha256': prediction_sha,
        'binding': {'rule_fingerprint': fp}, 'rule': {'sha256': fp, 'canonical_json': canonical(rule)},
        'context': {'station_id': STATION, 'event_id': EVENT, 'city_id': 'atlanta'},
        'rows': rows})
    store.audit('rule', event_id=EVENT, kind='RULE_STATE',
                details={'preimage': rule, 'fingerprint': fp, 'quarantined': False})
    label_ids = {}
    for mid, target in targets.items():
        bucket = next(b for b in PARTITION if b['market_id'] == mid)
        market = {'id': mid, 'conditionId': bucket['condition_id'], 'closed': True,
                  'outcomes': '["Yes","No"]',
                  'outcomePrices': '["1","0"]' if mid == 'm1' and not wrong_gamma_payout else '["0","1"]',
                  'clobTokenIds': json.dumps([bucket['yes_token'], bucket['no_token']])}
        event_response = {'id': gamma_event_id, 'markets': [market]}
        response = market if gamma_form == 'market' else event_response
        if gamma_form.endswith('-list'):
            response = [response]
        source_payload = ({'event': response} if gamma_form.startswith('event')
                          else {'response': response})
        gamma = store.capture('gamma-source-' + mid, event_id=EVENT, kind='RULES',
                              provider='GAMMA_MARKET' if gamma_form == 'market' else 'GAMMA_EVENT',
                              source_identity='market:' + mid if gamma_form == 'market' else 'event:' + EVENT,
                              revision='1', payload=source_payload)
        payload = {'context': {'station': STATION, 'city': 'atlanta', 'local_date': DAY,
                               'target': 'FINAL_CONTRACT_PAYOUT', 'rule_fingerprint': fp},
                   'target_identity': target, 'decision_target': 'FINAL_CONTRACT_PAYOUT',
                   'evidence_type': 'EXACT_SOURCE_LABEL', 'label_version': '1',
                   'value': int(mid == 'm1'), 'knowable_at': NOW,
                   'source_capture_id': gamma['id'], 'source_capture_sha256': gamma['sha256']}
        label_ids[mid] = store.capture('label-' + mid, event_id=EVENT, kind='LABEL',
                                      provider='GAMMA_CLOSED_MARKET_EXACT_TOKEN_PAYOUT',
                                      source_identity=mid, revision='1', payload=payload)['id']
    (root / 'capture-status.json').write_text(json.dumps(
        {'capture_id': capture['id'], 'prediction_sha256': prediction_sha}))
    (root / 'labels-status.json').write_text(json.dumps({'label_ids': label_ids}))
    return store, root, label_ids


def raw_weather(store, response=None, key='raw'):
    return store.capture(key, event_id='station:' + STATION, kind='OFFICIAL_OBSERVATION',
                         provider='NOAA_AWC', source_identity=STATION, revision='1',
                         payload={'response': observations() if response is None else response})


def normalized_weather(store, raw, key='normalized', response=None):
    rows = observations() if response is None else response
    day_start = datetime(2026, 10, 5, tzinfo=ZoneInfo('America/New_York')).timestamp()
    policy_sha = digest(dict(raw_id=raw['id'], raw_sha256=raw['sha256'], station=STATION,
                             official_max_age_seconds=max(1.0, raw['body']['received_at'] - day_start + 1.0)))
    return store.capture(key, event_id='station:' + STATION, kind='OFFICIAL_OBSERVATION',
                         provider='NOAA_AWC', source_identity=STATION, revision='1',
                         payload={'observations': [{'station': STATION, 'observed_at': r['obsTime'],
                                                    'temperature_c': r['temp']} for r in rows],
                                  'raw_evidence_id': raw['id'], 'raw_evidence_sha256': raw['sha256'],
                                  'normalization_sha256': policy_sha,
                                  'settlement_station_context': STATION})


def test_real_raw_normalized_pair(tmp_path):
    store, _, _ = fixture(tmp_path)
    raw = raw_weather(store)
    normalize_weather_capture(store, raw['id'], record_id='normalized', station=STATION,
                              official_max_age_seconds=NOW - observations()[0]['obsTime'] + 1)
    result = attest_day(tmp_path, DAY, now=NOW)
    assert result['state'] == 'OFFICIAL_OBSERVATION_PROXY_CORROBORATION_CONSISTENT'
    assert all(result[k] is False for k in ('independent_label_attestation', 'settlement_authority',
                                             'financial_authority', 'automatic_promotion'))


@pytest.mark.parametrize('retained', [list(range(3, 24)), list(range(21))])
def test_raw_day_prefix_or_suffix_cannot_be_removed(tmp_path, retained):
    store, _, _ = fixture(tmp_path)
    data = observations(peak=72)
    data[1 if 1 not in retained else 22]['temp'] = (80 - 32) * 5 / 9
    raw = raw_weather(store, data)
    normalized_weather(store, raw, response=[data[i] for i in retained])
    with pytest.raises(EvidenceError, match='ATTESTATION_RAW_DERIVED_MISMATCH'):
        attest_day(tmp_path, DAY, now=NOW)


def test_real_normalizer_shorter_age_window_refuses(tmp_path):
    store, _, _ = fixture(tmp_path)
    data = observations(peak=72)
    data[1]['temp'] = (80 - 32) * 5 / 9
    raw = raw_weather(store, data)
    normalize_weather_capture(store, raw['id'], record_id='short-age', station=STATION,
                              official_max_age_seconds=NOW - data[3]['obsTime'])
    with pytest.raises(EvidenceError, match='ATTESTATION_NORMALIZATION_POLICY_MISMATCH'):
        attest_day(tmp_path, DAY, now=NOW)


@pytest.mark.parametrize('derivation', ['missing', 'empty'])
def test_contradictory_raw_without_populated_derivation_refuses(tmp_path, derivation):
    store, _, _ = fixture(tmp_path)
    first = raw_weather(store)
    normalized_weather(store, first)
    late = raw_weather(store, observations(peak=80), key='late')
    if derivation == 'empty':
        normalized_weather(store, late, key='late-normalized', response=[])
    with pytest.raises(EvidenceError, match=('ATTESTATION_RAW_UNPROCESSED' if derivation == 'missing'
                                             else 'ATTESTATION_RAW_DERIVED_MISMATCH')):
        attest_day(tmp_path, DAY, now=NOW)


def test_raw_receipt_cannot_hide_in_derived_shape(tmp_path):
    store, _, _ = fixture(tmp_path)
    first = raw_weather(store)
    normalized_weather(store, first)
    store.capture('ambiguous-raw', event_id='station:' + STATION, kind='OFFICIAL_OBSERVATION',
                  provider='NOAA_AWC', source_identity=STATION, revision='1',
                  payload={'response': observations(peak=80), 'observations': []})
    with pytest.raises(EvidenceError, match='ATTESTATION_RAW_RESPONSE_INVALID'):
        attest_day(tmp_path, DAY, now=NOW)


@pytest.mark.parametrize('form', ['event', 'response', 'event-list', 'response-list'])
def test_gamma_event_container_id_is_bound(tmp_path, form):
    store, _, _ = fixture(tmp_path, gamma_form=form, gamma_event_id='WRONG-EVENT')
    raw = raw_weather(store)
    normalized_weather(store, raw)
    with pytest.raises(EvidenceError, match='ATTESTATION_LABEL_SOURCE_INVALID'):
        attest_day(tmp_path, DAY, now=NOW)


@pytest.mark.parametrize('form', ['event', 'response', 'event-list', 'response-list'])
def test_matching_gamma_event_container_passes(tmp_path, form):
    store, _, _ = fixture(tmp_path, gamma_form=form)
    raw = raw_weather(store)
    normalized_weather(store, raw)
    assert attest_day(tmp_path, DAY, now=NOW)['official_observation_corroboration'] == 'CONSISTENT'


def test_forged_normalization_policy_digest_refuses(tmp_path):
    store, _, _ = fixture(tmp_path)
    raw = raw_weather(store)
    normalized_weather(store, raw)
    with store._connect() as db:
        db.execute('DROP TRIGGER v11_no_update')
        row = db.execute("SELECT body FROM v11_records WHERE record_id='normalized'").fetchone()
        body = json.loads(row[0])
        body['payload']['normalization_sha256'] = 'f' * 64
        db.execute("UPDATE v11_records SET body=?,body_sha256=? WHERE record_id='normalized'",
                   (json.dumps(body, sort_keys=True, separators=(',', ':')), digest(body)))
    with pytest.raises(EvidenceError, match='ATTESTATION_NORMALIZATION_POLICY_MISMATCH'):
        attest_day(tmp_path, DAY, now=NOW)


def test_real_reference_hash_with_wrong_gamma_payout_refuses(tmp_path):
    store, _, _ = fixture(tmp_path, wrong_gamma_payout=True)
    raw = raw_weather(store)
    normalized_weather(store, raw)
    with pytest.raises(EvidenceError, match='ATTESTATION_LABEL_SOURCE_INVALID'):
        attest_day(tmp_path, DAY, now=NOW)


def test_run_does_not_persist_false_corroboration(tmp_path):
    from tools.v11_brain_label_attestation import run
    store, _, _ = fixture(tmp_path, wrong_gamma_payout=True)
    raw = raw_weather(store)
    normalized_weather(store, raw)
    with pytest.raises(EvidenceError, match='ATTESTATION_LABEL_SOURCE_INVALID'):
        run(tmp_path, now=NOW)
    assert not (tmp_path / 'label-attestation-status.json').exists()


def test_run_replay_is_stable_and_archive_is_read_only(tmp_path):
    from tools.v11_brain_label_attestation import run
    store, _, _ = fixture(tmp_path)
    raw = raw_weather(store)
    normalized_weather(store, raw)
    before = hashlib.sha256(store.path.read_bytes()).hexdigest()
    first = run(tmp_path, now=NOW)
    artifact = (tmp_path / 'label-attestation-status.json').read_bytes()
    second = run(tmp_path, now=NOW)
    assert first == second
    assert artifact == (tmp_path / 'label-attestation-status.json').read_bytes()
    assert hashlib.sha256(store.path.read_bytes()).hexdigest() == before
    assert first['days'][0]['independent_label_attestation'] is False


def test_forged_capture_child_hash_refuses(tmp_path):
    store, _, _ = fixture(tmp_path)
    raw = raw_weather(store)
    normalized_weather(store, raw)
    with store._connect() as db:
        db.execute('DROP TRIGGER v11_no_update')
        row = db.execute("SELECT body FROM v11_records WHERE record_id='capture'").fetchone()
        body = json.loads(row[0])
        body['details']['rows'][1]['decision_sha256'] = '1' * 64
        db.execute("UPDATE v11_records SET body=?,body_sha256=? WHERE record_id='capture'",
                   (json.dumps(body, sort_keys=True, separators=(',', ':')), digest(body)))
    with pytest.raises(EvidenceError, match='ATTESTATION_CAPTURE_CHILD_MISMATCH'):
        attest_day(tmp_path, DAY, now=NOW)


@pytest.mark.parametrize('fault', ['missing_gamma', 'forged_gamma_hash', 'wrong_condition',
                                   'wrong_target', 'wrong_side', 'wrong_station', 'wrong_day',
                                   'wrong_rule', 'wrong_event', 'missing_weather',
                                   'forged_weather_hash', 'synthetic_weather', 'omitted_raw_peak'])
def test_reference_and_identity_attacks_refuse(tmp_path, fault):
    store, _, ids = fixture(tmp_path)
    raw = raw_weather(store)
    normalized_weather(store, raw)
    with store._connect() as db:
        db.execute('DROP TRIGGER v11_no_update')
        db.execute('DROP TRIGGER v11_no_delete')
        if fault in ('missing_gamma', 'forged_gamma_hash', 'wrong_condition', 'wrong_target',
                     'wrong_side', 'wrong_station', 'wrong_day', 'wrong_rule', 'wrong_event'):
            row = db.execute('SELECT body FROM v11_records WHERE record_id=?', (ids['m1'],)).fetchone()
            body = json.loads(row[0])
            payload = body['payload']
            if fault == 'missing_gamma': payload['source_capture_id'] = 'missing'
            if fault == 'forged_gamma_hash': payload['source_capture_sha256'] = '1' * 64
            if fault == 'wrong_condition': payload['target_identity']['condition_id'] = 'wrong'
            if fault == 'wrong_target': payload['target_identity']['token_id'] = 'no1'
            if fault == 'wrong_side': payload['target_identity']['side'] = 'NO'
            if fault == 'wrong_station': payload['context']['station'] = 'KJFK'
            if fault == 'wrong_day': payload['context']['local_date'] = '2026-10-06'
            if fault == 'wrong_rule': payload['context']['rule_fingerprint'] = '0' * 64
            if fault == 'wrong_event': body['event_id'] = 'other-event'
            db.execute('UPDATE v11_records SET body=?,body_sha256=?,event_id=? WHERE record_id=?',
                       (json.dumps(body, sort_keys=True, separators=(',', ':')), digest(body), body['event_id'], ids['m1']))
        else:
            row = db.execute("SELECT body FROM v11_records WHERE record_id='normalized'").fetchone()
            body = json.loads(row[0])
            if fault == 'missing_weather': body['payload']['raw_evidence_id'] = 'missing'
            if fault == 'forged_weather_hash': body['payload']['raw_evidence_sha256'] = '2' * 64
            if fault == 'synthetic_weather': body['evidence_class'] = 'SYNTHETIC'
            if fault == 'omitted_raw_peak':
                body['payload']['observations'] = [o for o in body['payload']['observations']
                                                   if o['observed_at'] != observations()[14]['obsTime']]
            db.execute("UPDATE v11_records SET body=?,body_sha256=? WHERE record_id='normalized'",
                       (json.dumps(body, sort_keys=True, separators=(',', ':')), digest(body)))
    with pytest.raises(EvidenceError):
        attest_day(tmp_path, DAY, now=NOW)


def test_201st_conflict_is_inspected(tmp_path):
    store, _, _ = fixture(tmp_path)
    base = observations()
    raw = raw_weather(store, base)
    for i in range(200):
        normalized_weather(store, raw, key=f'normalized-{i}', response=base)
    conflict = copy.deepcopy(base)
    conflict[14]['temp'] = (78 - 32) * 5 / 9
    late_raw = raw_weather(store, conflict, key='late-raw')
    normalized_weather(store, late_raw, key='late-conflict', response=conflict)
    with pytest.raises(EvidenceError, match='ATTESTATION_OFFICIAL_OBSERVATION_CONFLICT'):
        attest_day(tmp_path, DAY, now=NOW)


def test_incomplete_archive_refuses(tmp_path, monkeypatch):
    from tools import v11_brain_label_attestation as runner
    store, _, _ = fixture(tmp_path)
    raw = raw_weather(store)
    normalized_weather(store, raw)
    monkeypatch.setattr(runner, 'MAX_ROWS', 2)
    with pytest.raises(EvidenceError, match='ATTESTATION_ARCHIVE_INCOMPLETE_BOUND'):
        attest_day(tmp_path, DAY, now=NOW)


def test_byte_bound_refuses(tmp_path, monkeypatch):
    from tools import v11_brain_label_attestation as runner
    store, _, _ = fixture(tmp_path)
    raw = raw_weather(store)
    normalized_weather(store, raw)
    monkeypatch.setattr(runner, 'MAX_BYTES', 1)
    with pytest.raises(EvidenceError, match='ATTESTATION_ARCHIVE_INCOMPLETE_BOUND'):
        attest_day(tmp_path, DAY, now=NOW)


def test_read_view_pins_archive_tip(tmp_path):
    from tools.v11_brain_label_attestation import _ReadView
    store, _, _ = fixture(tmp_path)
    raw = raw_weather(store)
    normalized_weather(store, raw)
    with _ReadView(store.path) as view:
        tip = view.tip
        later = raw_weather(store, key='later-raw')
        assert later['seq'] > tip
        assert later['id'] not in {row['id'] for row in view.station_rows(STATION)}
