#!/usr/bin/env python3
from __future__ import annotations
import argparse,importlib.util,json,os,re,site,subprocess,sys
from pathlib import Path
FINAL_MODULE='polymarket_scanner.weather_only_live_paper_all_signals_final_v10'
FORBIDDEN=("HTTP_PROXY","HTTPS_PROXY","ALL_PROXY","NO_PROXY","http_proxy","https_proxy","all_proxy","no_proxy","SSL_CERT_FILE","SSL_CERT_DIR","PYTHONPATH","PYTHONHOME","PYTHONUSERBASE","PYTHONSTARTUP","PYTHONINSPECT","PYTHONWARNINGS","PYTHONBREAKPOINT","LD_PRELOAD","LD_LIBRARY_PATH")
AUTH='/usr/local/libexec/polymarket-weather-paper-v2/authority.py'
def load_base():
 path=Path(__file__).with_name('attest-all-paper-runtime.py'); spec=importlib.util.spec_from_file_location('_base',path); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m
def proc_env(pid):
 out={}
 for item in Path(f'/proc/{pid}/environ').read_bytes().split(b'\0'):
  if b'=' in item:
   k,v=item.split(b'=',1); out[k.decode()]=v.decode()
 return out
def main():
 p=argparse.ArgumentParser(); p.add_argument('--app-dir',type=Path,required=True); p.add_argument('--release-file',type=Path,required=True); p.add_argument('--db',type=Path,required=True); p.add_argument('--status',type=Path); p.add_argument('--unit',default='polymarket-weather-paper.service'); p.add_argument('--require-active',action='store_true'); p.add_argument('--output',type=Path); p.add_argument('--expected-release-sha',required=True); p.add_argument('--generation-id',required=True); a=p.parse_args()
 base=load_base(); base.FINAL_ALL_PAPER_MODULE=FINAL_MODULE; base.KNOWN_WEATHER_WRITER_MARKERS=tuple(dict.fromkeys((*base.KNOWN_WEATHER_WRITER_MARKERS,'weather_only_live_paper_all_signals_final_v10.py')))
 facts=base.collect_facts(unit_name=a.unit,app_dir=a.app_dir,release_file=a.release_file,db_path=a.db)
 sha=a.expected_release_sha; py=(a.app_dir.resolve()/'.releases'/sha/'venv/bin/python').resolve(); argv0_expected=str(a.app_dir.resolve()/'.releases'/sha/'venv/bin/python')
 unit=facts.unit_text; required=(f'ExecStart={argv0_expected} -E -s -m {FINAL_MODULE}', 'Environment=PYTHONNOUSERSITE=1','Environment=PYTHONDONTWRITEBYTECODE=1')
 for row in required:
  if row not in unit: raise RuntimeError('ALL_PAPER_UNIT_RUNTIME_IDENTITY_MISMATCH:'+row)
 unset=set()
 for line in unit.splitlines():
  if line.startswith('UnsetEnvironment='): unset.update(line.split('=',1)[1].split())
 missing=set(FORBIDDEN)-unset
 if missing: raise RuntimeError('ALL_PAPER_UNIT_FORBIDDEN_ENV_UNSET_INCOMPLETE:'+','.join(sorted(missing)))
 subprocess.run(['/usr/bin/python3',AUTH,'verify-candidate-environment','--generation-id',a.generation_id,'--app-dir',str(a.app_dir),'--candidate-sha',sha,'--environment-manifest',str(a.app_dir/'.releases'/sha/'environment-manifest.json')],check=True,env={'PATH':'/usr/bin:/bin','HOME':'/nonexistent'})
 if a.require_active and not facts.active: raise RuntimeError('ALL_PAPER_ACTIVE_REQUIRED')
 if facts.active:
  pid=int(facts.main_pid); env=proc_env(pid); leaked=[x for x in FORBIDDEN if x in env]
  if leaked: raise RuntimeError('ALL_PAPER_PROCESS_FORBIDDEN_ENV:'+','.join(sorted(leaked)))
  if env.get('PYTHONNOUSERSITE')!='1': raise RuntimeError('ALL_PAPER_PROCESS_USER_SITE_FLAG_MISSING')
  argv=list(facts.process_argv)
  if not argv or argv[0]!=argv0_expected or '-E' not in argv or '-s' not in argv or FINAL_MODULE not in argv: raise RuntimeError('ALL_PAPER_PROCESS_ARGV_IDENTITY_MISMATCH')
  if Path(f'/proc/{pid}/exe').resolve()!=py: raise RuntimeError('ALL_PAPER_PROCESS_EXECUTABLE_MISMATCH')
 status={}
 if a.status and a.status.exists(): status=json.loads(a.status.read_text())
 if status:
  if status.get('runtime_python_executable')!=argv0_expected: raise RuntimeError('ALL_PAPER_STATUS_PYTHON_EXECUTABLE_MISMATCH')
  if status.get('python_user_site_disabled') is not True: raise RuntimeError('ALL_PAPER_STATUS_USER_SITE_NOT_DISABLED')
  allowed=[str(a.app_dir.resolve()),str((a.app_dir.resolve()/'.releases'/sha/'venv').resolve()),'/usr/lib','/usr/local/lib']
  for item in status.get('runtime_sys_path') or []:
   if not item: continue
   rp=str(Path(item).resolve())
   if not any(rp==root or rp.startswith(root.rstrip('/')+'/') for root in allowed): raise RuntimeError('ALL_PAPER_SYS_PATH_UNAPPROVED:'+rp)
 payload={'acceptance':'PASS_ALL_PAPER_RUNTIME_V10_ATTESTATION','release_sha':sha,'generation_id':a.generation_id,'final_module':FINAL_MODULE,'active':facts.active,'forbidden_environment_absent':True,'candidate_release_venv':argv0_expected,'financial_authority':False,'automatic_order_placement':False,'wallet_or_order_api_loaded':False}
 if a.output: a.output.parent.mkdir(parents=True,exist_ok=True); a.output.write_text(json.dumps(payload,sort_keys=True,indent=2)+'\n')
 print(json.dumps(payload,sort_keys=True,indent=2)); return 0
if __name__=='__main__': raise SystemExit(main())
