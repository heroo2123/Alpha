"""Offline tmpdir-only tests for the unused temporary-file witness comparator.

No protected path, database, network, provider, credential, account or
financial surface is touched. These tests never import or call
shadow_commission; a separate assertion below pins that its unconditional
gate text is unchanged by this slice, per
docs/V11_FORWARD_PROTECTED_JOURNAL_AMENDMENT_20261007.md §7's "Unconditional
gate and regressions" requirement.
"""

import ast
import errno
import fcntl
import inspect
import os
import time

import pytest

from polymarket_scanner.v11 import forward_witness_tuple as witness
from polymarket_scanner.v11 import shadow_commission


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def barrier():
    """Deterministic spacing so consecutive real operations land in distinct
    ctime/mtime ticks; avoids flaky same-tick coincidences in tests that must
    detect a change (as opposed to the dedicated ambiguity tests below, which
    deliberately use injected tuples or an unbounded trial count instead of
    real timing)."""
    time.sleep(0.05)


def write(path, data):
    with open(path, 'wb') as f:
        f.write(data)


def synthetic_witness(**overrides):
    base = dict(dev=1, ino=100, generation=1, mode=0o600, uid=1000, gid=1000,
                nlink=1, size=5, ctime_ns=1_000_000_000, mtime_ns=1_000_000_000,
                sha256='a' * 64, mount_id=30, mount_fstype='ext4', mount_major=253,
                mount_minor=2, parent_dev=1, parent_ino=10)
    base.update(overrides)
    return witness.FileWitness(**base)


class _ReadOverride:
    """Wraps a real `os.fdopen`-returned file object, replacing only `read`
    so a test can inject a short read, mid-read growth, or a side effect
    (such as a concurrent hardlink) on the first call."""

    def __init__(self, inner, override):
        self._inner = inner
        self._override = override
        self._calls = 0

    def __enter__(self):
        self._inner.__enter__()
        return self

    def __exit__(self, *exc_info):
        return self._inner.__exit__(*exc_info)

    def fileno(self):
        return self._inner.fileno()

    def read(self, n):
        self._calls += 1
        return self._override(self._inner, self._calls, n)


# --- gate regression (F7-style pin on this slice; no wiring, no runtime import) ---

def test_protected_interval_gate_unconditional_and_unimported():
    source = inspect.getsource(shadow_commission._require_protected_interval)
    check("raise EvidenceError('FORWARD_PROTECTED_INTERVAL_UNPROVEN')" in source,
          'GATE_TEXT_CHANGED')
    check('forward_witness_tuple' not in inspect.getsource(shadow_commission),
          'UNEXPECTED_WIRING_INTO_SHADOW_COMMISSION')


# --- normal matched tuple: never reported as proven continuity ---

def test_unchanged_real_file_is_unknown_not_proven_continuous(tmp_path):
    path = tmp_path / 'f'
    write(path, b'hello')
    before = witness.read_file_witness(path)
    after = witness.read_file_witness(path)
    check(isinstance(before, witness.FileWitness), 'EXPECTED_WITNESS')
    verdict = witness.compare_witness(before, after)
    check(verdict == witness.Verdict('UNKNOWN', 'TUPLE_MATCHED_NOT_PROOF_OF_CONTINUITY'),
          f'UNEXPECTED_{verdict}')


def test_real_mount_resolution_matches_device_for_tmp_path(tmp_path):
    path = tmp_path / 'f'
    write(path, b'hello')
    result = witness.read_file_witness(path)
    check(isinstance(result, witness.FileWitness), f'EXPECTED_WITNESS_{result}')
    st = os.stat(path)
    check((result.mount_major, result.mount_minor) == (os.major(st.st_dev), os.minor(st.st_dev)),
          f'MOUNT_DEVICE_MISMATCH_{result}')


# --- replace/restore and same-content new-inode ABA ---

def test_same_content_replace_via_unlink_recreate_is_discontinuous(tmp_path):
    # Whether the allocator reuses the freed inode number is filesystem/kernel
    # state-dependent and not something this test may assume either way; the
    # identity tuple (inode number *and* generation together) must still
    # catch the swap regardless of which field actually moved.
    path = tmp_path / 'f'
    write(path, b'hello')
    before = witness.read_file_witness(path)
    barrier()
    os.unlink(path)
    write(path, b'hello')
    after = witness.read_file_witness(path)
    check((before.ino, before.generation) != (after.ino, after.generation),
          'TEST_ASSUMPTION_SOME_IDENTITY_FIELD_SHOULD_DIFFER')
    verdict = witness.compare_witness(before, after)
    check(verdict.status == 'DISCONTINUITY', f'UNDETECTED_REUSE_ABA_{verdict}')
    check(verdict.code == 'IDENTITY_TUPLE_CHANGED', verdict.code)


def test_atomic_replace_and_restore_is_discontinuous(tmp_path):
    path = tmp_path / 'f'
    write(path, b'hello')
    before = witness.read_file_witness(path)
    barrier()
    staged = tmp_path / 'f.new'
    write(staged, b'hello')
    os.replace(staged, path)
    after = witness.read_file_witness(path)
    verdict = witness.compare_witness(before, after)
    check(verdict.status == 'DISCONTINUITY', f'UNDETECTED_REPLACE_ABA_{verdict}')


# --- same-inode rewrite with utime reset ---

def test_same_inode_rewrite_with_utime_reset_is_discontinuous(tmp_path):
    path = tmp_path / 'f'
    write(path, b'hello')
    before_stat = os.stat(path)
    before = witness.read_file_witness(path)
    barrier()
    with open(path, 'r+b') as f:
        f.write(b'HELLO')
    os.utime(path, ns=(before_stat.st_atime_ns, before_stat.st_mtime_ns))
    after = witness.read_file_witness(path)
    check(before.ino == after.ino, 'TEST_ASSUMPTION_SAME_INODE')
    check(before.mtime_ns == after.mtime_ns, 'TEST_ASSUMPTION_MTIME_RESET')
    verdict = witness.compare_witness(before, after)
    check(verdict.status == 'DISCONTINUITY', f'UTIME_RESET_EVADED_DETECTION_{verdict}')
    check(verdict.code == 'CTIME_ADVANCED', verdict.code)


# --- rename away and back ---

def test_rename_away_and_back_is_discontinuous(tmp_path):
    path = tmp_path / 'f'
    write(path, b'hello')
    before = witness.read_file_witness(path)
    barrier()
    away = tmp_path / 'f.away'
    os.rename(path, away)
    os.rename(away, path)
    after = witness.read_file_witness(path)
    check(before.ino == after.ino, 'TEST_ASSUMPTION_SAME_INODE_ON_RENAME')
    verdict = witness.compare_witness(before, after)
    check(verdict.status == 'DISCONTINUITY', f'RENAME_ABA_EVADED_DETECTION_{verdict}')
    check(verdict.code == 'CTIME_ADVANCED', verdict.code)


# --- parent swap ---

def test_parent_directory_swap_is_discontinuous(tmp_path):
    d = tmp_path / 'd'
    d.mkdir()
    path = d / 'f'
    write(path, b'hello')
    barrier()
    before = witness.read_file_witness(path)
    barrier()
    old = tmp_path / 'd_old'
    os.rename(d, old)
    d.mkdir()
    os.rename(old / 'f', path)
    after = witness.read_file_witness(path)
    check(before.parent_ino != after.parent_ino, 'TEST_ASSUMPTION_PARENT_CHANGED')
    verdict = witness.compare_witness(before, after)
    check(verdict.status == 'DISCONTINUITY', f'PARENT_SWAP_EVADED_DETECTION_{verdict}')
    check(verdict.code == 'IDENTITY_TUPLE_CHANGED', verdict.code)


def test_parent_identity_bound_to_opened_directory_not_renamed_path(tmp_path, monkeypatch):
    # The parent directory is renamed away *during* a single read_file_witness
    # call (mid-capture, after the directory fd is opened but before the old
    # path-based parent re-stat would have run). The reported parent must
    # stay bound to the directory that actually held the file at open time,
    # not to whatever now sits at that path.
    d = tmp_path / 'd'
    d.mkdir()
    path = d / 'f'
    write(path, b'hello')
    original_parent_ino = os.stat(d).st_ino
    real_ioctl = fcntl.ioctl

    def swapping_ioctl(*args, **kwargs):
        os.rename(d, tmp_path / 'd_old')
        d.mkdir()
        return real_ioctl(*args, **kwargs)

    monkeypatch.setattr(fcntl, 'ioctl', swapping_ioctl)
    result = witness.read_file_witness(path)
    check(isinstance(result, witness.FileWitness), f'EXPECTED_WITNESS_{result}')
    check(result.parent_ino == original_parent_ino,
          f'PARENT_IDENTITY_NOT_BOUND_TO_OPENED_DIR_{result.parent_ino}')
    check(not (tmp_path / 'd' / 'f').exists(), 'TEST_ASSUMPTION_NEW_DIR_EMPTY')


# --- between-child and within-commit sequence coverage ---

def test_sequence_detects_change_hidden_between_middle_captures():
    start = synthetic_witness(ino=100, generation=1, ctime_ns=1, mtime_ns=1, sha256='a' * 64)
    middle = synthetic_witness(ino=200, generation=1, ctime_ns=2, mtime_ns=2, sha256='b' * 64)
    end = synthetic_witness(ino=100, generation=1, ctime_ns=1, mtime_ns=1, sha256='a' * 64)
    # Endpoint-only comparison would miss the hidden swap-and-restore.
    endpoint_only = witness.compare_witness(start, end)
    check(endpoint_only == witness.Verdict('UNKNOWN', 'TUPLE_MATCHED_NOT_PROOF_OF_CONTINUITY'),
          f'TEST_SETUP_EXPECTED_ENDPOINTS_TO_MATCH_{endpoint_only}')
    verdict = witness.evaluate_sequence([start, middle, end])
    check(verdict.status == 'DISCONTINUITY', f'SEQUENCE_MISSED_BETWEEN_CHILD_CHANGE_{verdict}')
    check(verdict.index == 1, f'WRONG_SEQUENCE_POSITION_{verdict.index}')


def test_sequence_detects_change_within_final_commit_step():
    opening = synthetic_witness(ino=1, generation=1, ctime_ns=1, mtime_ns=1)
    pre_seal = synthetic_witness(ino=1, generation=1, ctime_ns=1, mtime_ns=1)
    post_commit = synthetic_witness(ino=1, generation=2, ctime_ns=2, mtime_ns=2)
    verdict = witness.evaluate_sequence([opening, pre_seal, post_commit])
    check(verdict.status == 'DISCONTINUITY', f'SEQUENCE_MISSED_WITHIN_COMMIT_CHANGE_{verdict}')
    check(verdict.index == 2, f'WRONG_SEQUENCE_POSITION_{verdict.index}')


def test_sequence_with_no_detected_change_is_unknown_not_proven():
    same = synthetic_witness()
    verdict = witness.evaluate_sequence([same, same, same])
    check(verdict == witness.Verdict('UNKNOWN', 'TUPLE_MATCHED_NOT_PROOF_OF_CONTINUITY'),
          f'UNEXPECTED_{verdict}')


def test_sequence_with_unsupported_gap_reports_gap_not_matched():
    gap = witness.Unsupported('WITNESS_OPEN_FAILED')
    same = synthetic_witness()
    verdict = witness.evaluate_sequence([gap, same, same])
    check(verdict == witness.Verdict('UNKNOWN', 'WITNESS_UNSUPPORTED_OR_MISSING'),
          f'GAP_MISREPORTED_AS_MATCHED_{verdict}')


def test_sequence_with_gap_between_changed_witnesses_stays_unknown():
    # A change hidden entirely behind a missing/unsupported capture cannot be
    # detected by a pairwise fold; this must stay UNKNOWN, never proof, and
    # never silently reported as a clean match either (covered above).
    gap = witness.Unsupported('WITNESS_OPEN_FAILED')
    start = synthetic_witness(ino=1)
    changed = synthetic_witness(ino=2, ctime_ns=2)
    verdict = witness.evaluate_sequence([start, gap, changed])
    check(verdict == witness.Verdict('UNKNOWN', 'WITNESS_UNSUPPORTED_OR_MISSING'), f'{verdict}')


def test_sequence_bound_rejects_short_or_oversized_input():
    check(witness.evaluate_sequence([synthetic_witness()]).code == 'WITNESS_SEQUENCE_BOUND',
          'SINGLE_ELEMENT_SHOULD_BE_BOUND_REFUSAL')
    check(witness.evaluate_sequence([synthetic_witness()] * (witness.MAX_SEQUENCE + 1)).code
          == 'WITNESS_SEQUENCE_BOUND', 'OVERSIZED_SEQUENCE_SHOULD_BE_BOUND_REFUSAL')


# --- unsupported generation / unsupported mount ---

def test_unsupported_generation_ioctl_is_unknown(tmp_path, monkeypatch):
    path = tmp_path / 'f'
    write(path, b'hello')

    def raise_enotty(*args, **kwargs):
        raise OSError(25, 'Inappropriate ioctl for device')

    monkeypatch.setattr(fcntl, 'ioctl', raise_enotty)
    result = witness.read_file_witness(path)
    check(isinstance(result, witness.Unsupported), 'EXPECTED_UNSUPPORTED')
    check(result.code == 'WITNESS_GENERATION_UNSUPPORTED', result.code)
    verdict = witness.compare_witness(result, result)
    check(verdict == witness.Verdict('UNKNOWN', 'WITNESS_UNSUPPORTED_OR_MISSING'), f'{verdict}')


def test_unsupported_mount_fstype_is_unknown(tmp_path, monkeypatch):
    path = tmp_path / 'f'
    write(path, b'hello')
    monkeypatch.setattr(witness, '_mount_entry_for_id', lambda mnt_id: ('tmpfs', 0, 99))
    result = witness.read_file_witness(path)
    check(result == witness.Unsupported('WITNESS_MOUNT_UNSUPPORTED'), f'{result}')


def test_unresolved_mount_is_unknown(tmp_path, monkeypatch):
    path = tmp_path / 'f'
    write(path, b'hello')
    monkeypatch.setattr(witness, '_mount_entry_for_id', lambda mnt_id: None)
    result = witness.read_file_witness(path)
    check(result == witness.Unsupported('WITNESS_MOUNT_UNRESOLVED'), f'{result}')


def test_fd_mount_id_unavailable_is_unknown(tmp_path, monkeypatch):
    path = tmp_path / 'f'
    write(path, b'hello')
    monkeypatch.setattr(witness, '_fd_mount_id', lambda fd: None)
    result = witness.read_file_witness(path)
    check(result == witness.Unsupported('WITNESS_MOUNT_UNRESOLVED'), f'{result}')


def test_mount_entry_for_id_matches_exact_id_not_shadowed_mountpoint(monkeypatch):
    # Two entries share the mount point /mnt; a path-prefix matcher (the
    # prior implementation) always kept the first, shadowed one. Matching by
    # exact mount ID must be able to select either, correctly, by ID alone.
    info = ("22 1 253:2 / / rw - ext4 /dev/vda2 rw\n"
            "40 22 253:2 /a /mnt rw - ext4 /dev/vda2 rw\n"
            "41 40 253:2 /b /mnt rw - tmpfs tmpfs rw\n")
    real_read_bytes = witness.Path.read_bytes

    def fake_read_bytes(self):
        return info.encode() if str(self) == '/proc/self/mountinfo' else real_read_bytes(self)

    monkeypatch.setattr(witness.Path, 'read_bytes', fake_read_bytes)
    check(witness._mount_entry_for_id(40) == ('ext4', 253, 2), 'EXPECTED_SHADOWED_MOUNT_BY_ID')
    check(witness._mount_entry_for_id(41) == ('tmpfs', 253, 2), 'EXPECTED_TOP_MOUNT_BY_ID')


def test_non_utf8_mountinfo_is_unresolved_not_raised(tmp_path, monkeypatch):
    path = tmp_path / 'f'
    write(path, b'hello')
    bad = b'50 1 0:9 / /mnt/\xff rw - tmpfs t rw\n'
    real_read_bytes = witness.Path.read_bytes

    def fake_read_bytes(self):
        return bad if str(self) == '/proc/self/mountinfo' else real_read_bytes(self)

    monkeypatch.setattr(witness.Path, 'read_bytes', fake_read_bytes)
    result = witness.read_file_witness(path)
    check(isinstance(result, witness.Unsupported), f'EXPECTED_UNSUPPORTED_NOT_RAISED_{result}')


def test_symlink_path_is_unsupported(tmp_path):
    target = tmp_path / 'f'
    write(target, b'hello')
    link = tmp_path / 'link'
    os.symlink(target, link)
    result = witness.read_file_witness(link)
    check(result == witness.Unsupported('WITNESS_SYMLINK_REFUSED'), f'{result}')


def test_ancestor_symlink_is_unsupported(tmp_path):
    real_dir = tmp_path / 'real'
    (real_dir / 'sub').mkdir(parents=True)
    target = real_dir / 'sub' / 'f'
    write(target, b'hello')
    link = tmp_path / 'lnk'
    os.symlink(real_dir, link)
    result = witness.read_file_witness(link / 'sub' / 'f')
    check(result == witness.Unsupported('WITNESS_SYMLINK_REFUSED'), f'{result}')


@pytest.mark.skipif(os.geteuid() == 0, reason='EACCES ancestor is not enforced for root')
def test_eacces_ancestor_is_unsupported_not_raised(tmp_path):
    # R1 regression: the lexical symlink walk's `is_symlink()` call raises
    # PermissionError for a search-denied ancestor instead of returning a
    # symlink verdict; the parent must convert that to Unsupported too.
    locked = tmp_path / 'locked'
    sub = locked / 'x'
    sub.mkdir(parents=True)
    target = sub / 'f'
    write(target, b'hello')
    os.chmod(locked, 0)
    try:
        result = witness.read_file_witness(target)
    finally:
        os.chmod(locked, 0o700)
    check(result == witness.Unsupported('WITNESS_SYMLINK_CHECK_FAILED'), f'{result}')


def test_ancestor_name_too_long_is_unsupported_not_raised(tmp_path):
    # R1 regression: `is_symlink()` raises ENAMETOOLONG for an over-length
    # path component instead of returning a symlink verdict.
    long_component = 'a' * 300
    result = witness.read_file_witness(tmp_path / long_component / 'f')
    check(result == witness.Unsupported('WITNESS_SYMLINK_CHECK_FAILED'), f'{result}')


def test_leaf_name_too_long_is_unsupported_not_raised(tmp_path):
    long_component = 'a' * 300
    result = witness.read_file_witness(tmp_path / long_component)
    check(result == witness.Unsupported('WITNESS_SYMLINK_CHECK_FAILED'), f'{result}')


def test_directory_path_is_unsupported(tmp_path):
    result = witness.read_file_witness(tmp_path)
    check(result == witness.Unsupported('WITNESS_NOT_REGULAR_FILE'), f'{result}')


def test_oversized_file_is_unsupported(tmp_path, monkeypatch):
    path = tmp_path / 'f'
    write(path, b'hello')
    monkeypatch.setattr(witness, 'MAX_WITNESS_BYTES', 2)
    result = witness.read_file_witness(path)
    check(result == witness.Unsupported('WITNESS_BYTE_BOUND'), f'{result}')


def test_in_loop_growth_past_bound_is_unsupported(tmp_path, monkeypatch):
    path = tmp_path / 'f'
    write(path, b'abc')
    monkeypatch.setattr(witness, 'MAX_WITNESS_BYTES', 3)
    real_fdopen = os.fdopen

    def grow_on_first_read(inner, call_number, n):
        if call_number == 1:
            with open(path, 'ab') as g:
                g.write(b'defgh')
        return inner.read(n)

    monkeypatch.setattr(witness.os, 'fdopen', lambda fd, mode, closefd=True:
                         _ReadOverride(real_fdopen(fd, mode, closefd=closefd), grow_on_first_read))
    result = witness.read_file_witness(path)
    check(result == witness.Unsupported('WITNESS_BYTE_BOUND'), f'{result}')


def test_short_read_is_unsupported(tmp_path, monkeypatch):
    path = tmp_path / 'f'
    write(path, b'hello')
    real_fdopen = os.fdopen

    def truncate_first_read(inner, call_number, n):
        if call_number > 1:
            return b''
        return inner.read(2)

    monkeypatch.setattr(witness.os, 'fdopen', lambda fd, mode, closefd=True:
                         _ReadOverride(real_fdopen(fd, mode, closefd=closefd), truncate_first_read))
    result = witness.read_file_witness(path)
    check(result == witness.Unsupported('WITNESS_SHORT_READ'), f'{result}')


def test_missing_path_is_unsupported(tmp_path):
    result = witness.read_file_witness(tmp_path / 'absent')
    check(result == witness.Unsupported('WITNESS_OPEN_FAILED'), f'{result}')


@pytest.mark.parametrize('failed_close', [1, 2], ids=['child', 'parent'])
def test_close_eio_after_release_is_typed_refusal(tmp_path, monkeypatch, failed_close):
    path = tmp_path / 'f'
    write(path, b'hello')
    check(isinstance(witness.read_file_witness(path), witness.FileWitness),
          'TEST_REQUIRES_SUCCESSFUL_WITNESS_BEFORE_FAULT')
    real_close = os.close
    closed = []

    def close_then_fail(fd):
        real_close(fd)
        closed.append(fd)
        if len(closed) == failed_close:
            raise OSError(errno.EIO, 'injected close EIO after release')

    with monkeypatch.context() as patch:
        patch.setattr(witness.os, 'close', close_then_fail)
        result = witness.read_file_witness(path)

    check(result == witness.Unsupported('WITNESS_OS_ERROR'), f'{result}')
    check(len(closed) == 2 and len(set(closed)) == 2,
          f'EXPECTED_ONE_CLOSE_PER_CHILD_AND_PARENT_{closed}')
    check(witness.compare_witness(result, result)
          == witness.Verdict('UNKNOWN', 'WITNESS_UNSUPPORTED_OR_MISSING'),
          'CLOSE_FAILURE_MUST_NOT_PROVE_CONTINUITY')


@pytest.mark.parametrize('failed_fstat, expected_code, expected_closes', [
    (1, 'WITNESS_PARENT_STAT_FAILED', 1),
    (2, 'WITNESS_OPEN_FAILED', 2),
], ids=['parent', 'child'])
def test_fstat_eio_retains_specific_refusal(tmp_path, monkeypatch, failed_fstat,
                                            expected_code, expected_closes):
    path = tmp_path / 'f'
    write(path, b'hello')
    real_fstat = os.fstat
    real_close = os.close
    fstat_calls = []
    closed = []

    def fstat_then_fail(fd):
        fstat_calls.append(fd)
        if len(fstat_calls) == failed_fstat:
            raise OSError(errno.EIO, 'injected fstat EIO')
        return real_fstat(fd)

    def record_close(fd):
        real_close(fd)
        closed.append(fd)

    with monkeypatch.context() as patch:
        patch.setattr(witness.os, 'fstat', fstat_then_fail)
        patch.setattr(witness.os, 'close', record_close)
        result = witness.read_file_witness(path)

    check(result == witness.Unsupported(expected_code), f'{result}')
    check(len(closed) == expected_closes and len(set(closed)) == expected_closes,
          f'EXPECTED_CLOSES_{closed}')


def test_parent_fstat_refusal_plus_parent_close_eio_is_typed(tmp_path, monkeypatch):
    path = tmp_path / 'f'
    write(path, b'hello')
    real_close = os.close
    closed = []
    fstat_calls = []

    def fail_parent_fstat(fd):
        fstat_calls.append(fd)
        raise OSError(errno.EIO, 'injected parent fstat EIO')

    def close_then_fail(fd):
        real_close(fd)
        closed.append(fd)
        raise OSError(errno.EIO, 'injected parent close EIO after release')

    with monkeypatch.context() as patch:
        patch.setattr(witness.os, 'fstat', fail_parent_fstat)
        patch.setattr(witness.os, 'close', close_then_fail)
        result = witness.read_file_witness(path)

    check(result == witness.Unsupported('WITNESS_OS_ERROR'), f'{result}')
    check(len(fstat_calls) == 1 and closed == fstat_calls,
          f'EXPECTED_ONE_PARENT_FSTAT_AND_CLOSE_{fstat_calls}_{closed}')
    check(witness.compare_witness(result, result)
          == witness.Verdict('UNKNOWN', 'WITNESS_UNSUPPORTED_OR_MISSING'),
          'DOUBLE_FAILURE_MUST_NOT_PROVE_CONTINUITY')


def test_hardlinked_file_is_unsupported(tmp_path):
    path = tmp_path / 'f'
    write(path, b'hello')
    link = tmp_path / 'g'
    os.link(path, link)
    result = witness.read_file_witness(path)
    check(result == witness.Unsupported('WITNESS_MULTILINK_REFUSED'), f'{result}')


def test_hardlink_created_during_read_is_unsupported(tmp_path, monkeypatch):
    # A hard link appears *during* the read (after the pre-open nlink==1
    # refusal already passed). The post-read recheck must still catch the
    # nlink change rather than returning a FileWitness claiming nlink=1.
    path = tmp_path / 'f'
    write(path, b'hello')
    real_fdopen = os.fdopen

    def link_on_first_read(inner, call_number, n):
        if call_number == 1:
            os.link(path, str(path) + '.linked')
        return inner.read(n)

    monkeypatch.setattr(witness.os, 'fdopen', lambda fd, mode, closefd=True:
                         _ReadOverride(real_fdopen(fd, mode, closefd=closefd), link_on_first_read))
    result = witness.read_file_witness(path)
    check(result == witness.Unsupported('WITNESS_CHANGED_DURING_READ'),
          f'MULTILINK_DURING_READ_NOT_REFUSED_{result}')


def test_fifo_swap_between_stat_and_open_does_not_hang(tmp_path, monkeypatch):
    path = tmp_path / 'f'
    write(path, b'hello')
    real_open = os.open

    def swapping_open(name, flags, *args, **kwargs):
        if kwargs.get('dir_fd') is not None and name == path.name:
            os.unlink(path)
            os.mkfifo(path)
        return real_open(name, flags, *args, **kwargs)

    monkeypatch.setattr(witness.os, 'open', swapping_open)
    started = time.monotonic()
    result = witness.read_file_witness(path)
    elapsed = time.monotonic() - started
    check(elapsed < 2.0, f'BLOCKED_FOR_{elapsed}s_INSTEAD_OF_RETURNING_PROMPTLY')
    check(isinstance(result, witness.Unsupported), f'EXPECTED_UNSUPPORTED_{result}')


def test_missing_path_is_unsupported_not_raised_when_absent():
    result = witness.read_file_witness('/nonexistent/forward-witness-prototype/absent')
    check(isinstance(result, witness.Unsupported), f'{result}')


def test_nul_byte_path_is_unsupported(tmp_path):
    result = witness.read_file_witness(str(tmp_path / 'a\x00b'))
    check(result == witness.Unsupported('WITNESS_INVALID_INPUT'), f'{result}')


def test_non_str_path_is_unsupported():
    result = witness.read_file_witness(3)
    check(result == witness.Unsupported('WITNESS_INVALID_INPUT'), f'{result}')


def test_bytes_path_is_unsupported():
    result = witness.read_file_witness(b'/tmp/x')
    check(result == witness.Unsupported('WITNESS_INVALID_INPUT'), f'{result}')


# --- same-tick ambiguity: injected tuples for the deterministic case, plus a
# real-filesystem probe since an ordinary unprivileged same-tick write is not
# a rare or synthetic edge case on hosts with coarse ctime granularity ---

def test_same_tick_adversarial_restore_is_unknown_not_proven_continuous():
    # An attacker who changes content and perfectly restores every witnessed
    # field within one clock tick produces a tuple indistinguishable from "no
    # change". This must never be reported as proven continuity.
    observed_after_hidden_change = synthetic_witness()
    observed_before = synthetic_witness()
    verdict = witness.compare_witness(observed_before, observed_after_hidden_change)
    check(verdict == witness.Verdict('UNKNOWN', 'TUPLE_MATCHED_NOT_PROOF_OF_CONTINUITY'),
          f'OVERCLAIMED_CONTINUITY_{verdict}')


def test_content_change_without_ctime_advance_is_discontinuous_synthetic():
    # Defense in depth for any tuple presented with ctime unchanged but
    # content changed, whether that tuple came from a real same-tick write
    # (see the real-filesystem test below) or was synthetic/injected: the
    # comparator must not silently trust ctime alone.
    before = synthetic_witness(sha256='a' * 64, size=5)
    forged = synthetic_witness(sha256='b' * 64, size=5)
    verdict = witness.compare_witness(before, forged)
    check(verdict == witness.Verdict('DISCONTINUITY', 'CONTENT_CHANGED_WITHOUT_CTIME_ADVANCE'),
          f'{verdict}')


def test_real_same_tick_inplace_write_never_overclaims_continuity(tmp_path):
    # An unprivileged in-place pwrite can land in the same (coarse) ctime
    # tick as the "before" capture often enough that this is not a rare
    # corner case on this kind of host. Whichever side of the tick it lands
    # on, the comparator must only ever report DISCONTINUITY (content change
    # caught despite ctime not advancing) or UNKNOWN (TUPLE_MATCHED, i.e. the
    # same-tick change happened to restore every witnessed field) -- never a
    # third "proven continuous" outcome, and never a crash. If the write
    # instead lands in the next tick, ctime legitimately advances and that
    # is DISCONTINUITY/CTIME_ADVANCED, equally acceptable.
    allowed = {('DISCONTINUITY', 'CONTENT_CHANGED_WITHOUT_CTIME_ADVANCE'),
               ('DISCONTINUITY', 'CTIME_ADVANCED'),
               ('UNKNOWN', 'TUPLE_MATCHED_NOT_PROOF_OF_CONTINUITY')}
    for i in range(50):
        path = tmp_path / f'st{i}'
        write(path, b'hello')
        before = witness.read_file_witness(path)
        check(isinstance(before, witness.FileWitness), 'EXPECTED_WITNESS')
        fd = os.open(path, os.O_WRONLY)
        try:
            os.pwrite(fd, b'HELLO', 0)
        finally:
            os.close(fd)
        after = witness.read_file_witness(path)
        check(isinstance(after, witness.FileWitness), 'EXPECTED_WITNESS')
        verdict = witness.compare_witness(before, after)
        check((verdict.status, verdict.code) in allowed,
              f'OVERCLAIMED_OR_UNEXPECTED_VERDICT_{verdict}')


def test_unsupported_or_missing_input_types_are_unknown():
    check(witness.compare_witness(None, synthetic_witness())
          == witness.Verdict('UNKNOWN', 'WITNESS_UNSUPPORTED_OR_MISSING'), 'EXPECTED_UNKNOWN')
    check(witness.compare_witness(witness.Unsupported('X'), witness.Unsupported('X'))
          == witness.Verdict('UNKNOWN', 'WITNESS_UNSUPPORTED_OR_MISSING'), 'EXPECTED_UNKNOWN')


# --- no I/O/network/account/financial side effect ---

def test_no_socket_or_subprocess_surface_imported():
    tree = ast.parse(inspect.getsource(witness))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split('.')[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split('.')[0])
    forbidden = imported & {'socket', 'subprocess', 'requests', 'urllib'}
    check(not forbidden, f'UNEXPECTED_IMPORT_{forbidden}')
    source = inspect.getsource(witness)
    for name in ('socket.', 'subprocess', 'requests', 'urllib'):
        check(name not in source, f'UNEXPECTED_{name.upper()}_REFERENCE')


if __name__ == '__main__':
    raise SystemExit(pytest.main([__file__, '-v']))
