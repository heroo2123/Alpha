import base64,csv,hashlib,io,json,resource,zipfile
from pathlib import Path
resource.setrlimit(resource.RLIMIT_AS,(256*1024*1024,256*1024*1024)); resource.setrlimit(resource.RLIMIT_CPU,(45,45))
hits=json.loads(Path('/tmp/alpha-v11-decoder-review-20a42f7-cache.json').read_text())['matching_cached_wheels']; assert len(hits)==1
p=Path(hits[0]['path']); site=Path('/home/alphaadmin/AlphaV11_Dev/venv/lib/python3.12/site-packages')
def digest(f):
 h=hashlib.sha256(); n=0
 for chunk in iter(lambda:f.read(1024*1024),b''): h.update(chunk); n+=len(chunk)
 return h.hexdigest(),n
with p.open('rb') as f: archive_sha,archive_size=digest(f)
with zipfile.ZipFile(p) as z:
 record=z.read(hits[0]['record']); installed_record=(site/hits[0]['record']).read_bytes()
 installed_rows={r[0]:r[1:] for r in csv.reader(io.StringIO(installed_record.decode()))}
 rows=list(csv.reader(io.StringIO(record.decode()))); results=[]
 for name,h,size in rows:
  if not name.startswith('eckitlib.libs/'): continue
  with z.open(name) as f: wh,ws=digest(f)
  with (site/name).open('rb') as f: ih,isz=digest(f)
  expected=base64.urlsafe_b64decode(h.split('=',1)[1]+'===').hex()
  results.append(dict(path=name,wheel_sha256=wh,wheel_bytes=ws,installed_sha256=ih,installed_bytes=isz,record_sha256=expected,record_bytes=int(size),installed_equals_cached_wheel=(wh==ih and ws==isz),cached_wheel_matches_own_record=(wh==expected and ws==int(size)),record_row_unchanged=installed_rows[name]==[h,size]))
 report=dict(cache_locator=str(p),archive_sha256=archive_sha,archive_bytes=archive_size,wheel_record_sha256=hashlib.sha256(record).hexdigest(),installed_record_sha256=hashlib.sha256(installed_record).hexdigest(),installed_record_equals_cached_record=record==installed_record,vendored_entries=results,provenance_status='LOCAL_CACHE_ONLY_NOT_INDEPENDENT_UPSTREAM_AUTHENTICATION')
Path('/tmp/alpha-v11-decoder-review-20a42f7-wheel.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({k:v for k,v in report.items() if k!='vendored_entries'},indent=2))
print('vendored entries',len(results),'equal cached wheel',sum(x['installed_equals_cached_wheel'] for x in results),'cached wheel agrees with own RECORD',sum(x['cached_wheel_matches_own_record'] for x in results),'RECORD rows unchanged',sum(x['record_row_unchanged'] for x in results))
