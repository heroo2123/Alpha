#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, importlib.util, json, os, re, shutil, subprocess, sys
from pathlib import Path

VERSION="weather-paper-release-environment-v2-clean-hashlocked"
BOOTSTRAP_DISTS={"pip"}
DENIED={"py-clob-client","web3","eth-account","eth-keys","coincurve","brownie","ape","web3auth"}

def norm(s:str)->str: return re.sub(r"[-_.]+","-",s).lower()
def sha(path:Path)->str:
 h=hashlib.sha256();
 with path.open('rb') as f:
  for c in iter(lambda:f.read(1024*1024),b''): h.update(c)
 return h.hexdigest()
def tree_rows(root:Path)->list[dict]:
 import stat
 base=root.parent; out=[]; todo=[root]
 while todo:
  p=todo.pop(); st=p.lstat(); rel=p.relative_to(base).as_posix(); mode=stat.S_IMODE(st.st_mode)
  if stat.S_ISDIR(st.st_mode): out.append({'path':rel,'kind':'dir','mode':mode}); todo.extend(sorted(p.iterdir(),reverse=True))
  elif stat.S_ISREG(st.st_mode): out.append({'path':rel,'kind':'file','mode':mode,'size':st.st_size,'sha256':sha(p)})
  elif stat.S_ISLNK(st.st_mode): out.append({'path':rel,'kind':'symlink','mode':mode,'target':os.readlink(p)})
  else: raise SystemExit('RELEASE_ENV_UNSUPPORTED_FILE_TYPE')
 return sorted(out,key=lambda x:x['path'])
def tree_digest(root:Path)->str:
 raw=(json.dumps(tree_rows(root),sort_keys=True,separators=(',',':'))+'\n').encode(); return hashlib.sha256(raw).hexdigest()
def lock_names(path:Path)->set[str]:
 out=set()
 for raw in path.read_text().splitlines():
  row=raw.split('#',1)[0].strip()
  if not row: continue
  m=re.match(r'^([A-Za-z0-9_.-]+)==',row)
  if not m: raise SystemExit('RELEASE_ENV_LOCK_NOT_EXACT')
  if '--hash=sha256:' not in row: raise SystemExit('RELEASE_ENV_LOCK_HASH_MISSING')
  out.add(norm(m.group(1)))
 return out
def run(args:list[str],env=None)->str:
 e={'PATH':'/usr/bin:/bin','HOME':'/nonexistent','LANG':'C.UTF-8','PYTHONNOUSERSITE':'1','PYTHONDONTWRITEBYTECODE':'1'}
 if env: e.update(env)
 p=subprocess.run(args,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=e,check=False)
 if p.returncode: raise SystemExit('RELEASE_ENV_COMMAND_FAILED:'+p.stderr[-1000:])
 return p.stdout
def inventory(py:Path)->list[dict]:
 code="import json,importlib.metadata as m; print(json.dumps(sorted([{'name':d.metadata.get('Name',''),'version':d.version} for d in m.distributions()], key=lambda x:(x['name'].lower(),x['version']))))"
 return json.loads(run([str(py),'-I','-s','-E','-c',code]))
def validate_inventory(rows:list[dict],lock:Path)->None:
 names={norm(str(x.get('name') or '')) for x in rows}; expected=lock_names(lock)
 extra=names-expected-{norm(x) for x in BOOTSTRAP_DISTS}; missing=expected-names
 if extra: raise SystemExit('RELEASE_ENV_UNEXPECTED_DISTRIBUTIONS:'+','.join(sorted(extra)))
 if missing: raise SystemExit('RELEASE_ENV_MISSING_DISTRIBUTIONS:'+','.join(sorted(missing)))
 denied=names & {norm(x) for x in DENIED}
 if denied: raise SystemExit('RELEASE_ENV_FINANCIAL_DISTRIBUTION:'+','.join(sorted(denied)))
def build(args)->dict:
 sha40=args.release_sha.lower()
 if not re.fullmatch(r'[0-9a-f]{40}',sha40): raise SystemExit('RELEASE_ENV_SHA_INVALID')
 root=(args.app_dir.resolve()/'.releases'/sha40); venv=root/'venv'
 if root.exists(): raise SystemExit('RELEASE_ENV_ALREADY_EXISTS')
 root.mkdir(parents=True,mode=0o700)
 run([str(args.python),'-m','venv',str(venv)])
 cfg=(venv/'pyvenv.cfg').read_text().lower()
 if 'include-system-site-packages = false' not in cfg: raise SystemExit('RELEASE_ENV_SYSTEM_SITE_ENABLED')
 py=venv/'bin/python'
 run([str(py),'-m','pip','install','--disable-pip-version-check','--require-hashes','-r',str(args.lock.resolve())])
 run([str(py),'-m','pip','check'])
 rows=inventory(py); validate_inventory(rows,args.lock)
 manifest={'version':VERSION,'release_sha':sha40,'venv_path':str(venv.resolve()),'lock_path':str(args.lock.resolve()),'lock_sha256':sha(args.lock.resolve()),'distributions':rows,'user_site_enabled':False,'system_site_packages':False,'python_flags':['-E','-s'],'venv_tree_sha256':tree_digest(venv)}
 out=root/'environment-manifest.json'; out.write_text(json.dumps(manifest,sort_keys=True,indent=2)+'\n'); os.chmod(out,0o600)
 verify_one(out); return manifest
def verify_one(path:Path)->dict:
 m=json.loads(path.read_text()); venv=Path(m['venv_path'])
 if m.get('version')!=VERSION or not venv.is_dir() or venv.is_symlink(): raise SystemExit('RELEASE_ENV_MANIFEST_INVALID')
 if tree_digest(venv)!=m.get('venv_tree_sha256'): raise SystemExit('RELEASE_ENV_TREE_MISMATCH')
 rows=inventory(venv/'bin/python')
 if rows!=m.get('distributions'): raise SystemExit('RELEASE_ENV_INVENTORY_MISMATCH')
 validate_inventory(rows,Path(m['lock_path']))
 if sha(Path(m['lock_path']))!=m.get('lock_sha256'): raise SystemExit('RELEASE_ENV_LOCK_DIGEST_MISMATCH')
 cfg=(venv/'pyvenv.cfg').read_text().lower()
 if 'include-system-site-packages = false' not in cfg: raise SystemExit('RELEASE_ENV_SYSTEM_SITE_ENABLED')
 return m
def main():
 p=argparse.ArgumentParser(); s=p.add_subparsers(dest='cmd',required=True)
 b=s.add_parser('build'); b.add_argument('--app-dir',type=Path,required=True); b.add_argument('--release-sha',required=True); b.add_argument('--lock',type=Path,required=True); b.add_argument('--python',type=Path,default=Path(sys.executable))
 v=s.add_parser('verify'); v.add_argument('--manifest',type=Path,required=True)
 a=p.parse_args(); result=build(a) if a.cmd=='build' else verify_one(a.manifest); print(json.dumps({'acceptance':'PASS_RELEASE_ENV_'+a.cmd.upper(),'release_sha':result['release_sha'],'venv_tree_sha256':result['venv_tree_sha256'],'distribution_count':len(result['distributions'])},sort_keys=True))
if __name__=='__main__': main()
