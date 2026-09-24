"""Preservation-only primitives. No service, signal, restore or privilege API.

Source files are archived without following symlinks. Failed attempts are kept
as incomplete private evidence. An online physical copy is never a SQLite
restore authority: use the separately verified pinned-transaction snapshot.
"""
from contextlib import closing
from datetime import datetime, timezone
import base64
import hashlib
import json
import os
from pathlib import Path
import stat
import tarfile
import time


VERSION = 'alpha_v11_preservation_only_v1'
MAX_FILES = 30000
MAX_BYTES = 512*1024**2
MAX_MANIFEST = 32*1024**2


class PreservationError(RuntimeError):
    pass


def digest_file(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda:f.read(256*1024), b''):
            h.update(b)
    return h.hexdigest()


def _path(path, *, exists=True):
    p = Path(path)
    if not p.is_absolute() or '..' in p.parts:
        raise PreservationError('ABSOLUTE_PATH_REQUIRED')
    for item in (p, *p.parents):
        if item.is_symlink():
            raise PreservationError('ROOT_OR_PARENT_SYMLINK_REFUSED')
    if exists and not p.exists():
        raise PreservationError('REQUIRED_PATH_MISSING')
    return p


def _metadata(path):
    s = path.lstat()
    attrs = {}
    for key in os.listxattr(path, follow_symlinks=False):
        value = os.getxattr(path, key, follow_symlinks=False)
        if len(attrs) >= 64 or sum(len(v) for v in attrs.values())+len(value)*2 > 128*1024:
            raise PreservationError('EXTENDED_ATTRIBUTE_BOUND')
        attrs[key] = base64.b64encode(value).decode('ascii')
    return dict(mode=stat.S_IMODE(s.st_mode), uid=s.st_uid, gid=s.st_gid, size=s.st_size,
                atime_ns=s.st_atime_ns, mtime_ns=s.st_mtime_ns, ctime_ns=s.st_ctime_ns, device=s.st_dev,
                inode=s.st_ino, nlink=s.st_nlink, xattrs_base64=attrs)


def _write_new(path, data):
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as f:
        f.write(data); f.flush(); os.fsync(f.fileno())


def _stable(metadata):
    # Preserve original atime but do not mistake read-access updates for writes.
    return {k:v for k,v in metadata.items() if k!='atime_ns'}


def supervision_assessment(properties, *, pids, process_status):
    """Separate expected normal TERM behavior from an impossible global promise."""
    main = str(properties.get('MainPID', '0'))
    term_bit = 1 << (15-1)
    masks = ('SigBlk', 'SigIgn', 'SigCgt', 'SigPnd', 'ShdPnd')
    known_masks = all(isinstance(process_status.get(k), str) for k in masks)
    try:
        term_default = known_masks and not any(int(process_status[k],16)&term_bit for k in masks)
    except ValueError:
        term_default = False
    normal = (properties.get('Type') == 'simple' and properties.get('Restart') == 'on-failure'
              and properties.get('RestartForceExitStatus') == '' and term_default)
    blockers = []
    if properties.get('Restart') != 'no': blockers.append('AUTOMATIC_RESTART_POLICY_ENABLED')
    if properties.get('SendSIGKILL') != 'no': blockers.append('AUTOMATIC_FINAL_KILL_POLICY_ENABLED')
    if main == '0' or pids != [main]: blockers.append('EXPECTED_SINGLE_MAIN_PROCESS_NOT_PROVEN')
    if not term_default: blockers.append('DEFAULT_UNBLOCKED_SIGTERM_NOT_PROVEN')
    for name in ('ExecStop_configured', 'ExecStopPost_configured'):
        if properties.get(name) is not False: blockers.append(name.upper())
    for name in ('OnSuccess','OnFailure','Triggers','TriggeredBy','Job'):
        if properties.get(name) != '': blockers.append('AUTOMATION_OR_UNKNOWN_'+name.upper())
    if properties.get('WatchdogUSec') != '0' or properties.get('RuntimeMaxUSec') != 'infinity':
        blockers.append('WATCHDOG_OR_RUNTIME_EXPIRY_NOT_EXCLUDED')
    # Kernel OOM, parent cgroup actions, other admins and future races cannot be
    # excluded by reading one unit. Do not certify a universal negative.
    blockers.append('KERNEL_PARENT_OR_EXTERNAL_ACTIONS_NOT_EXCLUDABLE')
    return dict(version=VERSION, normal_sigterm_exit_expected_clean=normal,
                main_process_term_disposition_checked=term_default,
                process_in_d_state=str(process_status.get('State','')).startswith('D'),
                unconditional_no_automatic_action_guarantee=False,
                suspension_readiness='NOT_READY', blockers=blockers,
                signal_sent=False, service_mutated=False)


def preserve_files(roots, destination, *, max_bytes=MAX_BYTES, max_files=MAX_FILES,
                   max_seconds=60, metadata=None):
    """Create a new private physical archive, including ACL/xattr metadata.

    This is only physical preservation. Live database consistency and source
    authority require a separate verified snapshot and runtime manifest. Bounds
    are cooperative; a kernel-blocked read cannot be promised a hard deadline.
    """
    destination = _path(destination, exists=False)
    if os.path.lexists(destination):
        raise PreservationError('DESTINATION_EXISTS_PRESERVE_IT')
    if not 0 < max_seconds <= 120 or not 0 < max_bytes <= MAX_BYTES or not 0 < max_files <= MAX_FILES:
        raise PreservationError('PRESERVATION_BOUNDS')
    if not isinstance(roots, dict) or not 1 <= len(roots) <= 16:
        raise PreservationError('ROOT_BOUND')
    checked = {}
    for name, raw in roots.items():
        if not isinstance(name,str) or not name.isascii() or not name.replace('_','').isalnum() or len(name)>40:
            raise PreservationError('ROOT_LABEL_INVALID')
        p = _path(raw)
        if p == destination or p in destination.parents or destination in p.parents:
            raise PreservationError('DESTINATION_SOURCE_OVERLAP')
        if any(p == old or p in old.parents or old in p.parents for old in checked.values()):
            raise PreservationError('OVERLAPPING_SOURCE_ROOTS')
        checked[name] = p
    fs = os.statvfs(destination.parent)
    if fs.f_bavail*fs.f_frsize < max_bytes+1024**3:
        raise PreservationError('PRESERVATION_DISK_HEADROOM')
    started = time.monotonic(); deadline = started+max_seconds
    def bound():
        if time.monotonic() > deadline: raise PreservationError('PRESERVATION_DEADLINE')
    destination.mkdir(mode=0o700)
    rows = []; used = 0
    archive = destination/'physical.tar'
    try:
        fd = os.open(archive, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd,'wb') as output, tarfile.open(fileobj=output,mode='w',format=tarfile.PAX_FORMAT,dereference=False) as tar:
            for label, root in sorted(checked.items()):
                pending = [(root,label)]
                while pending:
                    bound(); path, name = pending.pop()
                    if len(rows) >= max_files: raise PreservationError('PRESERVATION_FILE_BOUND')
                    for parent in path.parents:
                        if parent.is_symlink():raise PreservationError('SOURCE_PARENT_BECAME_SYMLINK')
                    before = _metadata(path); mode = path.lstat().st_mode
                    row = dict(name=name, source=str(path), metadata=before)
                    info = tarfile.TarInfo(name); info.mode=before['mode']; info.uid=before['uid']; info.gid=before['gid']
                    info.mtime=before['mtime_ns']/1e9
                    if stat.S_ISLNK(mode):
                        row.update(kind='SYMLINK', link_target=os.readlink(path)); info.type=tarfile.SYMTYPE
                        info.linkname=row['link_target']; tar.addfile(info)
                    elif stat.S_ISDIR(mode):
                        row['kind']='DIRECTORY'; info.type=tarfile.DIRTYPE; tar.addfile(info)
                        children=sorted(path.iterdir(), reverse=True)
                        if len(rows)+len(pending)+len(children)>max_files: raise PreservationError('PRESERVATION_FILE_BOUND')
                        pending.extend((p,name+'/'+p.name) for p in children)
                    elif stat.S_ISREG(mode):
                        if used+before['size']>max_bytes: raise PreservationError('PRESERVATION_BYTE_BOUND')
                        flags=os.O_RDONLY | os.O_NOFOLLOW
                        # O_NOATIME is used only when already entitled; no access
                        # escalation, permission change or fallback privilege path.
                        if hasattr(os,'O_NOATIME') and os.geteuid() in (0,before['uid']): flags |= os.O_NOATIME
                        source_fd=os.open(path,flags)
                        h=hashlib.sha256()
                        class Reader:
                            def read(self, n):
                                bound(); b=source.read(min(n,256*1024)); h.update(b); return b
                        with os.fdopen(source_fd,'rb') as source:
                            st=os.fstat(source.fileno())
                            if (st.st_dev,st.st_ino,st.st_size)!=(before['device'],before['inode'],before['size']):
                                raise PreservationError('SOURCE_CHANGED_BEFORE_READ')
                            info.size=before['size'];tar.addfile(info,Reader())
                        used+=before['size'];row.update(kind='REGULAR',sha256=h.hexdigest())
                    else:
                        raise PreservationError('SPECIAL_FILE_REQUIRES_EXPLICIT_PRESERVATION_PLAN')
                    after=_metadata(path)
                    if _stable(before)!=_stable(after) or row.get('link_target') is not None and row['link_target']!=os.readlink(path):
                        raise PreservationError('SOURCE_CHANGED_DURING_PHYSICAL_COPY')
                    rows.append(row)
            output.flush();os.fsync(output.fileno())
        # Tar's end markers were added on context exit; sync the complete file.
        with archive.open('rb') as f: os.fsync(f.fileno())
        for row in rows:
            bound()
            if _stable(_metadata(Path(row['source'])))!=_stable(row['metadata']):
                raise PreservationError('SOURCE_CHANGED_BEFORE_COMPLETION')
        bound()
        manifest=dict(version=VERSION,created_at=datetime.now(timezone.utc).isoformat(),
                classification='PRIVATE_CONTROL_PRESERVATION',archive='physical.tar',
                archive_sha256=digest_file(archive),physical_bytes=used,entries=rows,
                source_roots={k:str(v) for k,v in checked.items()},context=metadata or {},
                consistency='INDIVIDUAL_STABLE_FILES_NOT_ATOMIC_LIVE_DATABASE_SET',
                database_restore_authority=False,service_mutated=False,signal_sent=False,
                deadline_kind='COOPERATIVE_BETWEEN_IO_OPERATIONS')
        encoded=(json.dumps(manifest,sort_keys=True,indent=2)+'\n').encode()
        if len(encoded)>MAX_MANIFEST:raise PreservationError('MANIFEST_BYTE_BOUND')
        bound();_write_new(destination/'manifest.json',encoded)
        marker=dict(manifest_sha256=hashlib.sha256(encoded).hexdigest(),archive_sha256=manifest['archive_sha256'])
        _write_new(destination/'COMPLETE.json',(json.dumps(marker,sort_keys=True)+'\n').encode())
        fd=os.open(destination,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(fd)
        finally:os.close(fd)
        return marker
    except Exception as exc:
        # Preserve partial evidence; never remove or overwrite old or new files.
        try:_write_new(destination/'INCOMPLETE.json',json.dumps({'status':'INCOMPLETE','error':type(exc).__name__}).encode())
        except OSError:pass
        raise


def verify_preservation(directory, *, expected_manifest_sha256=None):
    directory=_path(directory)
    if (directory/'INCOMPLETE.json').exists():raise PreservationError('INCOMPLETE_PRESERVATION')
    manifest_path=_path(directory/'manifest.json');marker_path=_path(directory/'COMPLETE.json')
    if manifest_path.stat().st_size>MAX_MANIFEST or marker_path.stat().st_size>4096:raise PreservationError('MANIFEST_BYTE_BOUND')
    data=manifest_path.read_bytes();marker=json.loads(marker_path.read_text())
    if hashlib.sha256(data).hexdigest()!=marker.get('manifest_sha256'):raise PreservationError('MANIFEST_HASH_MISMATCH')
    if expected_manifest_sha256 is not None and marker['manifest_sha256']!=expected_manifest_sha256:
        raise PreservationError('EXTERNAL_MANIFEST_HASH_MISMATCH')
    manifest=json.loads(data)
    if manifest.get('version')!=VERSION or manifest.get('archive')!='physical.tar':raise PreservationError('MANIFEST_VERSION')
    archive=_path(directory/'physical.tar')
    if digest_file(archive)!=manifest['archive_sha256'] or manifest['archive_sha256']!=marker.get('archive_sha256'):
        raise PreservationError('ARCHIVE_HASH_MISMATCH')
    entries=manifest.get('entries',[])
    if not isinstance(entries,list) or len(entries)>MAX_FILES:raise PreservationError('MANIFEST_FILE_BOUND')
    expected={r['name']:r for r in entries}
    if len(expected)!=len(entries):raise PreservationError('DUPLICATE_MANIFEST_PATH')
    seen=set()
    with tarfile.open(archive,'r:') as tar:
        for item in tar:
            name=item.name
            if name not in expected or name in seen or Path(name).is_absolute() or '..' in Path(name).parts:
                raise PreservationError('ARCHIVE_MEMBER_MISMATCH')
            row=expected[name];m=row['metadata'];seen.add(name)
            if (item.uid,item.gid,item.mode)!=(m['uid'],m['gid'],m['mode']):raise PreservationError('ARCHIVE_MODE_OR_OWNER_MISMATCH')
            if row['kind']=='REGULAR':
                if not item.isfile() or item.size!=m['size']:raise PreservationError('ARCHIVE_FILE_MISMATCH')
                with tar.extractfile(item) as source:
                    h=hashlib.sha256()
                    for b in iter(lambda:source.read(256*1024),b''):h.update(b)
                if h.hexdigest()!=row['sha256']:raise PreservationError('ARCHIVE_MEMBER_HASH_MISMATCH')
            elif row['kind']=='DIRECTORY':
                if not item.isdir():raise PreservationError('ARCHIVE_DIRECTORY_MISMATCH')
            elif row['kind']=='SYMLINK':
                if not item.issym() or item.linkname!=row['link_target']:raise PreservationError('ARCHIVE_LINK_MISMATCH')
            else:raise PreservationError('ARCHIVE_KIND_UNKNOWN')
    if seen!=set(expected):raise PreservationError('ARCHIVE_MEMBER_MISSING')
    return dict(verified=True,manifest_sha256=marker['manifest_sha256'],archive_sha256=marker['archive_sha256'],
                entries=len(entries),physical_bytes=manifest['physical_bytes'],database_restore_authority=False,
                external_manifest_hash_matched=expected_manifest_sha256 is not None)


def rehearse_snapshot(snapshot_module, snapshot_directory):
    """Verify a completed pinned backup offline; no restore into original paths."""
    db,manifest=snapshot_module.open_verified_snapshot(Path(snapshot_directory))
    with closing(db):
        if [tuple(r) for r in db.execute('PRAGMA quick_check')]!=[('ok',)]:
            raise PreservationError('RECOVERY_COPY_INTEGRITY_FAILED')
    return dict(snapshot_sha256=manifest['snapshot_sha256'],quick_check='ok',
                committed_wal_included=manifest.get('committed_wal_included') is True,
                original_paths_overwritten=False,service_started=False,
                recovery_scope='SQLITE_READABILITY_ONLY_NOT_FULL_RUNTIME_RECOVERY')


def main(argv=None):
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest='action',required=True)
    create=commands.add_parser('archive')
    create.add_argument('--source-root',action='append',required=True,metavar='LABEL=ABSOLUTE_PATH')
    create.add_argument('--destination',type=Path,required=True)
    verify=commands.add_parser('verify')
    verify.add_argument('--directory',type=Path,required=True)
    verify.add_argument('--expected-manifest-sha256',required=True)
    args=parser.parse_args(argv)
    try:
        if args.action=='archive':
            roots={}
            for raw in args.source_root:
                name,separator,path=raw.partition('=')
                if not separator or name in roots:raise PreservationError('ROOT_ARGUMENT_INVALID')
                roots[name]=Path(path)
            result=preserve_files(roots,args.destination)
        else:
            result=verify_preservation(args.directory,expected_manifest_sha256=args.expected_manifest_sha256)
        print(json.dumps(dict(status='PRESERVATION_ONLY',result=result,signal_sent=False,service_mutated=False),sort_keys=True))
        return 0
    except (PreservationError,OSError,ValueError,tarfile.TarError) as exc:
        print(json.dumps(dict(status='BLOCKED',error=str(exc) if isinstance(exc,PreservationError) else type(exc).__name__,
                              signal_sent=False,service_mutated=False)))
        return 2


if __name__=='__main__':
    raise SystemExit(main())
