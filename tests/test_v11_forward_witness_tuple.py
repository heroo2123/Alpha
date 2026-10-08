"""Offline tmpdir-only tests for the unused temporary-file witness comparator.

No protected path, database, network, provider, credential, account or
financial surface is touched. These tests never import or call
shadow_commission; a separate assertion below pins that its unconditional
gate text is unchanged by this slice, per
docs/V11_FORWARD_PROTECTED_JOURNAL_AMENDMENT_20261007.md §7's "Unconditional
gate and regressions" requirement.
"""

import fcntl
import inspect
import os
import struct
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
    deliberately use injected tuples instead of real timing)."""
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

    monkeypatch.setattr(witness, '_mount_entry',
                         lambda resolved: (99, str(tmp_path), 'tmpfs', 0, 99))
    result = witness.read_file_witness(path)
    check(result == witness.Unsupported('WITNESS_MOUNT_UNSUPPORTED'), f'{result}')


def test_unresolved_mount_is_unknown(tmp_path, monkeypatch):
    path = tmp_path / 'f'
    write(path, b'hello')
    monkeypatch.setattr(witness, '_mount_entry', lambda resolved: None)
    result = witness.read_file_witness(path)
    check(result == witness.Unsupported('WITNESS_MOUNT_UNRESOLVED'), f'{result}')


def test_symlink_path_is_unsupported(tmp_path):
    target = tmp_path / 'f'
    write(target, b'hello')
    link = tmp_path / 'link'
    os.symlink(target, link)
    result = witness.read_file_witness(link)
    check(result == witness.Unsupported('WITNESS_SYMLINK_REFUSED'), f'{result}')


def test_directory_path_is_unsupported(tmp_path):
    result = witness.read_file_witness(tmp_path)
    check(result == witness.Unsupported('WITNESS_NOT_REGULAR_FILE'), f'{result}')


def test_oversized_file_is_unsupported(tmp_path, monkeypatch):
    path = tmp_path / 'f'
    write(path, b'hello')
    monkeypatch.setattr(witness, 'MAX_WITNESS_BYTES', 2)
    result = witness.read_file_witness(path)
    check(result == witness.Unsupported('WITNESS_BYTE_BOUND'), f'{result}')


def test_missing_path_is_unsupported(tmp_path):
    result = witness.read_file_witness(tmp_path / 'absent')
    check(result == witness.Unsupported('WITNESS_OPEN_FAILED'), f'{result}')


def test_hardlinked_file_is_unsupported(tmp_path):
    path = tmp_path / 'f'
    write(path, b'hello')
    link = tmp_path / 'g'
    os.link(path, link)
    result = witness.read_file_witness(path)
    check(result == witness.Unsupported('WITNESS_MULTILINK_REFUSED'), f'{result}')


# --- same-tick ambiguity: injected tuples, since a real undetectable ABA is
# by definition indistinguishable from "nothing happened" and cannot be
# physically engineered as a separate observable case ---

def test_same_tick_adversarial_restore_is_unknown_not_proven_continuous():
    # An attacker who changes content and perfectly restores every witnessed
    # field within one clock tick produces a tuple indistinguishable from "no
    # change". This must never be reported as proven continuity.
    observed_after_hidden_change = synthetic_witness()
    observed_before = synthetic_witness()
    verdict = witness.compare_witness(observed_before, observed_after_hidden_change)
    check(verdict == witness.Verdict('UNKNOWN', 'TUPLE_MATCHED_NOT_PROOF_OF_CONTINUITY'),
          f'OVERCLAIMED_CONTINUITY_{verdict}')


def test_privileged_forged_content_change_without_ctime_advance_still_flagged():
    # Out of normal-syscall reach (ctime cannot be set directly by an
    # unprivileged caller), but the comparator must not silently trust ctime
    # alone if such a tuple is ever presented to it (defense in depth; see
    # module docstring on privileged timestamp/metadata manipulation).
    before = synthetic_witness(sha256='a' * 64, size=5)
    forged = synthetic_witness(sha256='b' * 64, size=5)
    verdict = witness.compare_witness(before, forged)
    check(verdict == witness.Verdict('DISCONTINUITY', 'CONTENT_CHANGED_WITHOUT_CTIME_ADVANCE'),
          f'{verdict}')


def test_unsupported_or_missing_input_types_are_unknown():
    check(witness.compare_witness(None, synthetic_witness())
          == witness.Verdict('UNKNOWN', 'WITNESS_UNSUPPORTED_OR_MISSING'), 'EXPECTED_UNKNOWN')
    check(witness.compare_witness(witness.Unsupported('X'), witness.Unsupported('X'))
          == witness.Verdict('UNKNOWN', 'WITNESS_UNSUPPORTED_OR_MISSING'), 'EXPECTED_UNKNOWN')


# --- no I/O/network/account/financial side effect ---

def test_no_socket_or_subprocess_surface_imported():
    import socket  # noqa: F401 (import kept local to avoid widening module surface)
    source = inspect.getsource(witness)
    for forbidden in ('socket.', 'subprocess', 'requests', 'urllib'):
        check(forbidden not in source, f'UNEXPECTED_{forbidden.upper()}_REFERENCE')


if __name__ == '__main__':
    raise SystemExit(pytest.main([__file__, '-v']))
