from __future__ import annotations
import importlib.util,json,os,sqlite3,subprocess,sys,tempfile
from pathlib import Path
import pytest
from test_host_authority_production_boundary import host, prepare
ROOT=Path(__file__).resolve().parents[1]
def load(path,name):
 spec=importlib.util.spec_from_file_location(name,path); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m

def test_candidate_can_no_longer_install_or_replace_host_authority():
 for name in ('install-weather-paper-host-trust.sh','weather-paper-host-snapshot.sh','weather-paper-host-recovery.sh'):
  result=subprocess.run(['/bin/bash',str(ROOT/'deploy'/name)],capture_output=True,text=True,check=False)
  assert result.returncode==40 and 'REFUSED:' in result.stderr
 client=(ROOT/'deploy/production-host-control.sh').read_text()
 assert '/usr/local/libexec/polymarket-weather-paper-v3/authority.py' in client
 assert '--authority-source' not in client and 'install -' not in client

def test_archived_v2_digest_fixture_detects_modification_without_proving_provenance(tmp_path):
 src=ROOT/'host_trust/weather-paper-authority-v2/authority.py'; copy=tmp_path/'authority.py'; copy.write_bytes(src.read_bytes()); mod=load(copy,'auth_ok'); digest=mod.sha256_file(copy); anchor=tmp_path/'anchor.json'; anchor.write_text(json.dumps({'version':mod.AUTHORITY_VERSION,'authority_sha256':digest})); assert mod.verify_self(anchor,require_root=False)['authority_sha256']==digest
 copy.write_text(copy.read_text()+'\n# candidate mutation\n'); mod2=load(copy,'auth_bad')
 with pytest.raises(mod2.AuthorityError,match='AUTHORITY_SELF_DIGEST_MISMATCH'): mod2.verify_self(anchor,require_root=False)

def test_generation_manifest_binds_predecessor_candidate_and_rejects_wrong_active(tmp_path):
 mod=load(ROOT/'host_trust/weather-paper-authority-v2/authority.py','auth_gen'); root=tmp_path/'generations'; root.mkdir(); gid='1'*64; gd=root/gid; gd.mkdir();
 for n in ('predecessor-unit.service','predecessor-venv.tar','predecessor-venv-manifest.json'): (gd/n).write_text(n)
 data={'version':'weather-paper-cutover-v2','generation_id':gid,'predecessor_sha':'a'*40,'candidate_sha':'b'*40,'predecessor_unit_sha256':mod.sha256_file(gd/'predecessor-unit.service'),'predecessor_venv_archive_sha256':mod.sha256_file(gd/'predecessor-venv.tar'),'predecessor_venv_manifest_sha256':mod.sha256_file(gd/'predecessor-venv-manifest.json'),'predecessor_db_present':False}
 (gd/'manifest.json').write_text(json.dumps(data)); active=tmp_path/'active.json'; active.write_text(json.dumps({'generation_id':'2'*64}))
 with pytest.raises(mod.AuthorityError,match='CUTOVER_ACTIVE_GENERATION_MISMATCH'): mod.load_generation(gid,root,active,require_root=False)

def test_renderer_uses_isolated_production_components_without_environment_files(host):
 gid,manifest=prepare(host)
 for name in ('signals','execution'):
  text=host.m._render_unit(host.policy,gid,host.b,manifest,name)
  assert f'-m polymarket_scanner.production {name} --config ' in text
  assert 'ExecStart=/usr/bin/env -i ' in text and '-I -s -E -B -m' in text
  assert 'EnvironmentFile=' not in text and '--paper-stake' not in text

def test_v8_guard_rejects_python_and_native_loader_injection(monkeypatch):
 from polymarket_scanner.weather_only_live_paper_all_signals_final_v8 import assert_network_environment_isolated
 monkeypatch.setenv('PYTHONPATH','/tmp/evil')
 with pytest.raises(RuntimeError,match='PYTHONPATH'): assert_network_environment_isolated()
 monkeypatch.delenv('PYTHONPATH'); monkeypatch.setenv('LD_PRELOAD','/tmp/evil.so')
 with pytest.raises(RuntimeError,match='LD_PRELOAD'): assert_network_environment_isolated()

def test_semantic_census_distinguishes_complete_gamma_from_incomplete_semantics():
 from polymarket_scanner.weather_only_discovery import WeatherOnlyDiscovery
 unknown={'id':'x','title':'Highest temperature in Unknownville on September 16?','markets':[]}
 category,detail=WeatherOnlyDiscovery._semantic_classification(unknown); assert category!='SUPPORTED'
 # Product policy is explicitly strict subset: unknown contracts remain excluded, not promoted.
 assert category in {'UNSUPPORTED_STATION','UNSUPPORTED_RULE_GRAMMAR','UNSUPPORTED_UNIT','UNSUPPORTED_FAMILY','UNSUPPORTED_BUCKET_FORM','AMBIGUOUS','OTHER_FAIL_CLOSED'}

def test_requirements_have_no_financial_signing_packages():
 text=(ROOT/'requirements-runtime-hashed.txt').read_text().lower(); denied=('py-clob-client','web3==','eth-account','eth_keys','eth-keys','coincurve')
 assert not any(x in text for x in denied)

def test_prepare_requires_generation_and_never_mutates_predecessor_environment(host):
 before=host.m._tree_digest(host.app/'.venv')
 gid,manifest=prepare(host)
 assert host.m._tree_digest(host.app/'.venv')==before
 assert manifest['generation_id']==gid
 assert all(str(host.app/'.venv')!=row['venv_path'] for row in manifest['components'].values())
 with pytest.raises(host.m.AuthorityError,match='CUTOVER_CANDIDATE_MISMATCH'):
  host.m.prepare_candidate(gid,host.a,require_root=False)

def test_host_authority_rejects_resnapshot_and_wrong_generation_behaviorally(host):
 gid=host.m.create_cutover(host.b,require_root=False)
 with pytest.raises(host.m.AuthorityError,match='CUTOVER_ALREADY_ACTIVE'):
  host.m.create_cutover(host.b,require_root=False)
 value=json.loads(host.m.ACTIVE.read_text()); value['generation_id']='f'*64
 host.m._safe_write(host.m.ACTIVE,host.m._canonical(value),root_custody=False)
 with pytest.raises(host.m.AuthorityError,match='CUTOVER_ACTIVE_GENERATION_MISMATCH'):
  host.m._load_generation(gid,host.policy,require_root=False)
