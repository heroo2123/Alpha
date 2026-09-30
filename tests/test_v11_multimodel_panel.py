import copy
from dataclasses import replace
from datetime import datetime, timezone
import gzip
import json
import os
from pathlib import Path
import random
import sqlite3

import pytest

from tools import v11_multimodel_panel as panel
from tools import v11_multimodel_stacking as stack


def context(day='2026-08-23', zone='America/New_York'):
    start, end = panel.local_window(day, zone)
    run = ((start-3600)//21600)*21600
    dt = datetime.fromtimestamp(run, timezone.utc)
    return dict(station='KATL', target_date=day, timezone=zone, run_utc=dt.isoformat(),
                run_date=dt.date().isoformat(), cycle=dt.hour, latitude=33.64028, longitude=-84.42694,
                split=panel.split_for(day), metadata_evidence_sha256='a'*64,
                forecast_hours=list(range(int((start-run)//10800)*3, int(-(-(end-run)//10800))*3+1, 3)))


@pytest.mark.parametrize('zone,day,hours', [
    ('America/New_York', '2026-03-08', 23), ('America/New_York', '2026-11-01', 25),
    ('Europe/London', '2026-03-29', 23), ('Europe/London', '2026-10-25', 25),
    ('Asia/Kolkata', '2026-08-23', 24), ('America/Sao_Paulo', '2026-08-23', 24),
])
def test_local_calendar_day_handles_dst_and_fractional_offsets(zone, day, hours):
    start, end = panel.local_window(day, zone)
    assert end-start == hours*3600


@pytest.mark.parametrize('zone,start,end,missing_start,missing_end', [
    ('America/New_York', 4, 28, True, False), ('America/Chicago', 5, 29, True, False),
    ('America/Denver', 6, 30, False, False), ('America/Los_Angeles', 1, 25, False, True),
    ('America/Sao_Paulo', 3, 27, True, True), ('Europe/London', 5, 29, True, False),
])
def test_filtering_plan_loses_brackets(zone, start, end, missing_start, missing_end):
    row = context(zone=zone)
    ctx = panel.day_context(row)
    assert (ctx['start_forecast_hour'], ctx['end_forecast_hour']) == (start, end)
    c = panel.coverage([h for h in row['forecast_hours'] if h%6 == 0], ctx)
    assert c['brackets_start'] is not missing_start
    assert c['brackets_end'] is not missing_end
    assert c['exact_day_extreme_supported'] is False
    assert c['interpolation'] is False


@pytest.mark.parametrize('family', panel.FAMILIES)
def test_sampled_extreme_excludes_outside_values_and_next_midnight(family):
    ctx = dict(start_forecast_hour=6, end_forecast_hour=30)
    values = [350, 280, 290, 285, 287, 150]
    result = panel.sampled_extrema(values, [0, 6, 12, 18, 24, 30], ctx, family)
    assert result == (290 if family == panel.FAMILIES[0] else 280)-273.15


def test_point_samples_cannot_identify_between_sample_extreme():
    # Two continuous paths agree at every retained point, yet one peaks at 310K.
    hours, observed = [6, 12, 18, 24, 30], [280]*5
    ctx = dict(start_forecast_hour=6, end_forecast_hour=30)
    assert panel.sampled_extrema(observed, hours, ctx, panel.FAMILIES[0]) == 280-273.15
    hidden_path = list(zip(hours, observed)) + [(15, 310)]
    assert max(v for h, v in hidden_path if 6 <= h < 30) != max(observed)
    assert not panel.coverage(hours, ctx)['exact_day_extreme_supported']


def test_randomized_native_day_filter_property():
    rng = random.Random(90210)
    for _ in range(100):
        start = rng.choice([1, 3, 4, 5, 6])
        ctx = dict(start_forecast_hour=start, end_forecast_hour=start+24)
        hours = list(range(0, 37, 6))
        vals = [rng.uniform(240, 315) for _ in hours]
        selected = [v for h, v in zip(hours, vals) if start <= h < start+24]
        for family, fn in zip(panel.FAMILIES, (max, min)):
            assert panel.sampled_extrema(vals, hours, ctx, family) == fn(selected)-273.15


@pytest.mark.parametrize('field,value,reason', [
    ('split', 'HISTORICAL_CONFIRMATION', 'SPLIT'), ('cycle', 6, 'RUN_IDENTITY'),
    ('forecast_hours', [6, 12, 18, 24, 30], 'BRACKETS'),
    ('run_utc', '2026-08-23T06:00:00+00:00', 'PRE_DAY'),
    ('metadata_evidence_sha256', 'missing', 'DIGEST'),
    ('run_utc', '2026-08-23T00:00:00', 'EXPLICIT_UTC'),
])
def test_plan_drift_fails_closed(field, value, reason):
    row = context()
    row[field] = value
    with pytest.raises(panel.PanelError, match=reason):
        panel.day_context(row)


def test_immutable_db_hashes_and_readonly(tmp_path):
    path = tmp_path/'input.sqlite'
    db = sqlite3.connect(path)
    db.execute('create table test(id integer primary key, value text)')
    db.execute("insert into test values (1,'original')")
    db.commit()
    pin = dict(file_sha256=panel.file_sha(path), content_sha256=panel.content_sha(db))
    db.close()
    before = path.read_bytes()
    with panel.immutable_db(path, pin) as ro:
        with pytest.raises(sqlite3.OperationalError, match='readonly'):
            ro.execute("update test set value='modified'")
    assert path.read_bytes() == before
    assert not Path(str(path)+'-shm').exists()
    with pytest.raises(panel.PanelError, match='CONTENT_HASH'):
        with panel.immutable_db(path, dict(pin, content_sha256='0'*64)):
            pass
    with pytest.raises(panel.PanelError, match='FILE_HASH'):
        with panel.immutable_db(path, dict(pin, file_sha256='0'*64)):
            pass
    Path(str(path)+'-wal').write_bytes(b'pending')
    with pytest.raises(panel.PanelError, match='UNQUIESCED'):
        with panel.immutable_db(path, pin):
            pass


def test_canonical_artifact_and_output_conflicts(tmp_path):
    path = tmp_path/'artifact.json'
    body = dict(a=1, b=2)
    body['artifact_sha256'] = panel.digest(body)
    path.write_bytes(panel.canonical(body))
    assert panel.artifact(path, dict(file_sha256=panel.file_sha(path))) == body
    panel.write_once(path, path.read_bytes())
    with pytest.raises(panel.PanelError, match='OUTPUT_CONFLICT'):
        panel.write_once(path, b'changed')
    body['a'] = 3
    path.write_bytes(panel.canonical(body))
    with pytest.raises(panel.PanelError, match='SELF_HASH'):
        panel.artifact(path, dict(file_sha256=panel.file_sha(path)))
    with pytest.raises(panel.PanelError, match='DUPLICATE'):
        panel.strict_json('{"a":1,"a":2}')
    with pytest.raises(panel.PanelError, match='NONFINITE'):
        panel.strict_json('{"a":NaN}')


def fixture_databases(tmp_path):
    """Miniature two-provider historical schema, with real source identity shapes."""
    g = sqlite3.connect(tmp_path/'g.sqlite')
    e = sqlite3.connect(tmp_path/'e.sqlite')
    for db in (g, e):
        db.row_factory = sqlite3.Row
    g.executescript('''
        CREATE TABLE station_days(station_day,station,target_date,split,expected_values,status);
        CREATE TABLE messages(message_key,run_date,cycle,hour,member,status,attempts,idx_sha256,grib_sha256,bytes,error,updated_at);
        CREATE TABLE point_values(message_key,station_day,member,hour,value_k,distance_km,nearest_lat,nearest_lon);
    ''')
    e.executescript('''
        CREATE TABLE station_days(station_day,split,expected_values,context_json);
        CREATE TABLE station_provider_expectations(station_day,provider,expected_values,required_hours_json,omitted_hours_json);
        CREATE TABLE messages(message_key,provider,run_date,cycle,hour,member,status,attempts,public_url,index_sha256,index_byte_length,grib_sha256,byte_length,range_start,observed_header_sha256,grid_sha256,error,updated_at);
        CREATE TABLE point_values(message_key,station_day,provider,member,hour,value_k,distance_km,nearest_lat,nearest_lon);
    ''')
    days = [context(day) for day in ('2026-08-23', '2026-09-16', '2026-09-24')]
    events = []
    for index, row in enumerate(days):
        sid = row['station']+'|'+row['target_date']
        common = [h for h in row['forecast_hours'] if h%6 == 0]
        g.execute('insert into station_days values(?,?,?,?,?,?)', (sid, row['station'], row['target_date'], row['split'], 31*len(row['forecast_hours']), 'COMPLETE'))
        e.execute('insert into station_days values(?,?,?,?)', (sid, row['split'], 102*len(common), json.dumps(row)))
        for provider, count in panel.PROVIDERS.items():
            hours = row['forecast_hours'] if provider == 'GEFS' else common
            if provider != 'GEFS':
                e.execute('insert into station_provider_expectations values(?,?,?,?,?)',
                          (sid, provider, 51*len(hours), json.dumps(hours), json.dumps([h for h in row['forecast_hours'] if h not in hours])))
            for member in range(count):
                for hour in hours:
                    key = panel.message_key(provider, row, hour, member)
                    if provider == 'GEFS':
                        g.execute('insert into messages values(?,?,?,?,?,?,?,?,?,?,?,?)',
                                  (key,row['run_date'],row['cycle'],hour,member,'DONE',1,'a'*64,'b'*64,100,None,1800000000.))
                        g.execute('insert into point_values values(?,?,?,?,?,?,?,?)',
                                  (key,sid,member,hour,280+hour/10,0,row['latitude'],row['longitude']))
                    else:
                        e.execute('insert into messages values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                                  (key,provider,row['run_date'],row['cycle'],hour,member,'DONE',1,
                                   panel.expected_url(provider,row,hour,member),'a'*64,100,'b'*64,100,0,'c'*64,'d'*64,None,1800000000.))
                        e.execute('insert into point_values values(?,?,?,?,?,?,?,?,?)',
                                  (key,sid,provider,member,hour,280+hour/10,0,row['latitude'],row['longitude']))
        for j, family in enumerate(panel.FAMILIES):
            identity = f'{index}_{j}'
            events.append(dict(event_id=identity, station='KATL', target_date=row['target_date'], family=family,
                               source_family='NWS_WRH_TIMESERIES', unit='C', complete_final_vector=True,
                               exactly_one_winner=True, bucket_count=2,
                               slug=('highest' if j == 0 else 'lowest')+'-temperature-in-atlanta-on-'+row['target_date'],
                               source_urls=['https://www.weather.gov/wrh/timeseries?site=katl'],
                               buckets=[dict(market_id=identity+str(k), condition_id='c'+identity+str(k),
                                             yes_token='y'+identity+str(k), no_token='n'+identity+str(k),
                                             status='FINAL', lower=None if k == 0 else 11,
                                             upper=10 if k == 0 else None, yes_payout=1-k, no_payout=k) for k in (0,1)]))
    plan = dict(station_days=days, stations={'KATL': {k: days[0][k] for k in ('station','latitude','longitude','timezone','metadata_evidence_sha256')}},
                catalog_sha256='f'*64, split_counts={s:dict(station_days=1) for s in panel.SPLITS},
                financial_authority=False,promotion_authority=False,untouched_forward_holdout_claim=False)
    catalog = dict(events=events,artifact_sha256='f'*64,created_utc='2026-09-29T00:00:00+00:00')
    return plan, catalog, g, e


def test_integration_panel_deterministic_preserves_splits_and_gate(tmp_path):
    args = fixture_databases(tmp_path)
    try:
        a = panel.assemble(*args)
        b = panel.assemble(*args)
        assert panel.canonical(a) == panel.canonical(b)
        assert gzip.compress(panel.canonical(a), mtime=0) == gzip.compress(panel.canonical(b), mtime=0)
        assert [d['context']['split'] for d in a['station_days']] == list(panel.SPLITS)
        r = panel.admission_report(a)
        assert r['training_status'] == 'NOT_FITTED'
        assert all(c['brier'] is None for c in r['comparisons'].values())
        assert all(d['providers']['IFS']['exact_day_extrema_c'] is None for d in a['station_days'])
        # Changing a later label cannot change any feature payload.
        args[1]['events'][-1]['buckets'][0]['yes_payout'] = 0
        args[1]['events'][-1]['buckets'][0]['no_payout'] = 1
        args[1]['events'][-1]['buckets'][1]['yes_payout'] = 1
        args[1]['events'][-1]['buckets'][1]['no_payout'] = 0
        assert panel.assemble(*args)['station_days'] == a['station_days']
    finally:
        args[2].close(); args[3].close()


@pytest.mark.parametrize('sql,reason', [
    ("DELETE FROM point_values WHERE rowid=1", 'COVERAGE'),
    ("UPDATE point_values SET member=1 WHERE rowid=1", 'COVERAGE'),
    ("UPDATE point_values SET hour=9 WHERE rowid=1", 'COVERAGE'),
    ("UPDATE messages SET status='FAILED' WHERE rowid=1", 'STATUS'),
    ("UPDATE messages SET run_date='2026-09-30' WHERE rowid=1", 'IDENTITY'),
    ("UPDATE messages SET provider='AIFS' WHERE rowid=1", 'SOURCE_IDENTITY'),
    ("UPDATE messages SET grib_sha256='bad' WHERE rowid=1", 'PROVENANCE'),
    ("UPDATE messages SET public_url='https://invalid' WHERE rowid=1", 'SOURCE_IDENTITY'),
    ("UPDATE point_values SET value_k=999 WHERE rowid=1", 'TEMPERATURE'),
    ("UPDATE point_values SET nearest_lat=0 WHERE rowid=1", 'DISTANCE'),
    ("UPDATE station_days SET split='TRAIN' WHERE split='DEVELOPMENT'", 'DAY_IDENTITY'),
    ("UPDATE station_provider_expectations SET required_hours_json='[0,6]' WHERE rowid=1", 'HOUR_POLICY'),
    ("INSERT INTO point_values SELECT message_key,'orphan',provider,member,hour,value_k,distance_km,nearest_lat,nearest_lon FROM point_values WHERE rowid=1", 'ORPHAN'),
    ("INSERT INTO messages SELECT 'extra',provider,run_date,cycle,hour,member,status,attempts,public_url,index_sha256,index_byte_length,grib_sha256,byte_length,range_start,observed_header_sha256,grid_sha256,error,updated_at FROM messages WHERE rowid=1", 'UNREFERENCED'),
])
def test_integration_corrupted_store_fails_closed(tmp_path, sql, reason):
    args = fixture_databases(tmp_path)
    try:
        args[3].execute(sql)
        with pytest.raises(panel.PanelError, match=reason):
            panel.assemble(*args)
    finally:
        args[2].close(); args[3].close()


@pytest.mark.parametrize('mutation,reason', [
    ('partition', 'PARTITION'), ('winner', 'PAYOUT'), ('duplicate', 'COHORT'),
    ('source', 'SOURCE_STATION'), ('city', 'CITY_IDENTITY'), ('metadata', 'METADATA_IDENTITY'),
])
def test_catalog_plan_integrity(tmp_path, mutation, reason):
    args = fixture_databases(tmp_path)
    try:
        event = args[1]['events'][0]
        if mutation == 'partition': event['buckets'][1]['lower'] = 12
        if mutation == 'winner': event['buckets'][0]['yes_payout'] = 0
        if mutation == 'duplicate': args[1]['events'].append(copy.deepcopy(event))
        if mutation == 'source': event['source_urls'] = ['https://www.weather.gov/wrh/timeseries?site=klga']
        if mutation == 'city': event['slug'] = 'unknown'
        if mutation == 'metadata': args[0]['stations']['KATL']['latitude'] = 1
        with pytest.raises(panel.PanelError, match=reason): panel.assemble(*args)
    finally:
        args[2].close(); args[3].close()


def examples():
    rows = []
    for i, (split, day) in enumerate(zip(panel.SPLITS, ('2026-08-23','2026-09-16','2026-09-24'))):
        for j, family in enumerate(panel.FAMILIES):
            for station in ('KATL','KLGA'):
                rows.append(stack.EvaluationExample(
                    f'{i}_{j}_{station}', station+'|'+day, station, day, family, split,
                    {'GEFS': (.6,.4), 'IFS': (.8,.2), 'AIFS': (.7,.3)},
                    0, i*10+1., i*10., i*10+2., 'EXACT_LOCAL_DAY_EXTREME', 'SYNTHETIC'))
    return rows


def test_group_pool_and_member_replication_invariant():
    p = stack.member_probabilities([10,12,14], [11,13])
    replicated = stack.member_probabilities([10,12,14]*10, [11,13])
    assert replicated == pytest.approx(p, abs=1e-15)
    ps = dict(GEFS=p, IFS=(.2,.3,.5), AIFS=(.2,.3,.5))
    for share in (0,.25,.5,.75,1):
        assert stack.pool(ps, .5, share) == pytest.approx(stack.pool(ps, .5, 0))


def test_randomized_pool_coherence_and_no_sharpening():
    rng = random.Random(13)
    for _ in range(100):
        ps = {}
        for provider in panel.PROVIDERS:
            v = [rng.random() for _ in range(11)]
            ps[provider] = tuple(x/sum(v) for x in v)
        pooled = stack.pool(ps, rng.random(), rng.random())
        assert sum(pooled) == pytest.approx(1)
        for i, p in enumerate(pooled):
            assert min(v[i] for v in ps.values())-1e-15 <= p <= max(v[i] for v in ps.values())+1e-15


def test_confirmation_label_changes_do_not_change_frozen_selection():
    rows = examples()
    a = stack.evaluate(rows, fit_cutoff=30)
    changed = [replace(r, winner=1, probabilities={p:(.1,.9) for p in panel.PROVIDERS})
               if r.split == 'HISTORICAL_CONFIRMATION' else r for r in rows]
    b = stack.evaluate(changed, fit_cutoff=30)
    assert a['training_status'] == 'FITTED_NOT_CALIBRATED'
    assert a['promotion_status'] == 'NO_PROMOTION'
    assert a['evidence_class'] == 'SYNTHETIC'
    for f in panel.FAMILIES:
        assert a['families'][f]['frozen'] == b['families'][f]['frozen']
        assert len(a['families'][f]['frozen']['parameters']['attempts']) == 8
    assert panel.canonical(a) == panel.canonical(stack.evaluate(rows, fit_cutoff=30))


@pytest.mark.parametrize('change,reason', [
    ({'feature_available_at': 2.}, 'CAUSAL'), ({'label_knowable_at': 31.}, 'CAUSAL'),
    ({'label_knowable_at': 12.}, 'HISTORICAL_FIT'),
    ({'evidence_class': 'HISTORICAL_PUBLIC_RETRIEVAL'}, 'ADAPTER'),
    ({'feature_semantics': 'SAMPLED_EXTREME'}, 'EXACT_DAY'),
    ({'probabilities': {'GEFS':(.5,.5)}}, 'PROVIDER'),
    ({'winner': 2}, 'WINNER'),
])
def test_evaluator_causal_and_schema_gate(change, reason):
    rows = examples()
    rows[0] = replace(rows[0], **change)
    with pytest.raises(panel.PanelError, match=reason):
        stack.evaluate(rows, fit_cutoff=30)


def test_city_day_and_temporal_leakage():
    rows = examples()
    rows[4] = replace(rows[4], city_day=rows[0].city_day)
    with pytest.raises(panel.PanelError, match='CITY_DAY_SPLIT'):
        stack.evaluate(rows, fit_cutoff=30)
    rows = examples()
    rows[0] = replace(rows[0], local_date='2026-09-25', city_day='KATL|2026-09-25')
    with pytest.raises(panel.PanelError, match='TEMPORAL'):
        stack.evaluate(rows, fit_cutoff=30)
    with pytest.raises(panel.PanelError, match='SELECTION_PARTITIONS'):
        stack.freeze_selection(rows[:4], rows[8:])


def test_city_day_alias_and_duplicate_targets_refused():
    rows = examples()
    rows[2] = replace(rows[2], city_day='different-city-same-station-day')
    with pytest.raises(panel.PanelError, match='STATION_DAY_CITY_ALIAS'):
        stack.evaluate(rows, fit_cutoff=30)
    rows = examples()
    rows.append(replace(rows[0], event_id='new-id-same-target'))
    with pytest.raises(panel.PanelError, match='DUPLICATE_STATION_DAY_FAMILY'):
        stack.evaluate(rows, fit_cutoff=30)


def test_metric_counts_and_infinite_loss_are_honest():
    rows = examples()[:4]
    ps = [(1.,0.)]*4
    m = stack.metrics(rows, ps)
    assert (m['events'], m['city_days'], m['date_blocks']) == (4,2,1)
    assert m['effective_independent_sample_count'] is None
    assert m['brier'] == m['log_loss'] == m['calibration_error'] == 0
    bad = stack.metrics([replace(rows[0], winner=1)], [(1.,0.)])
    assert bad['brier'] == 2 and bad['infinite_log_loss'] and bad['log_loss'] is None


@pytest.mark.skipif(os.environ.get('ALPHA_R09_REAL_INPUTS') != '1', reason='explicit immutable local input integration')
def test_real_inputs_reproduce_all_outputs():
    root = panel.ROOT/'private-evidence/r09-multimodel'
    commit = os.environ['ALPHA_R09_CODE_COMMIT']
    pins = panel.ROOT/'config/v11/r09_multimodel_input_pins.json'
    a = panel.run(pins, root/'repeat-a', commit)
    b = panel.run(pins, root/'repeat-b', commit)
    assert a == b
    for name in ('panel.json.gz','manifest.json','result.json'):
        assert (root/'repeat-a'/name).read_bytes() == (root/'repeat-b'/name).read_bytes()
    result = json.loads((root/'repeat-a/result.json').read_bytes())
    assert [result['split_counts'][s]['city_days'] for s in panel.SPLITS] == [357,120,64]
    assert sum(r['native_both_boundaries'] for r in result['station_coverage'].values()) == 36
    assert result['training_status'] == 'NOT_FITTED'
