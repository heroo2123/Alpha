"""Independent bounded offline review. Writes only the requested /tmp terminal and own scratch."""
import sys
sys.dont_write_bytecode = True
from pathlib import Path
import contextlib, hashlib, io, json, os, resource, socket, subprocess, tempfile, time
from unittest.mock import patch
ROOT = Path('/tmp/alpha-v11-gate3-a7-review-313eeaf')
TERMINAL = Path('/tmp/alpha-v11-gate3-a7-review-313eeaf_terminal.json')
COMMIT = '313eeaffe9865ada9d0c28f102a8ed679c221323'
BASE = 'e08858b4f0e25d3b37f53a1779b16bc6ac5cc942'
sys.path[:0] = [str(ROOT), str(ROOT/'tests')]
os.chdir(ROOT)
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
original_connect = socket.socket.connect
def offline_connect(self, address):
    if self.family in (socket.AF_INET, socket.AF_INET6):
        raise RuntimeError('Review prohibits network')
    return original_connect(self, address)
socket.socket.connect = offline_connect

def sha(data): return hashlib.sha256(data).hexdigest()
def artifact(path):
    path = Path(path); data = path.read_bytes()
    return dict(path=str(path), bytes=len(data), sha256=sha(data))
def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT).decode().strip()
def save(key, value):
    data = json.loads(TERMINAL.read_text()) if TERMINAL.exists() else {}
    data[key] = value
    TERMINAL.write_text(json.dumps(data, indent=2, sort_keys=True)+'\n')
    print(json.dumps({key:value}, sort_keys=True))
def outcome(call):
    try: return dict(returned=call())
    except Exception as exc: return dict(exception=type(exc).__name__, message=str(exc))

def inventory():
    author_path = '/tmp/alpha-v11-gate3-a7-repair-e08858b.author.json'
    author = json.loads(Path(author_path).read_text())
    prior_path = '/tmp/alpha-v11-gate3-a7-review-e08858b_terminal.json'
    prior = json.loads(Path(prior_path).read_text())
    checks = []
    for item in list(prior['artifacts'].values()) + prior['inspected_inputs']:
        result = artifact(item['path'])
        result['expected_sha256'] = item['sha256']
        result['matches'] = result['sha256'] == item['sha256'] and result['bytes'] == item['bytes']
        checks.append(result)
    copied_report = artifact('/home/alphaadmin/AlphaV11_Dev/Alpha/docs/V11_R09_GATE3_A7_REVIEW_e08858b.md')
    copied_report['matches_prior_report'] = copied_report['sha256'] == prior['artifacts']['report']['sha256']
    log = artifact(author['tests']['log'])
    log['matches_author'] = log['sha256'] == author['tests']['log_sha256']
    log['text'] = Path(author['tests']['log']).read_text()
    files = git('diff','--name-only',BASE,COMMIT).splitlines()
    exact = []
    for file in files + ['docs/V11_R09_GATE3_IDENTITY_ACCEPTANCE_20261001.md', 'polymarket_scanner/v11/ecmwf_grib.py']:
        item = artifact(ROOT/file)
        blob = subprocess.check_output(['git','show',COMMIT+':'+file],cwd=ROOT)
        item.update(relative_path=file, git_blob=git('rev-parse',COMMIT+':'+file), matches_git_blob=sha(blob)==item['sha256'])
        exact.append(item)
    result = dict(commit=git('rev-parse','HEAD'),tree=git('rev-parse','HEAD^{tree}'),base=BASE,
        parent=git('rev-parse','HEAD^'),checkout=str(ROOT),status=git('status','--porcelain=v1','--untracked-files=all','--ignored'),
        detached=subprocess.run(['git','symbolic-ref','-q','HEAD'],cwd=ROOT,capture_output=True).returncode==1,
        diff_check=subprocess.run(['git','diff','--check',BASE,COMMIT],cwd=ROOT,capture_output=True,text=True).__dict__,
        exact_files=exact, author=artifact(author_path), prior_terminal=artifact(prior_path), prior_checks=checks,
        copied_prior_report=copied_report,author_test_log=log,changed_files_match_author=files==author['changed_files'],
        author_identity_matches=author['candidate_commit']==COMMIT and author['candidate_tree']==git('rev-parse','HEAD^{tree}'),
        local_reference_files=[artifact('/usr/share/man/man7/cgroup_namespaces.7.gz'),artifact('/usr/lib/python3.12/subprocess.py')])
    save('inventory',result)

def tests():
    import pytest
    log = io.StringIO()
    with tempfile.TemporaryDirectory(prefix='a7-review-313eeaf-tests-',dir='/tmp') as scratch:
        old=tempfile.tempdir; tempfile.tempdir=scratch
        try:
            args=['-q','-p','no:cacheprovider','--basetemp',scratch+'/pytest',
                'tests/test_v11_r09_gate3_a7_decoder.py','tests/test_v11_model_panel.py','tests/test_v11_r09_gate3_offline_io.py']
            with contextlib.redirect_stdout(log),contextlib.redirect_stderr(log):
                code=pytest.main(args)
            size=sum(p.stat().st_size for p in Path(scratch).rglob('*') if p.is_file())
        finally: tempfile.tempdir=old
    save('focused_adjacent_tests',dict(args=args,exit_code=int(code),output=log.getvalue(),output_sha256=sha(log.getvalue().encode()),
        scratch_file_bytes_at_completion=size,scratch_removed=True,python=sys.version,interpreter=sys.executable,
        modifications='No candidate edits. Bytecode and pytest cache disabled; candidate itself uses -B.'))

def cgroups():
    from tools import v11_r09_gate3_a7_decoder as a7
    mib=1024**2
    def hierarchy(member='/team/leaf',mountpoint='/sys/fs/cgroup',root='/'):
        f={'/proc/meminfo':'MemAvailable: 8388608 kB\n','/proc/self/cgroup':'0::'+member+'\n',
           '/proc/self/mountinfo':f'34 25 0:29 {root} {mountpoint} rw - cgroup2 cgroup2 rw\n'}
        mp=mountpoint.replace('\\040',' ')
        for d in ('','/team','/team/leaf'):
            f[mp+d+'/memory.max']='max\n'; f[mp+d+'/memory.current']='0\n'
        return f
    cases={}
    def check(name,files,expected=None,denied=None):
        reads=[]
        def read(p,*a,**kw):
            reads.append(str(p))
            if str(p)==denied: raise PermissionError(str(p))
            if str(p) not in files: raise FileNotFoundError(str(p))
            return files[str(p)]
        with patch.object(Path,'read_text',read): result=outcome(a7._available_memory)
        if expected is not None: assert result == expected, (name,result,expected)
        cases[name]=dict(result=result,reads=reads,files=files)
    for name,d in [('leaf','/team/leaf'),('ancestor','/team'),('root','')]:
        f=hierarchy(); f['/sys/fs/cgroup'+d+'/memory.max']=str(256*mib); f['/sys/fs/cgroup'+d+'/memory.current']=str(192*mib)
        check(name,f,{'returned':64*mib})
    f=hierarchy(); check('unlimited',f,{'returned':8*1024**3})
    del f['/sys/fs/cgroup/memory.max']; del f['/sys/fs/cgroup/memory.current']
    check('controllerless_global_root',f,{'returned':8*1024**3})
    refusal={'exception':'A7Refusal','message':'A7_HEADROOM_UNKNOWN'}
    for d in ('','/team','/team/leaf'):
        for control in ('memory.max','memory.current'):
            path='/sys/fs/cgroup'+d+'/'+control
            f=hierarchy(); del f[path]; check('missing:'+path,f,refusal)
            check('unreadable:'+path,hierarchy(),refusal,denied=path)
    for control in ('memory.max','memory.current'):
        for value in ('-1','garbage','', '1.5'):
            f=hierarchy(); f['/sys/fs/cgroup/team/leaf/'+control]=value
            check('malformed:'+control+':'+repr(value),f,refusal)
    for kind,value in [('no_membership',''),('duplicate_membership','0::/team/leaf\n0::/team\n'),('traversal','0::/../team\n')]:
        f=hierarchy();f['/proc/self/cgroup']=value;check(kind,f,refusal)
    f=hierarchy(root='/team');check('subtree_mount',f,refusal)
    f=hierarchy();f['/proc/self/mountinfo']*=2;check('ambiguous_mount',f,refusal)
    f=hierarchy(mountpoint='/sys/cgroup\\040test');check('escaped_mount_space',f,{'returned':8*1024**3})
    f=hierarchy();f['/sys/fs/cgroup/team/leaf/memory.max']='100';f['/sys/fs/cgroup/team/leaf/memory.current']='101'
    check('over_limit_clamped',f,{'returned':0})
    # Valid namespace-relative view: / is a namespace root, not global root.
    # Ancestor outside this namespace has only 64 MiB available (synthetic fact).
    f=hierarchy(member='/');check('hidden_namespace_ancestor',f,{'returned':8*1024**3})
    cases['hidden_namespace_ancestor'].update(modeled_hidden_ancestor_limit=256*mib,modeled_hidden_ancestor_current=192*mib,
        expected_effective_available=64*mib,default_required=640*mib,incorrectly_admits_default=True,
        scope='Synthetic namespace-relative procfs/controller view; no namespace or cgroup changed, no worker launched.')
    actual={'available':outcome(a7._available_memory),'membership':Path('/proc/self/cgroup').read_text(),
        'mounts':[line for line in Path('/proc/self/mountinfo').read_text().splitlines() if ' - cgroup2 ' in line],
        'memavailable':[line for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemAvailable:')]}
    save('cgroup_probes',dict(cases=cases,actual_host=actual))

def processes():
    from tools import v11_r09_gate3_a7_decoder as a7
    from test_v11_r09_gate3_a7_decoder import fixture,invoke
    raw,identity,pins=fixture(); out={}
    original_popen=subprocess.Popen; original_wait4=os.wait4
    # Force the legitimate exit-between-WNOHANG-and-kill interleaving without reaping.
    proc=original_popen([sys.executable,'-I','-B','-c','import time;time.sleep(.1)'])
    real_kill=proc.kill
    def exit_before_kill():
        time.sleep(.25)
        return real_kill()
    with patch.object(proc,'kill',exit_before_kill):
        out['timeout_exit_race']=outcome(lambda:a7._wait_with_usage(proc,0))
    out['timeout_exit_race']['returncode_after']=proc.returncode
    if proc.returncode is None: proc.kill();proc.wait()
    assert out['timeout_exit_race']['exception']=='ChildProcessError'
    # Exact child CPU, even while an unrelated child burns CPU and exits.
    other=original_popen([sys.executable,'-I','-B','-c','import time\ns=time.process_time()\nwhile time.process_time()-s<.15: pass'])
    child=original_popen([sys.executable,'-I','-B','-c','import time;time.sleep(.25)'])
    own=a7._wait_with_usage(child,2);other.wait()
    out['exact_child_accounting']=dict(sleeping_child_cpu=own,unrelated_child_returncode=other.returncode)
    # Preserve real worker decode but add bounded memory after its RSS sample,
    # at serialization. Intercept wait4 only to retain the kernel's actual rusage.
    usages=[]
    def record_wait4(*args):
        result=original_wait4(*args)
        if result[0]: usages.append(dict(pid=result[0],max_rss_bytes=result[2].ru_maxrss*1024,
            cpu_seconds=result[2].ru_utime+result[2].ru_stime))
        return result
    script=("import sys;sys.path.insert(0,sys.argv[1]);from tools import v11_r09_gate3_a7_decoder as a\n"
            "dump=a.json.dumps\n"
            "def after_sample(value,*args,**kwargs):\n"
            " if isinstance(value,dict) and 'max_rss_bytes' in value: a._review_allocation=bytearray(48*1024*1024)\n"
            " return dump(value,*args,**kwargs)\n"
            "a.json.dumps=after_sample\na.worker_main()\n")
    def rss_popen(command,**kwargs):
        return original_popen([sys.executable,'-I','-B','-c',script,str(ROOT)],**kwargs)
    with patch.object(a7,'_available_memory',lambda:8*1024**3),patch.object(a7.subprocess,'Popen',rss_popen),patch.object(a7.os,'wait4',record_wait4):
        result=invoke(raw,identity,pins)
    out['rss_after_sample']=dict(measurement=result,reaped_usage=usages,allocation_bytes=48*1024**2,
        injected='48 MiB allocation during real worker JSON serialization after its RSS sample; child still under default address-space limit')
    assert usages[-1]['max_rss_bytes']>result['max_rss_bytes']
    assert abs(usages[-1]['cpu_seconds']-result['cpu_seconds'])<1e-10
    accepted=a7.Bounds(cpu_seconds=60,wall_seconds=90,memory_bytes=2*1024**3,output_bytes=65536,memory_headroom_bytes=0,disk_headroom_bytes=0)
    accepted.validate();out['allowed_documented_extremes']=vars(accepted)
    save('process_probes',out)

def decoder():
    from datetime import datetime,timezone
    import eccodes,eccodeslib
    from tools import v11_r09_gate3_a7_decoder as a7
    from polymarket_scanner.v11 import ecmwf_grib as grib
    from test_v11_r09_gate3_a7_decoder import fixture,invoke,synthetic_ccsds
    from test_v11_model_panel import ecmwf_bytes
    from test_v11_grib_fields import mutate,u
    out={}
    raw=Path('/tmp/aifs_cf.grib2').read_bytes()
    request=dict(provider='ECMWF_AIFS_ENS',model='aifs-ens',model_version='UNVERIFIED_LOCAL_OBSERVATION',dataset='ecmwf-open-data:0p25',
        release_evidence_sha256='0'*64,initialized_at=datetime(2026,9,29,tzinfo=timezone.utc).timestamp(),step=6,member=0,grib_signature_sha256=grib.release_signature(raw))
    library=Path(eccodeslib.__file__).parent/'lib64'
    native={str(p):sha(p.read_bytes()) for p in (library/'libeccodes.so',library/'libeccodes_memfs.so')}
    pins=a7.Pins(sha(a7.DECODER.read_bytes()),sha(a7.source_bytes(request)),sha(raw),{n:sha(s) for n,s in grib.sections(raw).items() if n!=7},'RETAINED_UNVERIFIED',native)
    start=resource.getrusage(resource.RUSAGE_CHILDREN)
    measurement=outcome(lambda:a7.run(raw,request=request,pins=pins,latitude=33.64,longitude=-84.43))
    end=resource.getrusage(resource.RUSAGE_CHILDREN)
    total=end.ru_utime+end.ru_stime-start.ru_utime-start.ru_stime
    out['retained']=dict(measurement=measurement,total_child_cpu=total,request=request,raw_bytes=len(raw),raw_sha256=sha(raw),native=native,
        decoder_sha256=pins.decoder_sha256,python=sys.version,interpreter=sys.executable,eccodes=eccodes.codes_get_api_version(),
        scope='Diagnostic derived pins, RETAINED_UNVERIFIED; actual memory admission used, no qualification')
    if 'returned' in measurement: assert abs(measurement['returned']['cpu_seconds']-total)<1e-6
    out['simple_ieee']={}
    for packing in (0,1,2):
        raw,identity,pins=fixture(ecmwf_bytes(packing=packing))
        out['simple_ieee'][packing]=outcome(lambda:invoke(raw,identity,pins))
    raw,identity,pins=fixture(synthetic_ccsds())
    with patch.object(grib,'_ccsds_values',side_effect=AssertionError('native called')):
        out['preflight_without_native']=a7._preflight(raw,identity,pins,33.,-84.)[2]
    get_message=eccodes.codes_get_message
    with patch.object(eccodes,'codes_get_message',side_effect=lambda h:get_message(h)[:-1]+b'X'):
        out['forced_reencode_mismatch']=outcome(lambda:grib.decode_station(raw,request=a7._preflight(raw,identity,pins,33.,-84.)[0],target=grib.HistoricalPointTarget(33.,-84.)))
    raw,identity,pins=fixture();bad=mutate(raw,5,9,u(255,2));raw,identity,pins=fixture(bad)
    with patch.object(a7.subprocess,'Popen',side_effect=AssertionError('worker started')):
        out['unknown_packing']=outcome(lambda:invoke(raw,identity,pins))
    save('decoder_probes',out)


def followups():
    from dataclasses import replace
    from tools import v11_r09_gate3_a7_decoder as a7
    from test_v11_r09_gate3_a7_decoder import fixture,invoke
    raw,identity,pins=fixture(); out={}
    original_popen=subprocess.Popen
    children=[]
    def exiting_popen(command,**kwargs):
        proc=original_popen([sys.executable,'-I','-B','-c','import time;time.sleep(1.1)'],**kwargs)
        children.append(proc)
        real_kill=proc.kill
        def delayed_kill():
            time.sleep(.25)
            return real_kill()
        proc.kill=delayed_kill
        return proc
    with patch.object(a7,'_available_memory',lambda:8*1024**3),patch.object(a7.subprocess,'Popen',exiting_popen):
        out['timeout_race_through_run']=outcome(lambda:invoke(raw,identity,pins,bounds=replace(a7.Bounds(),cpu_seconds=1,wall_seconds=1)))
    out['timeout_race_through_run']['child_returncodes']=[p.returncode for p in children]
    assert out['timeout_race_through_run']['exception']=='ChildProcessError'
    out['bounds_refusals']={}
    for name,value in [('cpu_seconds',0),('cpu_seconds',61),('wall_seconds',91),('memory_bytes',128*1024**2-1),
        ('memory_bytes',2*1024**3+1),('output_bytes',1023),('output_bytes',65537),('memory_headroom_bytes',-1),
        ('memory_headroom_bytes',2*1024**3+1),('disk_headroom_bytes',-1),('disk_headroom_bytes',2*1024**3+1),('cpu_seconds',True)]:
        result=outcome(lambda:replace(a7.Bounds(),**{name:value}).validate())
        assert result==dict(exception='A7Refusal',message='A7_BOUNDS')
        out['bounds_refusals'][name+':'+str(value)]=result
    required=2*a7.MAX_INPUT+a7.Bounds().output_bytes+a7.Bounds().disk_headroom_bytes
    out['disk_threshold']={}
    for delta in (-1,0):
        stat=type('Stat',(),dict(f_bavail=required+delta,f_frsize=1))()
        with patch.object(a7,'_available_memory',lambda:8*1024**3),patch.object(a7.os,'statvfs',lambda _:stat),patch.object(a7.subprocess,'Popen',side_effect=OSError('injected start failure')):
            result=outcome(lambda:invoke(raw,identity,pins))
        assert result==dict(exception='A7Refusal',message='A7_HOST_HEADROOM' if delta<0 else 'A7_PROCESS_START_FAILED')
        out['disk_threshold'][str(delta)]=result
    save('followup_probes',out)

if __name__=='__main__':
    {'inventory':inventory,'tests':tests,'cgroups':cgroups,'processes':processes,'decoder':decoder,'followups':followups}[sys.argv[1]]()
