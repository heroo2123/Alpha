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
  or safe for any protected or caller-selected path.
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
"""

from dataclasses import dataclass
from pathlib import Path
import fcntl
import hashlib
import os
import re
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


def _unescape_mountinfo(value):
    return re.sub(r'\\([0-7]{3})', lambda match: chr(int(match[1], 8)), value)


def _mount_entry(resolved):
    """Best-effort longest-mountpoint-prefix match; never raises on parse issues.

    Returns (mount_id, mount_point, fstype, major, minor) or None. This is a
    diagnostic best-effort lookup, not a proof that no other mount could ever
    shadow this path; see the module docstring and the amendment's explicit
    "mount overlay/removal... may evade endpoint tuples" limitation.
    """
    best = None
    try:
        lines = Path('/proc/self/mountinfo').read_text().splitlines()
    except OSError:
        return None
    for line in lines:
        before, separator, after = line.partition(' - ')
        if not separator:
            continue
        fields = before.split()
        tail = after.split()
        if len(fields) < 5 or not tail:
            continue
        mount_point = _unescape_mountinfo(fields[4])
        if not (str(resolved) == mount_point
                or str(resolved).startswith(mount_point.rstrip('/') + '/')
                or mount_point == '/'):
            continue
        if best is not None and len(mount_point) <= len(best[1]):
            continue
        try:
            mount_id = int(fields[0])
            major, minor = (int(part) for part in fields[2].split(':'))
        except ValueError:
            continue
        best = (mount_id, mount_point, tail[0], major, minor)
    return best


def read_file_witness(path):
    """Capture one immutable identity/content tuple for a plain regular file.

    Structural or unsupported conditions return `Unsupported(code)` rather
    than raising or guessing; callers must treat that exactly like any other
    undetectable case (see `compare_witness`), never as a passing witness.
    """
    p = Path(path)
    try:
        if p.is_symlink() or p.parent.is_symlink():
            return Unsupported('WITNESS_SYMLINK_REFUSED')
        before = p.stat()
        if not stat_module.S_ISREG(before.st_mode):
            return Unsupported('WITNESS_NOT_REGULAR_FILE')
        if before.st_nlink != 1:
            return Unsupported('WITNESS_MULTILINK_REFUSED')
        if before.st_size > MAX_WITNESS_BYTES:
            return Unsupported('WITNESS_BYTE_BOUND')
        fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return Unsupported('WITNESS_OPEN_FAILED')
    try:
        with os.fdopen(fd, 'rb') as f:
            opened = os.fstat(f.fileno())
            if (opened.st_dev, opened.st_ino, opened.st_size) != \
               (before.st_dev, before.st_ino, before.st_size):
                return Unsupported('WITNESS_CHANGED_BEFORE_READ')
            digest = hashlib.sha256()
            total = 0
            for block in iter(lambda: f.read(256 * 1024), b''):
                total += len(block)
                if total > MAX_WITNESS_BYTES:
                    return Unsupported('WITNESS_BYTE_BOUND')
                digest.update(block)
            after = os.fstat(f.fileno())
            if (after.st_dev, after.st_ino, after.st_mtime_ns, after.st_ctime_ns, after.st_size) != \
               (before.st_dev, before.st_ino, before.st_mtime_ns, before.st_ctime_ns, before.st_size):
                return Unsupported('WITNESS_CHANGED_DURING_READ')
            try:
                buf = fcntl.ioctl(f.fileno(), FS_IOC_GETVERSION, struct.pack('L', 0))
                generation, = struct.unpack('L', buf)
            except (OSError, struct.error):
                return Unsupported('WITNESS_GENERATION_UNSUPPORTED')
    except OSError:
        return Unsupported('WITNESS_READ_FAILED')
    try:
        parent_stat = p.parent.stat()
    except OSError:
        return Unsupported('WITNESS_PARENT_STAT_FAILED')
    entry = _mount_entry(p.resolve())
    if entry is None:
        return Unsupported('WITNESS_MOUNT_UNRESOLVED')
    mount_id, _mount_point, fstype, major, minor = entry
    if fstype not in SUPPORTED_FSTYPES:
        return Unsupported('WITNESS_MOUNT_UNSUPPORTED')
    if (major, minor) != (os.major(after.st_dev), os.minor(after.st_dev)):
        return Unsupported('WITNESS_MOUNT_DEVICE_MISMATCH')
    return FileWitness(
        dev=after.st_dev, ino=after.st_ino, generation=generation,
        mode=stat_module.S_IMODE(after.st_mode), uid=after.st_uid, gid=after.st_gid,
        nlink=after.st_nlink, size=after.st_size, ctime_ns=after.st_ctime_ns,
        mtime_ns=after.st_mtime_ns, sha256=digest.hexdigest(), mount_id=mount_id,
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
    # signal and cannot be set directly by an unprivileged utime() call, unlike
    # mtime. Reaching here with ctime unchanged means a real filesystem could
    # not have produced a content/metadata change; these branches only exist
    # as a defensive check against synthetic/injected or privileged-forged
    # tuples, per the amendment's "privileged timestamp/metadata manipulation
    # may evade endpoint tuples" limitation.
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
    short-circuits with its position; otherwise the fold result is whatever
    the last pair returned (`UNKNOWN` unless that final pair itself detected
    a change). No all-matching run is ever reported as proven continuity.
    """
    if not isinstance(witnesses, (list, tuple)) or not 2 <= len(witnesses) <= MAX_SEQUENCE:
        return Verdict('UNKNOWN', 'WITNESS_SEQUENCE_BOUND')
    last = None
    for index, (before, after) in enumerate(zip(witnesses, witnesses[1:])):
        last = compare_witness(before, after)
        if last.status == 'DISCONTINUITY':
            return Verdict(last.status, last.code, index=index + 1)
    return last
