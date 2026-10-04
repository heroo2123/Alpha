import copy
from polymarket_scanner.v11.evidence import canonical,digest
from polymarket_scanner.v11.rules import RuleFingerprint
from polymarket_scanner.v11.rule_rollforward import envelope_from_reviewed,assess_rollforward

def rule(day,start=63,source='https://www.weather.gov/wrh/timeseries?site=katl'):
    part=[]
    for i in range(11):
        lo=None if i==0 else start+1+(i-1)*2
        hi=start if i==0 else None if i==10 else start+2*i
        part.append(dict(market_id=f'm{day}-{i}',condition_id=f'c{day}-{i}',question=f'q{i}',
                         lower=lo,upper=hi,unit='F',yes_token=f'y{day}-{i}',no_token=f'n{day}-{i}'))
    p=dict(version='alpha_v11_universal_rule_v1',event_id=str(day),title='x',
       strict_contract=dict(version='weather_contract_strict_v9_reviewed_city_station_binding',
          event_id=str(day),family='daily_high_temperature',location='atlanta',
          operative_rules='strict-compiler-already-validated',operative_source=source,
          questions=['q']*11,sha256='b'*64,station='KATL',target_date=f'2026-10-{day:02d}',unit='F'),
       station='KATL',city='atlanta',target_date=f'2026-10-{day:02d}',timezone='America/New_York',
       unit='F',family='daily_high_temperature',statistic='DAILY_HIGHEST_TEMP',
       observation_population='WRH_HOURLY_DATA',precision_rounding='WHOLE_DEGREE_F',
       primary_source=source,source_family='NWS_WRH_TIMESERIES',
       fallback_policy='WEATHER_UNDERGROUND_IF_WRH_UNAVAILABLE_BY_NEXT_DAY_2359_ET',
       correction_policy='ACCEPT_REVISIONS_UNTIL_FIRST_FOLLOWING_DATE_DATAPOINT',
       finality_and_deadline_policy='FIRST_FOLLOWING_DATE_DATAPOINT_OR_NEXT_DAY_2359_ET',
       no_data_outcome='LOWEST_BRACKET',partition=part,metadata_fingerprint='e'*64,
       compiler_version='weather_only_contract_compiler_v1_inventory_no_financial_authority',
       semantic_profile_version='weather_temperature_rule_authority_v2_hko_decimal_fail_closed',
       financial_authority=False)
    return RuleFingerprint(canonical(p),digest(p),'a'*64)

def test_daily_shift_and_new_ids_are_allowed():
    env=envelope_from_reviewed(rule(4,63))
    out=assess_rollforward(env,rule(5,69))
    assert out['passed'] is True and out['reason']=='RULE_TEMPLATE_MATCH'
    assert out['financial_authority'] is False

def test_material_source_change_fails_closed():
    env=envelope_from_reviewed(rule(4))
    out=assess_rollforward(env,rule(5,69,'https://evil.example/wrh'))
    assert out['passed'] is False and out['reason']=='INVARIANT_CHANGED'

def test_strict_contract_identity_change_fails_closed():
    base=rule(4);env=envelope_from_reviewed(base)
    p=copy.deepcopy(rule(5).payload);p['strict_contract']['location']='atlanta metro'
    changed=RuleFingerprint(canonical(p),digest(p),'a'*64)
    assert assess_rollforward(env,changed)['reason']=='STRICT_CONTRACT_IDENTITY_CHANGED'

def test_partition_shape_change_fails_closed():
    base=rule(4);env=envelope_from_reviewed(base)
    p=copy.deepcopy(rule(5).payload);p['partition'][2]['upper']+=1
    changed=RuleFingerprint(canonical(p),digest(p),'a'*64)
    out=assess_rollforward(env,changed)
    assert out['passed'] is False

def test_unit_change_fails_closed():
    base=rule(4);env=envelope_from_reviewed(base)
    p=copy.deepcopy(rule(5).payload);p['unit']='C'
    for x in p['partition']:x['unit']='C'
    changed=RuleFingerprint(canonical(p),digest(p),'a'*64)
    assert assess_rollforward(env,changed)['passed'] is False
