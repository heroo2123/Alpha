import copy, json, runpy, sys, tempfile
from pathlib import Path
from pytest import MonkeyPatch
sys.path.insert(0, '/tmp/alpha-v11-gate3-v4-provider-mapping-20261001')
ns = runpy.run_path('/tmp/alpha-v11-gate3-v4-provider-mapping-20261001/tests/test_v11_r09_gate3_launch_v4.py')

def apply_spec(p, provider, purpose, spec):
    p['sources'][provider]['purpose_mappings'][purpose]['path_spec'] = spec
    if purpose == 'FIELD':
        p['sources'][provider]['path_spec'] = spec
        p['network']['path_specs'][provider] = spec

def scramble(p):
    old = p['sources']['GEFS']['path_spec']
    spec = [ns['_lit']('/wrong/')]
    for c in reversed(old):
        if c['kind'] != 'LITERAL':
            spec += [copy.deepcopy(c), ns['_lit']('/')]
    apply_spec(p, 'GEFS', 'FIELD', spec[:-1])

def cross_model(p):
    for purpose in ('FIELD', 'INDEX'):
        spec = copy.deepcopy(p['sources']['IFS']['purpose_mappings'][purpose]['path_spec'])
        spec[4]['value'] = '/aifs-ens/0p25/'
        apply_spec(p, 'IFS', purpose, spec)

def wrong_index(p):
    spec = copy.deepcopy(p['sources']['IFS']['purpose_mappings']['INDEX']['path_spec'])
    spec[-1]['value'] = '.grib2'
    apply_spec(p, 'IFS', 'INDEX', spec)

def arbitrary_purposes(p):
    for source in p['sources'].values():
        for purpose in ('OBJECT_ID', 'METADATA', 'PROBE'):
            source['purpose_mappings'][purpose]['path_spec'] = [ns['_lit']('/unreviewed/' + purpose.lower())]

    p['schedule']['requests'].insert(3, ns['request_for_slot'](p, 0, 'PROBE', 3, 4096, []))
    p['schedule']['reservation_total_bytes'] = sum(r['reservation_bytes'] for r in p['schedule']['requests'])
    p['runtime']['resource_bounds']['required_store_objects'] = 11

def guessed_s3(p):
    source = p['sources']['GEFS']
    source['origin'] = 'https://noaa-gefs-pds.s3.amazonaws.com'
    for mapping in source['purpose_mappings'].values():
        mapping['origin'] = source['origin']

results=[]
for name, mutate in [('baseline',lambda p: None), ('scrambled_gefs_layout',scramble), ('ifs_uses_aifs_model_directory',cross_model), ('ifs_index_is_grib_object',wrong_index), ('unevidenced_object_metadata_probe',arbitrary_purposes), ('guessed_gefs_s3',guessed_s3)]:
    with tempfile.TemporaryDirectory(prefix='/tmp/alpha-v11-mapping-review-15f054f.fixture-') as tmp, MonkeyPatch.context() as mp:
        p, repo, root, start = ns['candidate'](Path(tmp), mp)
        mutate(p)
        if name.startswith('ifs_'):
            slot = next(i for i, row in enumerate(p['runs_and_slots']['slots']) if row[0] == 'IFS' and row[2] == 1)
            p['schedule']['attempt_slots'] = [slot]
            p['schedule']['requests'] = [
                ns['request_for_slot'](p, slot, purpose, pos,
                    3145728 if purpose == 'INDEX' else 4194304 if purpose == 'FIELD' else 4096,
                    [0,1,2] if purpose == 'FIELD' else [])
                for pos, purpose in enumerate(('INDEX','OBJECT_ID','METADATA','FIELD'))]
            p['schedule']['reservation_total_bytes'] = sum(r['reservation_bytes'] for r in p['schedule']['requests'])
        ns['_rebind_endpoints_and_requests'](p)
        try:
            digest = ns['validate'](p,repo,root,start)
            result = 'ACCEPTED'
        except Exception as exc:
            result = type(exc).__name__ + ': ' + str(exc)
        results.append({'case':name,'result':result,'field_path':p['schedule']['requests'][-1]['path']})
output = json.dumps(results,indent=2)
Path('/tmp/alpha-v11-mapping-review-15f054f.results.json').write_text(output + '\n')
print(output)
