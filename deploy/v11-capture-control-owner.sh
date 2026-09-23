#!/usr/bin/env bash
# Owner-invoked, isolated READ-ONLY control capture. No deployment/service edit.
set -euo pipefail
if [[ $# -gt 1 || ( $# -eq 1 && "$1" != "--check-only" ) ]]; then
  printf '%s\n' 'Usage: bash v11-capture-control-owner.sh [--check-only]' >&2
  exit 2
fi
v11_repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
v11_snapshot_tool="$v11_repo_root/tools/v11_snapshot.py"

# Recheck harmless, readable identity/headroom immediately before requesting the
# owner's sudo action. No secret file or active database is read by this check.
/usr/bin/python3 - "$v11_snapshot_tool" "$v11_repo_root/docs/V11_CONTROL_SOURCE_MANIFEST.json" <<'PY'
import hashlib,json,pathlib,shutil,subprocess,sys
expected={
 '/opt/alpha-paper-demo/demo_launcher.py':'6b531d769d12e498426857ef213eaf70cdac1c10dd42c18edcc011b86659826c',
 '/etc/systemd/system/alpha-paper-demo.service':'e5d201296d6e4dd04139ae1b2d134824862279df5ae359bf20b3d72087130bc7',
 '/opt/alpha-paper-demo/source/requirements.txt':'8ce2ef32a60d43527be60255f5f2875c7f7fa72b2c6c2aac7fe330ac547cce5f',
}
for name,wanted in expected.items():
 if hashlib.sha256(pathlib.Path(name).read_bytes()).hexdigest()!=wanted:
  raise SystemExit('BLOCKED: readable control configuration changed; reverify it first')
source_manifest=pathlib.Path(sys.argv[2]).read_bytes()
if hashlib.sha256(source_manifest).hexdigest()!='00e9c933728b0207f494dae6d7b0a7ecae92d28fdbb0a555e4f04b6d738e6b58':
 raise SystemExit('BLOCKED: control source manifest identity changed')
source_manifest=json.loads(source_manifest)
deployed=pathlib.Path('/opt/alpha-paper-demo/source')
for relative,wanted in source_manifest['files'].items():
 path=deployed/relative
 if any(p.is_symlink() for p in (path,*path.parents)) or hashlib.sha256(path.read_bytes()).hexdigest()!=wanted:
  raise SystemExit('BLOCKED: deployed source differs from pinned control; preserve and reverify')
actual_python={str(p.relative_to(deployed)) for p in (deployed/'polymarket_scanner').rglob('*.py')}
expected_python={p for p in source_manifest['files'] if p.endswith('.py')}
if actual_python!=expected_python:
 raise SystemExit('BLOCKED: unexpected deployed Python files; reverify control')
tool=pathlib.Path(sys.argv[1])
if any(p.is_symlink() for p in (tool,*tool.parents)):
 raise SystemExit('BLOCKED: snapshot tool path must not contain symlinks')
if hashlib.sha256(tool.read_bytes()).hexdigest()!='6317cc599935147a9084d4775cb9099898db5c3f1365fcf67398ef79c1b1a172':
 raise SystemExit('BLOCKED: snapshot tool differs from the tested reviewed bytes')
memory={r.split(':')[0]:int(r.split()[1])*1024 for r in pathlib.Path('/proc/meminfo').read_text().splitlines() if r.startswith(('MemAvailable:','SwapFree:'))}
if memory['MemAvailable']<384*1024**2 or shutil.disk_usage('/var/tmp').free<2*1024**3:
 raise SystemExit('BLOCKED: snapshot headroom insufficient; no control change made')
def field(unit,property):
 return subprocess.check_output(['/usr/bin/systemctl','show',unit,'--property='+property,'--value'],text=True,timeout=5).strip()
if field('alpha-paper-demo.service','ActiveState')!='active':
 raise SystemExit('BLOCKED: control service not active; investigate before capture')
if field('alpha-weather-execution.service','LoadState')!='masked' or field('alpha-weather-execution.service','ActiveState')!='inactive':
 raise SystemExit('BLOCKED: executor containment differs; no changes made')
if field('alpha-weather-controller.service','ActiveState')!='inactive':
 raise SystemExit('BLOCKED: production controller is not inactive')
if pathlib.Path('/var/tmp/alpha-v11-control-evidence-20260923').exists():
 raise SystemExit('BLOCKED: destination already exists; preserve and inspect it')
print(json.dumps({'status':'READ_ONLY_PREFLIGHT_PASSED','memory_available_bytes':memory['MemAvailable'],'disk_free_bytes':shutil.disk_usage('/var/tmp').free,'executor':'MASKED_INACTIVE'}))
PY

if [[ "${1:-}" == "--check-only" ]]; then
  exit 0
fi

# This is the sole owner-only step. Existing units, permissions, code, database
# contents and executor mask remain unchanged. Credentials are inaccessible.
exec sudo /usr/bin/systemd-run --wait --collect --pipe \
  --unit=alpha-v11-control-snapshot-20260923 \
  --property=CPUQuota=20% --property=MemoryMax=128M --property=MemorySwapMax=0 \
  --property=TasksMax=8 --property=RuntimeMaxSec=90s \
  --property=Nice=19 --property=IOSchedulingClass=idle \
  --property=ProtectSystem=strict --property=ProtectHome=read-only \
  --property=ReadWritePaths=/var/tmp \
  --property=ReadOnlyPaths=/var/lib/alpha-paper-demo \
  '--property=InaccessiblePaths=/etc/alpha-weather-execution /var/lib/alpha-weather-execution /var/lib/alpha-weather-signals' \
  --property=NoNewPrivileges=yes --property=PrivateNetwork=yes \
  '--property=CapabilityBoundingSet=CAP_DAC_READ_SEARCH CAP_CHOWN CAP_FOWNER' \
  /usr/bin/python3 -I -B "$v11_snapshot_tool" \
  --source /var/lib/alpha-paper-demo/weather-paper.sqlite \
  --destination /var/tmp/alpha-v11-control-evidence-20260923 \
  --release-sha 5bbac24759349714d4521faf9e087a14c5c0ae05 \
  --tree-sha d5d2b806e273f11e2f832940f483a5f656462584 \
  --config-sha256 ffcefb7f6be610b939a14f005ce28522cb0b31e0bfa8725770075d08a8fbf436 \
  --context-file status=/var/lib/alpha-paper-demo/status.json \
  --context-file release=/var/lib/alpha-paper-demo/release.sha \
  --output-owner alphaadmin
