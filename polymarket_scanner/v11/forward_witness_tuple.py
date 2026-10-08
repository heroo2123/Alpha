"""Unused, offline, pure temporary-file witness tuple comparator.

This is the "Temporary-file witness prototype" slice of
docs/V11_FORWARD_PROTECTED_JOURNAL_AMENDMENT_20261007.md §8 item 4, continuing
the F6 Witness ABA item of that amendment's §5 and §7. It has no caller in
this repository, no protected-path access, no database/network/provider/
credential/account access and grants no financial or forward-qualification
effect. `shadow_commission._require_protected_interval` remains an
unconditional raise, untouched and unwired to anything here.

Two narrowly separated pieces:

- `read_file_witness(path)` is the impure OS reader. It stats, opens, hashes
  and reads the ext4 inode generation (`FS_IOC_GETVERSION`) and owning mount
  of exactly one plain regular file, and returns either a `FileWitness` or an
  `Unsupported(code)` sentinel. It must only ever be exercised against
  disposable tmpdir fixtures (see the paired test module); it is not reviewed
  or safe for any protected or caller-selected path. It never raises: OS-level
  failures (including a denied or too-long ancestor path) and malformed input
  (non-str path, embedded NUL, ...) come back as `Unsupported(code)`.
- `compare_witness` / `evaluate_sequence` are pure: they take already-captured
  `FileWitness`/`Unsupported` values and never touch a filesystem.

Per the amendment's §5 ("It is not an unconditional detector for all
histories... Owner commissioning must exclude uncontrolled writers/mount/
clock/restore operations during coverage") and §7 ("Undetectable/unsupported
cases are documented UNKNOWN, never described as proven continuity"), the
comparator below returns only two outcomes: a detected `DISCONTINUITY`, or
`UNKNOWN`. There is no third "proven continuous" result. A fully matching
tuple is reported as `UNKNOWN` with code `TUPLE_MATCHED_NOT_PROOF_OF_CONTINUITY`
because stat/statx-style metadata matching is consistent with "nothing
changed" but is equally consistent with an undetectable same-tick replace-and-
restore or an unsupported filesystem/mount; this module never claims the
former over the latter.

The reader binds its parent-directory and mount observations to the actual
open file descriptor rather than re-resolving by path after the fact:
`parent_dev`/`parent_ino` come from an `fstat` of the directory file
descriptor opened (`O_DIRECTORY | O_NOFOLLOW`) before the child is looked up,
and the child is opened/rechecked relative to that directory fd
(`dir_fd=`), so a rename of the directory elsewhere during the read cannot
retarget which parent is reported. The mount is identified by the open file
descriptor's exact `mnt_id` (from `/proc/self/fdinfo/<fd>`), not by a
path-prefix guess against `/proc/self/mountinfo`, so a stacked/shadowed mount
at the same mount point cannot be mistaken for the file's actual mount.
"""

from dataclasses import dataclass
from pathlib import Path
import fcntl
import hashlib
import os
import stat as stat_module
import struct


FS_IOC_GETVERSION = 0x80087601  # _IOR('v', 1, long); 64-bit Linux long is 8 bytes.
SUPPORTED_FSTYPES = frozenset({'ext4'})
MAX_WITNESS_BYTES = 8 * 1024 * 1024
MAX_SEQUENCE = 64

# Fields that identify *which* storage object this is, not merely its current
# content. A mismatch here means the tuple is not describing the same object
# any more (new/reused inode, different generation, different owning mount,
# different parent directory), independent of whether bytes happen to match.
IDENTITY_FIELDS = ('dev', 'ino', 'generation', 'mount_id', 'mount_fstype',
                    'mount_major', 'mount_minor', 'parent_dev', 'parent_ino')
CONTENT_FIELDS = ('size', 'sha256')
METADATA_FIELDS = ('mode', 'uid', 'gid', 'nlink', 'mtime_ns')


@dataclass(frozen=True)
class FileWitness:
    dev: int
    ino: int
    generation: int
    mode: int
    uid: int
    gid: int
    nlink: int
    size: int
    ctime_ns: int
    mtime_ns: int
    sha256: str
    mount_id: int
    mount_fstype: str
    mount_major: int
    mount_minor: int
    parent_dev: int
    parent_ino: int


@dataclass(frozen=True)
class Unsupported:
    """A witness this reader could not safely capture; never treat as a match."""

    code: str


@dataclass(frozen=True)
class Verdict:
    status: str  # 'DISCONTINUITY' or 'UNKNOWN'; no third "proven continuous" value.
    code: str
    index: int = None


def _fd_mount_id(fd):
    """Read this open fd's current mount ID from /proc/self/fdinfo.

    Binds the mount lookup to the file descriptor that was actually read,
    not to a path re-resolved after the fd is closed. Never raises; returns
    None if the mount ID cannot be determined.
    """
    try:
        for line in Path(f'/proc/self/fdinfo/{fd}').read_text().splitlines():
            if line.startswith('mnt_id:'):
                return int(line.split(':', 1)[1].strip())
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    return None


def _mount_entry_for_id(mnt_id):
    """Look up the mountinfo entry with this exact mount ID; never raises.

    Matching by exact ID (rather than longest mount-point path prefix) means
    a stacked/shadowed mount at the same mount point cannot be confused for
    the mount actually backing the open file descriptor. Returns
    (fstype, major, minor) or None.
    """
    try:
        data = Path('/proc/self/mountinfo').read_bytes()
    except OSError:
        return None
    text = data.decode('utf-8', 'surrogateescape')
    for line in text.splitlines():
        before, separator, after = line.partition(' - ')
        if not separator:
            continue
        fields = before.split()
        tail = after.split()
        if len(fields) < 3 or not tail:
            continue
        try:
            if int(fields[0]) != mnt_id:
                continue
            major, minor = (int(part) for part in fields[2].split(':'))
        except ValueError:
            continue
        return (tail[0], major, minor)
    return None


def read_file_witness(path):
    """Capture one immutable identity/content tuple for a plain regular file.

    Structural or unsupported conditions return `Unsupported(code)` rather
    than raising or guessing; callers must treat that exactly like any other
    undetectable case (see `compare_witness`), never as a passing witness.
    Malformed input (non-str/non-path-like, embedded NUL, ...) is reported
    the same way, as `Unsupported`, never as a raised exception.
    """
    try:
        return _read_file_witness(path)
    except (ValueError, TypeError):
        return Unsupported('WITNESS_INVALID_INPUT')
    except OSError:
        # A cleanup close can fail after the inner reader has prepared either
        # a witness or a specific refusal. Never expose the pending witness.
        return Unsupported('WITNESS_OS_ERROR')


def _read_file_witness(path):
    p = Path(path)
    # Lexical and path-based: it narrows but does not close the window
    # between this check and the parent-directory open below. A write-capable
    # attacker on an ancestor directory who swaps it for a symlink in that
    # window is a same-tick-class residual, not something this check proves
    # closed; it is not treated as continuity proof either way. A denied or
    # too-long ancestor path raises OSError from `is_symlink()` itself rather
    # than returning a symlink verdict; both come back as `Unsupported` so
    # this reader still never raises.
    try:
        if p.is_symlink() or any(ancestor.is_symlink() for ancestor in p.parents):
            return Unsupported('WITNESS_SYMLINK_REFUSED')
    except OSError:
        return Unsupported('WITNESS_SYMLINK_CHECK_FAILED')
    name = p.name
    if not name:
        return Unsupported('WITNESS_INVALID_INPUT')
    try:
        parent_fd = os.open(p.parent, os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError:
        return Unsupported('WITNESS_PARENT_STAT_FAILED')
    try:
        return _read_file_witness_in_parent(parent_fd, name)
    finally:
        os.close(parent_fd)


def _read_file_witness_in_parent(parent_fd, name):
    try:
        parent_stat = os.fstat(parent_fd)
    except OSError:
        return Unsupported('WITNESS_PARENT_STAT_FAILED')
    try:
        before = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except OSError:
        return Unsupported('WITNESS_OPEN_FAILED')
    if stat_module.S_ISLNK(before.st_mode):
        return Unsupported('WITNESS_SYMLINK_REFUSED')
    if not stat_module.S_ISREG(before.st_mode):
        return Unsupported('WITNESS_NOT_REGULAR_FILE')
    if before.st_nlink != 1:
        return Unsupported('WITNESS_MULTILINK_REFUSED')
    if before.st_size > MAX_WITNESS_BYTES:
        return Unsupported('WITNESS_BYTE_BOUND')
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
                     | os.O_NOCTTY | os.O_CLOEXEC, dir_fd=parent_fd)
    except OSError:
        return Unsupported('WITNESS_OPEN_FAILED')
    try:
        return _read_opened_file_witness(fd, parent_fd, name, before, parent_stat)
    finally:
        os.close(fd)


def _read_opened_file_witness(fd, parent_fd, name, before, parent_stat):
    # O_NONBLOCK above means a path swapped to a FIFO between the stat and
    # the open (above) cannot hang this open(); it returns immediately and
    # the S_ISREG check below refuses it instead.
    try:
        opened = os.fstat(fd)
    except OSError:
        return Unsupported('WITNESS_OPEN_FAILED')
    if not stat_module.S_ISREG(opened.st_mode):
        return Unsupported('WITNESS_NOT_REGULAR_FILE')
    if opened.st_nlink != 1:
        return Unsupported('WITNESS_MULTILINK_REFUSED')
    if (opened.st_dev, opened.st_ino, opened.st_size) != \
       (before.st_dev, before.st_ino, before.st_size):
        return Unsupported('WITNESS_CHANGED_BEFORE_READ')
    digest = hashlib.sha256()
    total = 0
    try:
        with os.fdopen(fd, 'rb', closefd=False) as f:
            for block in iter(lambda: f.read(256 * 1024), b''):
                total += len(block)
                if total > MAX_WITNESS_BYTES:
                    return Unsupported('WITNESS_BYTE_BOUND')
                digest.update(block)
        after = os.fstat(fd)
    except OSError:
        return Unsupported('WITNESS_READ_FAILED')
    if total != after.st_size:
        return Unsupported('WITNESS_SHORT_READ')
    if (after.st_dev, after.st_ino, after.st_mode, after.st_nlink,
        after.st_mtime_ns, after.st_ctime_ns, after.st_size) != \
       (before.st_dev, before.st_ino, before.st_mode, before.st_nlink,
         before.st_mtime_ns, before.st_ctime_ns, before.st_size):
        return Unsupported('WITNESS_CHANGED_DURING_READ')
    try:
        recheck = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except OSError:
        return Unsupported('WITNESS_CHANGED_DURING_READ')
    if (recheck.st_dev, recheck.st_ino) != (after.st_dev, after.st_ino):
        return Unsupported('WITNESS_CHANGED_DURING_READ')
    try:
        buf = fcntl.ioctl(fd, FS_IOC_GETVERSION, struct.pack('L', 0))
        generation, = struct.unpack('L', buf)
    except (OSError, struct.error):
        return Unsupported('WITNESS_GENERATION_UNSUPPORTED')
    mnt_id = _fd_mount_id(fd)
    if mnt_id is None:
        return Unsupported('WITNESS_MOUNT_UNRESOLVED')
    entry = _mount_entry_for_id(mnt_id)
    if entry is None:
        return Unsupported('WITNESS_MOUNT_UNRESOLVED')
    fstype, major, minor = entry
    if fstype not in SUPPORTED_FSTYPES:
        return Unsupported('WITNESS_MOUNT_UNSUPPORTED')
    if (major, minor) != (os.major(after.st_dev), os.minor(after.st_dev)):
        return Unsupported('WITNESS_MOUNT_DEVICE_MISMATCH')
    return FileWitness(
        dev=after.st_dev, ino=after.st_ino, generation=generation,
        mode=stat_module.S_IMODE(after.st_mode), uid=after.st_uid, gid=after.st_gid,
        nlink=after.st_nlink, size=after.st_size, ctime_ns=after.st_ctime_ns,
        mtime_ns=after.st_mtime_ns, sha256=digest.hexdigest(), mount_id=mnt_id,
        mount_fstype=fstype, mount_major=major, mount_minor=minor,
        parent_dev=parent_stat.st_dev, parent_ino=parent_stat.st_ino)


def compare_witness(before, after):
    """Pure two-witness comparator; touches no filesystem and takes no path.

    Only ever returns `DISCONTINUITY` (treat the prior interval as aborted) or
    `UNKNOWN` (the tuple cannot prove either outcome). There is no return
    value that asserts proven content continuity; see the module docstring.
    """
    if not isinstance(before, FileWitness) or not isinstance(after, FileWitness):
        return Verdict('UNKNOWN', 'WITNESS_UNSUPPORTED_OR_MISSING')
    if any(getattr(before, field) != getattr(after, field) for field in IDENTITY_FIELDS):
        return Verdict('DISCONTINUITY', 'IDENTITY_TUPLE_CHANGED')
    if before.ctime_ns != after.ctime_ns:
        return Verdict('DISCONTINUITY', 'CTIME_ADVANCED')
    # ctime is the kernel-maintained "something about this inode changed"
    # signal and is coarse (jiffy granularity on hosts without multigrain
    # timestamps): an ordinary unprivileged write can land in the same ctime
    # tick as the "before" capture, so reaching here with ctime unchanged is
    # NOT proof that nothing changed. These branches exist to catch exactly
    # that real, unprivileged same-tick case (not only synthetic/injected or
    # privileged-forged tuples) whenever the content/metadata bytes
    # themselves still differ. A same-tick change that also perfectly
    # restores every one of these fields remains UNKNOWN (see
    # TUPLE_MATCHED_NOT_PROOF_OF_CONTINUITY below); that residual ambiguity
    # is accepted, not hidden.
    if any(getattr(before, field) != getattr(after, field) for field in CONTENT_FIELDS):
        return Verdict('DISCONTINUITY', 'CONTENT_CHANGED_WITHOUT_CTIME_ADVANCE')
    if any(getattr(before, field) != getattr(after, field) for field in METADATA_FIELDS):
        return Verdict('DISCONTINUITY', 'METADATA_CHANGED_WITHOUT_CTIME_ADVANCE')
    return Verdict('UNKNOWN', 'TUPLE_MATCHED_NOT_PROOF_OF_CONTINUITY')


def evaluate_sequence(witnesses):
    """Pure pairwise fold over an ordered witness sequence (>= 2 captures).

    Compares every consecutive pair, not only the first and last, so a change
    hidden between two intermediate captures (a between-children or
    within-commit change) is still caught. The first `DISCONTINUITY` found
    short-circuits with its position. If no pair detects a discontinuity but
    at least one pair involved a missing/unsupported witness (a gap in the
    sequence), the result is `UNKNOWN / WITNESS_UNSUPPORTED_OR_MISSING`
    regardless of what later, fully-matching pairs reported, so a gap is
    never misreported as a matched run. Otherwise the fold result is
    whatever the last pair returned (`UNKNOWN` unless that final pair itself
    detected a change). No all-matching run is ever reported as proven
    continuity.
    """
    if not isinstance(witnesses, (list, tuple)) or not 2 <= len(witnesses) <= MAX_SEQUENCE:
        return Verdict('UNKNOWN', 'WITNESS_SEQUENCE_BOUND')
    gap = False
    last = None
    for index, (before, after) in enumerate(zip(witnesses, witnesses[1:])):
        last = compare_witness(before, after)
        if last.status == 'DISCONTINUITY':
            return Verdict(last.status, last.code, index=index + 1)
        if last.code == 'WITNESS_UNSUPPORTED_OR_MISSING':
            gap = True
    if gap:
        return Verdict('UNKNOWN', 'WITNESS_UNSUPPORTED_OR_MISSING')
    return last
