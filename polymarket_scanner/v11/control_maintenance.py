"""Inventory and render a maintenance proposal; never install, signal or restore.

The owner-only inventory records protected metadata in a NEW private directory.
It does not open SQLite, copy the database, or grant suspension readiness.
"""
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
import time


VERSION = 'alpha_v11_maintenance_preparation_v1'
MARKER = '/run/alpha-v11-maintenance-20260924/hold'
DROPIN = '/run/systemd/system/alpha-paper-demo.service.d/zz-alpha-v11-maintenance-20260924.conf'
DROPIN_BYTES = ('[Unit]\nRefuseManualStart=yes\nConditionPathExists=!'+MARKER+
                '\n\n[Service]\nRestart=no\nRestartForceExitStatus=\nSendSIGKILL=no\n').encode()
INVENTORY_DESTINATION = '/var/tmp/alpha-v10-presuspension-inventory-20260924-01'
ROOTS = {
    'source_workspace':'/home/alphaadmin/alpha-owner-auth-fix-20260918',
    'deployed_runtime':'/opt/alpha-paper-demo',
    'runtime_state':'/var/lib/alpha-paper-demo',
    'control_config':'/etc/alpha-weather/telegram.json',
    'service_unit':'/etc/systemd/system/alpha-paper-demo.service',
    'resource_dropins':'/etc/systemd/system.control/alpha-paper-demo.service.d',
}
HASH_SMALL_FILES = {
    '/etc/alpha-weather/telegram.json', '/etc/systemd/system/alpha-paper-demo.service',
    '/opt/alpha-paper-demo/demo_launcher.py',
    '/etc/systemd/system.control/alpha-paper-demo.service.d/50-MemoryHigh.conf',
    '/etc/systemd/system.control/alpha-paper-demo.service.d/50-MemoryMax.conf',
    '/etc/systemd/system.control/alpha-paper-demo.service.d/50-MemorySwapMax.conf',
}


class PreparationError(RuntimeError):
    pass


def proposal():
    return dict(version=VERSION, status='PREPARED_NOT_INSTALLED_NOT_APPROVED',
                dropin_path=DROPIN, dropin_text=DROPIN_BYTES.decode(),
                dropin_sha256=hashlib.sha256(DROPIN_BYTES).hexdigest(), marker_path=MARKER,
                scope='SYSTEMD_UNIT_RESTART_AND_FINAL_KILL_DURING_SAME_BOOT_MAINTENANCE',
                memory_limits_unchanged=True, permanent_units_unchanged=True,
                kernel_oom_prevented=False, reboot_persistence=False,
                service_mutated=False, signal_sent=False)


def _path(raw, *, exists=True):
    path=Path(raw)
    if not path.is_absolute() or '..' in path.parts:
        raise PreparationError('ABSOLUTE_SAFE_PATH_REQUIRED')
    if any(p.is_symlink() for p in (path,*path.parents)):
        raise PreparationError('ROOT_OR_PARENT_SYMLINK_REFUSED')
    if exists and not path.exists():raise PreparationError('REQUIRED_SOURCE_MISSING')
    return path


def _new(path, data):
    fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as f:f.write(data);f.flush();os.fsync(f.fileno())


def inventory(roots, destination, *, hash_paths=(), max_entries=30000, max_seconds=15):
    """Bounded metadata inventory, preserving partial attempts and symlink targets.

    Only explicitly selected small configuration files are read for hashing. All
    other regular files, including DB/WAL/SHM, are stat'ed without opening them.
    ACL/xattr values stay in the private manifest. This is NOT a complete backup.
    """
    if type(max_entries) is not int or not 1<=max_entries<=30000 or not 0<max_seconds<=30:
        raise PreparationError('INVENTORY_BOUND')
    if not isinstance(roots,dict) or not 1<=len(roots)<=16:raise PreparationError('ROOT_BOUND')
    dest=_path(destination,exists=False)
    if os.path.lexists(dest):raise PreparationError('DESTINATION_EXISTS_PRESERVE_IT')
    checked={k:_path(v) for k,v in roots.items()}
    if any(p==dest or p in dest.parents or dest in p.parents for p in checked.values()):
        raise PreparationError('DESTINATION_SOURCE_OVERLAP')
    hashes={str(_path(p)) for p in hash_paths}
    if len(hashes)>16:raise PreparationError('HASH_FILE_BOUND')
    deadline=time.monotonic()+max_seconds;rows=[];regular_bytes=0;matched=set();metadata_bytes=0
    def bound():
        if time.monotonic()>deadline:raise PreparationError('INVENTORY_COOPERATIVE_DEADLINE')
    dest.mkdir(mode=0o700)
    try:
        pending=[(name,path) for name,path in sorted(checked.items())]
        while pending:
            bound()
            if len(rows)>=max_entries:raise PreparationError('INVENTORY_ENTRY_BOUND')
            label,path=pending.pop()
            if any(p.is_symlink() for p in path.parents):raise PreparationError('SOURCE_PARENT_CHANGED')
            before=path.lstat()
            row=dict(root=label,path=str(path),mode=stat.S_IMODE(before.st_mode),uid=before.st_uid,gid=before.st_gid,
                     device=before.st_dev,inode=before.st_ino,nlink=before.st_nlink,size=before.st_size,
                     mtime_ns=before.st_mtime_ns,ctime_ns=before.st_ctime_ns,xattrs_base64={})
            for key in os.listxattr(path,follow_symlinks=False):
                value=os.getxattr(path,key,follow_symlinks=False)
                if len(row['xattrs_base64'])>=64 or len(value)>65536:raise PreparationError('XATTR_BOUND')
                row['xattrs_base64'][key]=base64.b64encode(value).decode()
            if stat.S_ISLNK(before.st_mode):
                row.update(kind='SYMLINK',target=os.readlink(path),target_followed=False)
            elif stat.S_ISDIR(before.st_mode):
                row['kind']='DIRECTORY'
                with os.scandir(path) as children:
                    for child in children:
                        bound()
                        if len(rows)+len(pending)>=max_entries:raise PreparationError('INVENTORY_ENTRY_BOUND')
                        pending.append((label,Path(child.path)))
            elif stat.S_ISREG(before.st_mode):
                row['kind']='REGULAR';regular_bytes+=before.st_size
                if str(path) in hashes:
                    if before.st_size>131072:raise PreparationError('CONFIG_HASH_BYTES_BOUND')
                    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
                    with os.fdopen(fd,'rb') as f:
                        opened=os.fstat(f.fileno())
                        if (opened.st_dev,opened.st_ino)!=(before.st_dev,before.st_ino):
                            raise PreparationError('CONFIG_IDENTITY_CHANGED')
                        raw=f.read(131073)
                    if len(raw)!=before.st_size:raise PreparationError('CONFIG_CHANGED_DURING_HASH')
                    row['sha256']=hashlib.sha256(raw).hexdigest();matched.add(str(path))
            else:row['kind']='SPECIAL_REQUIRES_REVIEW'
            after=path.lstat()
            if (before.st_dev,before.st_ino,before.st_mode,before.st_size,before.st_mtime_ns,before.st_ctime_ns)!=\
               (after.st_dev,after.st_ino,after.st_mode,after.st_size,after.st_mtime_ns,after.st_ctime_ns):
                raise PreparationError('SOURCE_CHANGED_DURING_INVENTORY')
            metadata_bytes+=len(json.dumps(row,sort_keys=True).encode())
            if metadata_bytes>8*1024**2:raise PreparationError('INVENTORY_METADATA_MEMORY_BOUND')
            rows.append(row)
        if hashes!=matched:raise PreparationError('HASH_PATH_NOT_IN_SOURCE_SET')
        bound()
        body=dict(version=VERSION,created_at=datetime.now(timezone.utc).isoformat(),
                  status='METADATA_INVENTORY_ONLY',entries=rows,regular_bytes=regular_bytes,
                  database_opened=False,backup_complete=False,service_mutated=False,signal_sent=False,
                  missing_coverage=['JOURNAL_EXPORT','ENABLEMENT_LINK_MANIFEST','EXTERNAL_GIT_AND_INTERPRETER_CLOSURE',
                                    'ADDITIONAL_CONFIG_REFERENCE_REVIEW','CURRENT_CONSISTENT_BACKUP'])
        data=(json.dumps(body,sort_keys=True,indent=2)+'\n').encode()
        if len(data)>32*1024**2:raise PreparationError('INVENTORY_MANIFEST_BOUND')
        _new(dest/'inventory.json',data)
        result=dict(status=body['status'],destination=str(dest),entries=len(rows),regular_bytes=regular_bytes,
                    inventory_sha256=hashlib.sha256(data).hexdigest(),backup_complete=False,
                    database_opened=False,service_mutated=False,signal_sent=False)
        _new(dest/'COMPLETE.json',(json.dumps(result,sort_keys=True)+'\n').encode())
        fd=os.open(dest,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(fd)
        finally:os.close(fd)
        return result
    except Exception as exc:
        try:_new(dest/'INCOMPLETE.json',json.dumps(dict(error=type(exc).__name__,backup_complete=False)).encode())
        except OSError:pass
        raise


def main(argv=None):
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--owner-inventory-only',action='store_true')
    args=parser.parse_args(argv)
    try:
        if args.owner_inventory_only:
            if os.geteuid()!=0:raise PreparationError('OWNER_PROTECTED_READ_ACCESS_REQUIRED')
            result=inventory(ROOTS,INVENTORY_DESTINATION,hash_paths=HASH_SMALL_FILES)
        else:result=proposal()
        print(json.dumps(result,sort_keys=True));return 0
    except (OSError,ValueError,PreparationError) as exc:
        print(json.dumps(dict(status='BLOCKED',error=str(exc) if isinstance(exc,PreparationError) else type(exc).__name__,
                              signal_sent=False,service_mutated=False,backup_complete=False)))
        return 2


if __name__=='__main__':raise SystemExit(main())
