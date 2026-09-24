"""Read-only verification of the owner's private inventory; no live source reads.

Only inventory.json, COMPLETE.json and local memory metadata are read. This never
captures again, opens SQLite, copies evidence, changes permissions or runs services.
"""
from datetime import datetime, timezone
import base64
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import time


VERSION = 'alpha_v11_control_inventory_verify_v1'
INVENTORY_VERSION = 'alpha_v11_maintenance_preparation_v1'
DESTINATION = '/var/tmp/alpha-v10-presuspension-inventory-20260924-01'
EXPECTED_SHA256 = 'b161426cff5b39b262e72a6e8142982dd29fa8a0bf29c9965232edf1ff364bd3'
ROOTS = {
    'source_workspace':'/home/alphaadmin/alpha-owner-auth-fix-20260918',
    'deployed_runtime':'/opt/alpha-paper-demo',
    'runtime_state':'/var/lib/alpha-paper-demo',
    'control_config':'/etc/alpha-weather/telegram.json',
    'service_unit':'/etc/systemd/system/alpha-paper-demo.service',
    'resource_dropins':'/etc/systemd/system.control/alpha-paper-demo.service.d',
}
HASH_PATHS = {
    '/etc/alpha-weather/telegram.json', '/etc/systemd/system/alpha-paper-demo.service',
    '/opt/alpha-paper-demo/demo_launcher.py',
    '/etc/systemd/system.control/alpha-paper-demo.service.d/50-MemoryHigh.conf',
    '/etc/systemd/system.control/alpha-paper-demo.service.d/50-MemoryMax.conf',
    '/etc/systemd/system.control/alpha-paper-demo.service.d/50-MemorySwapMax.conf',
}
MISSING = ['JOURNAL_EXPORT', 'ENABLEMENT_LINK_MANIFEST', 'EXTERNAL_GIT_AND_INTERPRETER_CLOSURE',
           'ADDITIONAL_CONFIG_REFERENCE_REVIEW', 'CURRENT_CONSISTENT_BACKUP']
FLAGS = dict(database_opened=False, backup_complete=False, service_mutated=False, signal_sent=False)
MAX_JSON_BYTES = 8*1024**2


class VerificationError(RuntimeError):
    pass


def _require(ok, code):
    if not ok:
        raise VerificationError(code)


def _sha(value):
    return isinstance(value, str) and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def _stable(s):
    return (s.st_dev, s.st_ino, s.st_mode, s.st_uid, s.st_gid, s.st_nlink, s.st_size, s.st_mtime_ns, s.st_ctime_ns)


def _object(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, 'DUPLICATE_JSON_KEY')
        result[key] = value
    return result


def _memory_available():
    with open('/proc/meminfo', 'r') as f:
        for line in f:
            if line.startswith('MemAvailable:'):
                return int(line.split()[1])*1024
    raise VerificationError('MEMORY_HEADROOM_UNKNOWN')


def verify(directory, expected_sha256, *, roots=ROOTS, hash_paths=HASH_PATHS,
           expected_uid=0, expected_gid=0, memory_available=None):
    """Verify only saved inventory bytes/metadata; report redacted aggregates.

    Hash reads are capped at 32 MiB, parsing at 8 MiB, with 16x file-size plus
    192 MiB available-memory headroom. A larger manifest stays hash-only verified;
    it needs an off-host schema review. The 10 s deadline is cooperative, not a
    promise that kernel-blocked I/O can be interrupted. No signal wrapper exists.
    """
    _require(_sha(expected_sha256), 'EXPECTED_HASH_REQUIRED')
    path = Path(directory)
    _require(path.is_absolute() and '..' not in path.parts, 'ABSOLUTE_SAFE_PATH_REQUIRED')
    _require(not any(p.is_symlink() for p in (path, *path.parents)), 'SYMLINK_PATH_REFUSED')
    before = path.lstat()
    _require(stat.S_ISDIR(before.st_mode) and stat.S_IMODE(before.st_mode) == 0o700
             and (before.st_uid, before.st_gid) == (expected_uid, expected_gid), 'PRIVATE_DIRECTORY_CUSTODY')
    deadline = time.monotonic()+10
    def bound():
        _require(time.monotonic() <= deadline, 'VERIFICATION_COOPERATIVE_DEADLINE')
    dfd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        _require(_stable(os.fstat(dfd)) == _stable(before), 'DIRECTORY_IDENTITY_CHANGED')
        _require(set(os.listdir(dfd)) == {'inventory.json', 'COMPLETE.json'}, 'INVENTORY_COMPLETION_FILE_SET')
        def read(name, maximum, *, hash_only_above=None):
            flags = os.O_RDONLY | os.O_NOFOLLOW
            if hasattr(os, 'O_NOATIME'):
                flags |= os.O_NOATIME
            fd = os.open(name, flags, dir_fd=dfd)
            with os.fdopen(fd, 'rb') as f:
                s = os.fstat(f.fileno())
                _require(stat.S_ISREG(s.st_mode) and s.st_nlink == 1 and stat.S_IMODE(s.st_mode) == 0o600
                         and (s.st_uid, s.st_gid) == (expected_uid, expected_gid), 'PRIVATE_FILE_CUSTODY')
                _require(0 < s.st_size <= maximum, 'VERIFICATION_FILE_BOUND')
                retain = hash_only_above is None or s.st_size <= hash_only_above
                if retain:
                    available = _memory_available() if memory_available is None else memory_available
                    _require(available >= 16*s.st_size+192*1024**2, 'VERIFICATION_MEMORY_HEADROOM')
                h = hashlib.sha256(); chunks = []; count = 0
                while True:
                    bound(); block = f.read(min(256*1024, maximum-count+1))
                    if not block:
                        break
                    count += len(block); _require(count <= maximum, 'VERIFICATION_FILE_BOUND'); h.update(block)
                    if retain:
                        chunks.append(block)
                _require(count == s.st_size and _stable(s) == _stable(os.fstat(f.fileno()))
                         and _stable(s) == _stable(os.stat(name, dir_fd=dfd, follow_symlinks=False)),
                         'INVENTORY_CHANGED_DURING_VERIFICATION')
                return b''.join(chunks) if retain else None, h.hexdigest(), count
        raw, actual, size = read('inventory.json', 32*1024**2, hash_only_above=MAX_JSON_BYTES)
        _require(actual == expected_sha256, 'OWNER_INVENTORY_HASH_MISMATCH')
        marker_raw, _, _ = read('COMPLETE.json', 4096)
        marker = json.loads(marker_raw, object_pairs_hook=_object)
        _require(isinstance(marker, dict) and marker.get('inventory_sha256') == actual
                 and marker.get('status') == 'METADATA_INVENTORY_ONLY'
                 and marker.get('destination') == str(path)
                 and all(marker.get(k) is v for k, v in FLAGS.items()), 'COMPLETION_MARKER_MISMATCH')
        result = dict(version=VERSION, verified_at=datetime.now(timezone.utc).isoformat(),
                      inventory_sha256=actual, external_owner_hash_matched=True, inventory_bytes=size,
                      status='INVENTORY_HASH_VERIFIED_SCHEMA_UNCHECKED', schema_verified=False,
                      missing_coverage=MISSING, suspension_readiness='NOT_READY', read_only=True,
                      live_sources_read=False, **FLAGS)
        if raw is not None:
            body = json.loads(raw, object_pairs_hook=_object)
            _require(isinstance(body, dict) and body.get('version') == INVENTORY_VERSION
                     and body.get('status') == 'METADATA_INVENTORY_ONLY'
                     and body.get('missing_coverage') == MISSING
                     and all(body.get(k) is v for k, v in FLAGS.items()), 'INVENTORY_MANIFEST_SCHEMA')
            created = body.get('created_at')
            _require(isinstance(created, str) and len(created) <= 64, 'INVENTORY_CAPTURE_TIME_SCHEMA')
            _require(datetime.fromisoformat(created).utcoffset() == timezone.utc.utcoffset(None), 'INVENTORY_CAPTURE_TIME_SCHEMA')
            rows = body.get('entries'); _require(isinstance(rows, list) and 1 <= len(rows) <= 30000, 'INVENTORY_ENTRY_BOUND')
            seen = set(); found_roots = set(); hashes = set(); total = 0
            counts = {label:dict(entries=0, regular_bytes=0, symlinks=0, special=0, xattrs=0, multilink_files=0)
                      for label in roots}
            db_members = {name:False for name in ('weather-paper.sqlite', 'weather-paper.sqlite-wal', 'weather-paper.sqlite-shm')}
            for row in rows:
                bound(); _require(isinstance(row, dict) and row.get('root') in roots, 'INVENTORY_ROOT_SCHEMA')
                label = row['root']; p = PurePosixPath(row.get('path', '')); root = PurePosixPath(roots[label])
                _require(p.is_absolute() and '..' not in p.parts and (p == root or root in p.parents)
                         and str(p) not in seen, 'INVENTORY_PATH_SCOPE_OR_DUPLICATE')
                seen.add(str(p)); counts[label]['entries'] += 1
                if p == root:
                    _require(row.get('kind') == ('REGULAR' if label in {'control_config', 'service_unit'} else 'DIRECTORY'),
                             'INVENTORY_ROOT_KIND')
                    found_roots.add(label)
                for field in ('mode', 'uid', 'gid', 'device', 'inode', 'nlink', 'size', 'mtime_ns', 'ctime_ns'):
                    _require(type(row.get(field)) is int and row[field] >= 0, 'INVENTORY_METADATA_SCHEMA')
                _require(row['mode'] <= 0o7777 and isinstance(row.get('xattrs_base64'), dict)
                         and len(row['xattrs_base64']) <= 64, 'INVENTORY_MODE_OR_XATTR_SCHEMA')
                counts[label]['xattrs'] += len(row['xattrs_base64'])
                for attr, value in row['xattrs_base64'].items():
                    _require(isinstance(attr, str) and len(attr) <= 255 and isinstance(value, str)
                             and len(value) <= 87384, 'INVENTORY_XATTR_BOUND')
                    _require(len(base64.b64decode(value, validate=True)) <= 65536, 'INVENTORY_XATTR_BOUND')
                kind = row.get('kind')
                _require(kind in {'REGULAR', 'DIRECTORY', 'SYMLINK', 'SPECIAL_REQUIRES_REVIEW'}, 'INVENTORY_KIND_UNKNOWN')
                if kind == 'REGULAR':
                    total += row['size']; counts[label]['regular_bytes'] += row['size']
                    counts[label]['multilink_files'] += int(row['nlink'] > 1)
                    if str(p) in hash_paths:
                        _require(_sha(row.get('sha256')), 'CONFIG_DIGEST_MISSING'); hashes.add(str(p))
                    if label == 'runtime_state' and p.parent == root and p.name in db_members:
                        db_members[p.name] = True
                elif kind == 'SYMLINK':
                    _require(isinstance(row.get('target'), str) and row.get('target_followed') is False, 'INVENTORY_SYMLINK_SCHEMA')
                    counts[label]['symlinks'] += 1
                elif kind == 'SPECIAL_REQUIRES_REVIEW':
                    counts[label]['special'] += 1
            _require(found_roots == set(roots) and hashes == set(hash_paths), 'INVENTORY_REQUIRED_COVERAGE_MISSING')
            _require(type(body.get('regular_bytes')) is int and body['regular_bytes'] == total
                     and type(marker.get('entries')) is int and marker['entries'] == len(rows)
                     and type(marker.get('regular_bytes')) is int and marker['regular_bytes'] == total, 'INVENTORY_TOTALS_MISMATCH')
            result.update(status='INVENTORY_VERIFIED_ONLY', schema_verified=True, captured_at=body.get('created_at'),
                          entries=len(rows), regular_bytes=total, roots=counts, recorded_database_members=db_members,
                          small_configuration_digests_present=len(hashes),
                          recorded_size_within_physical_copier_bound=total <= 512*1024**2,
                          current_sources_revalidated=False, database_integrity_verified=False,
                          permissions_and_xattrs='RECORDED_METADATA_ONLY_NOT_RESTORATION_VERIFIED')
        bound()
        _require(_stable(before) == _stable(path.lstat()) == _stable(os.fstat(dfd)), 'DIRECTORY_CHANGED_DURING_VERIFICATION')
        return result
    finally:
        os.close(dfd)


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--owner-verify-inventory-only', action='store_true', required=True)
    parser.parse_args(argv)
    try:
        _require(os.geteuid() == 0, 'OWNER_PROTECTED_READ_ACCESS_REQUIRED')
        result = verify(DESTINATION, EXPECTED_SHA256)
        print(json.dumps(result, sort_keys=True)); return 0 if result['schema_verified'] else 3
    except (VerificationError, OSError, ValueError, TypeError) as exc:
        print(json.dumps(dict(version=VERSION, status='BLOCKED',
              error=str(exc) if isinstance(exc, VerificationError) else type(exc).__name__,
              read_only=True, live_sources_read=False, suspension_readiness='NOT_READY', **FLAGS)))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
