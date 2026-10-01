"""Independent local evidence comparisons; no decoder import or network."""
import base64, csv, hashlib, json, resource
from pathlib import Path
resource.setrlimit(resource.RLIMIT_AS,(256*1024*1024,256*1024*1024))
resource.setrlimit(resource.RLIMIT_CPU,(45,45))
prefix=Path('/tmp/alpha-v11-decoder-review-82e1619')
c=json.loads((prefix/'docs/V11_R09_GATE3_DECODER_BUILD_OBSERVATION_20261001.json').read_text())
r=json.loads(Path(str(prefix)+'-raw.json').read_text())
site=Path('/home/alphaadmin/AlphaV11_Dev/venv/lib/python3.12/site-packages')
def digest(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
 return h.hexdigest()
records=[]
for pkg in c['python_package_record_checks']:
 record=site/pkg['dist_info']/'RECORD'
 rows=list(csv.reader(record.open()))
 mismatches=[]; unhashed=0; missing=[]
 for name,h,n in rows:
  if not h:unhashed+=1;continue
  path=site/name
  if not path.is_file():missing.append(name);continue
  algorithm,expected=h.split('=',1);assert algorithm=='sha256'
  expected=base64.urlsafe_b64decode(expected+'===').hex()
  observed=digest(path);size=path.stat().st_size
  if observed!=expected or size!=int(n):
   mismatches.append(dict(path=name,record_sha256=expected,observed_sha256=observed,record_bytes=int(n),observed_bytes=size,delta_bytes=size-int(n)))
 assert len(rows)==pkg['entry_count']
 assert unhashed==pkg['unattested_entry_count']
 assert len(rows)-unhashed==pkg['attested_entry_count']
 assert missing==pkg['missing_entries']==[]
 assert [v['path'] for v in mismatches]==pkg['mismatched_entries']
 assert mismatches==pkg.get('mismatched_entry_details',[])
 assert (site/pkg['dist_info']/'direct_url.json').exists()==pkg['has_direct_url_json']==False
 records.append(dict(dist_info=pkg['dist_info'],record_sha256=digest(record),rows=len(rows),unhashed=unhashed,mismatches=mismatches))
for k in ('decoder_sources','loaded_native_libraries','synthetic_decode_probe'):
 assert r[k]==c[k],k
assert r['repo_head']=='82e16191fb3b0dc4ca6f4cb068e1cba748957c48'
assert r['repo_tree']=='3d8a7d3a1bb5815d2320110d115a0694960d713b'
assert r['repo_worktree_clean']
w=json.loads(Path(str(prefix)+'-wheel.json').read_text())
for k,v in c['local_cached_wheel_comparison'].items():
 if k!='note':assert w[k]==v,k
listed={x['path'] for x in r['loaded_native_libraries']}
all_maps=set()
for line in Path(str(prefix)+'-all-maps.txt').read_text().splitlines():
 fields=line.split()
 if len(fields)>=6 and fields[-1].startswith('/') and '.so' in fields[-1]:all_maps.add(fields[-1])
unlisted=sorted(all_maps-listed)
assert len(listed)==42 and len(unlisted)==36
assert len(c['unresolved_identities'])==6
assert [v['status'] for v in c['unresolved_identities']]==['MISSING_EVIDENCE']*4+['OBSERVATION_FOR_REVIEW','MISSING_EVIDENCE']
assert c['qualification_status']=='NOT_QUALIFIED' and c['g3l_status']=='NO-GO'
assert all(c[k]==False for k in ('financial_authority','promotion_authority','host_authority','launch_authority'))
report=dict(comparison_result='PASS',record_audit=records,unlisted_mapped_shared_objects=unlisted,
 source_library_decode_fields_equal=True,cached_wheel_comparison_equal=True,unresolved_items=6,
 candidate_commit=r['repo_head'],candidate_tree=r['repo_tree'],resource_usage=r['resource_usage'],
 mapped_eckit_lib64_count=sum('/eckitlib/lib64/' in x for x in listed),
 mapped_eckit_vendored_count=sum('/eckitlib.libs/' in x for x in listed))
Path(str(prefix)+'-independent.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({k:v for k,v in report.items() if k not in ('record_audit','unlisted_mapped_shared_objects')},indent=2))
