from __future__ import annotations
import importlib.util,json,os,sqlite3,subprocess,sys,tempfile
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1]
def load(path,name):
 spec=importlib.util.spec_from_file_location(name,path); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m

def test_candidate_can_no_longer_install_or_replace_host_authority():
 for name in ('install-weather-paper-host-trust.sh','weather-paper-host-snapshot.sh','weather-paper-host-recovery.sh'):
  text=(ROOT/'deploy'/name).read_text(); assert 'REFUSED:' in text
 renderer=(ROOT/'deploy'/'render-all-paper-unit.py').read_text(); assert '/usr/local/libexec/polymarket-weather-paper-v2/authority.py' in renderer; assert 'deploy/weather-paper-host-release-gate.py' not in renderer

def test_authority_digest_anchor_rejects_modified_candidate_copy(tmp_path):
 src=ROOT/'host_trust/weather-paper-authority-v2/authority.py'; copy=tmp_path/'authority.py'; copy.write_bytes(src.read_bytes()); mod=load(copy,'auth_ok'); digest=mod.sha256_file(copy); anchor=tmp_path/'anchor.json'; anchor.write_text(json.dumps({'version':mod.AUTHORITY_VERSION,'authority_sha256':digest})); assert mod.verify_self(anchor,require_root=False)['authority_sha256']==digest
 copy.write_text(copy.read_text()+'\n# candidate mutation\n'); mod2=load(copy,'auth_bad')
 with pytest.raises(mod2.AuthorityError,match='AUTHORITY_SELF_DIGEST_MISMATCH'): mod2.verify_self(anchor,require_root=False)

def test_generation_manifest_binds_predecessor_candidate_and_rejects_wrong_active(tmp_path):
 mod=load(ROOT/'host_trust/weather-paper-authority-v2/authority.py','auth_gen'); root=tmp_path/'generations'; root.mkdir(); gid='1'*64; gd=root/gid; gd.mkdir();
 for n in ('predecessor-unit.service','predecessor-venv.tar','predecessor-venv-manifest.json'): (gd/n).write_text(n)
 data={'version':'weather-paper-cutover-v2','generation_id':gid,'predecessor_sha':'a'*40,'candidate_sha':'b'*40,'predecessor_unit_sha256':mod.sha256_file(gd/'predecessor-unit.service'),'predecessor_venv_archive_sha256':mod.sha256_file(gd/'predecessor-venv.tar'),'predecessor_venv_manifest_sha256':mod.sha256_file(gd/'predecessor-venv-manifest.json'),'predecessor_db_present':False}
 (gd/'manifest.json').write_text(json.dumps(data)); active=tmp_path/'active.json'; active.write_text(json.dumps({'generation_id':'2'*64}))
 with pytest.raises(mod.AuthorityError,match='CUTOVER_ACTIVE_GENERATION_MISMATCH'): mod.load_generation(gid,root,active,require_root=False)

def test_renderer_uses_release_venv_v10_and_scrubs_loader_python_environment():
 m=load(ROOT/'deploy/render-all-paper-unit.py','render_v10'); text=m.render(Path('/home/test/app'),Path('/home/test/config'),'tester','a'*40,'b'*64)
 assert '/.releases/'+'a'*40+'/venv/bin/python -E -s -m polymarket_scanner.weather_only_live_paper_all_signals_final_v10' in text
 for key in ('PYTHONPATH','PYTHONHOME','PYTHONUSERBASE','LD_PRELOAD','LD_LIBRARY_PATH'): assert key in text.split('UnsetEnvironment=',1)[1].split('\n',1)[0]
 assert 'Environment=PYTHONNOUSERSITE=1' in text

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

def test_prepare_and_start_require_explicit_generation_and_no_active_venv_mutation():
 prep=(ROOT/'deploy/prepare-all-paper-candidate-v4.sh').read_text(); start=(ROOT/'deploy/start-all-paper-candidate-v2.sh').read_text(); assert '.releases/${RELEASE_SHA}/environment-manifest.json' in prep; assert '"${APP_DIR}/.venv/bin/python" -m pip install' not in prep; assert 'GENERATION_ID' in prep; assert 'GEN=' in start; assert 'snapshot-generation-v4' not in prep+start

def test_host_authority_source_contains_resnapshot_and_wrong_generation_guards():
 text=(ROOT/'host_trust/weather-paper-authority-v2/authority.py').read_text(); assert 'CUTOVER_ALREADY_ACTIVE' in text; assert 'CUTOVER_CANDIDATE_ALREADY_CURRENT' in text; assert 'CUTOVER_ACTIVE_GENERATION_MISMATCH' in text; assert 'CUTOVER_PREDECESSOR_EQUALS_CANDIDATE' in text
