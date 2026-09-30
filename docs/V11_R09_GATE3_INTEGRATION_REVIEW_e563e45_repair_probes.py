"""Independent exact-candidate acceptance. Synthetic evidence-directory fixtures only.
No author implementation is modified. Faults and path races run in owned children.
"""
import copy
import errno
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import select
import signal
import stat
import time

import pytest
from tools import v11_r09_gate3_launch as launch
from tools import v11_r09_gate3_offline_io as io

R = Path(launch.__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('independent_fixture_source', R / 'tests/test_v11_r09_gate3_launch.py')
author = importlib.util.module_from_spec(spec)
spec.loader.exec_module(author)


def child(action, timeout=5):
    read_fd, write_fd = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(read_fd)
        try:
            try:
                result = {'ok': action()}
            except BaseException as exc:
                result = {'failure': type(exc).__name__, 'reason': str(exc)}
            os.write(write_fd, json.dumps(result).encode() + b'\n')
        finally:
            os._exit(0)
    os.close(write_fd)
    try:
        data = b''
        deadline = time.monotonic() + timeout
        while b'\n' not in data:
            remaining = deadline - time.monotonic()
            assert remaining > 0 and select.select([read_fd], [], [], remaining)[0], 'owned child timed out'
            chunk = os.read(read_fd, 65536)
            assert chunk, 'owned child exited without full diagnostic'
            data += chunk
        result = json.loads(data)
        assert 'failure' not in result, result
        return result['ok']
    finally:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        os.waitpid(pid, 0)
        os.close(read_fd)


def budget_state(b):
    return copy.deepcopy({k: getattr(b, k) for k in ('events', 'prev', 'count', 'received', 'reserved', 'in_flight', 'violated', 'window_started', 'last_started', 'attempts', 'failed', 'uncertain_received_bytes')})


def store_open(root):
    return io.VersionedImmutableObjectStore(root, manifest_sha256='a'*64,
        policy_sha256='b'*64, build_id='independent', clock_method='synthetic',
        max_clock_age_seconds=10, host_id='synthetic-host', boot_id='synthetic-boot')


@pytest.mark.parametrize('state', ['idle', 'active', 'partial', 'complete', 'violated', 'poisoned'])
def test_inherited_budget_all_states_before_io_and_cleanup(tmp_path, state):
    journal = tmp_path / 'budget'; journal.mkdir(mode=0o700)
    root = tmp_path / 'store'; root.mkdir(mode=0o700); (root/'objects').mkdir(mode=0o700)
    b = launch.DurableBudget(journal, 'a'*64, max_bytes=4)
    s = store_open(root)  # Real composed order: budget, then store.
    try:
        if state != 'idle': b.reserve('parent', 2, started_monotonic=1)
        if state in ('partial', 'complete'): b.consume('parent', b'A')
        if state == 'complete': b.complete('parent')
        if state == 'violated':
            with pytest.raises(launch.LaunchContractError, match='STREAM_ABORT_AT_ALLOWANCE'):
                b.consume('parent', b'ABC')
        if state == 'poisoned':
            with pytest.MonkeyPatch.context() as m:
                real_write = os.write
                m.setattr(os, 'write', lambda fd, data: real_write(fd, data[:7]))
                with pytest.raises(launch.LaunchContractError, match='SHORT_WRITE'):
                    b.consume('parent', b'A')
        before = budget_state(b); raw = (journal/'gate3.jsonl').read_bytes()
        parent_fds = [b.fd, b.lock_fd, b.dir_fd]
        offsets = [os.lseek(fd, 0, os.SEEK_CUR) for fd in parent_fds]
        def inherited():
            child_before = budget_state(b)
            with pytest.raises(launch.LaunchContractError, match='STORE_OWNER_PROCESS'):
                s.read_receipt(None)
            def forbidden(*args, **kwargs):
                raise AssertionError('inherited operation reached filesystem IO')
            with pytest.MonkeyPatch.context() as m:
                for name in ('fstat', 'stat', 'lstat', 'read', 'write', 'fsync'):
                    m.setattr(os, name, forbidden)
                m.setattr(fcntl, 'flock', forbidden)
                for action in (lambda: b.reserve('child', 1, started_monotonic=4),
                               lambda: b.next_read_limit(1),
                               lambda: b.consume('parent', b'XYZ'),
                               lambda: b.consume(None, None),
                               lambda: b.complete('parent'),
                               lambda: b._append({'op': 'complete', 'key': 'parent'})):
                    with pytest.raises(launch.LaunchContractError, match='^JOURNAL_OWNER_PROCESS$'):
                        action()
            assert budget_state(b) == child_before
            b.close(); b.close(); s.close(); s.close()
            for fd in parent_fds:
                with pytest.raises(OSError) as exc: os.fstat(fd)
                assert exc.value.errno == errno.EBADF
            assert all(getattr(b, n) is None for n in ('fd', 'lock_fd', 'dir_fd'))
            with pytest.raises(launch.LaunchContractError, match='JOURNAL_OWNER_PROCESS'):
                b.consume('parent', b'Z')
            with pytest.raises(launch.LaunchContractError, match='JOURNAL_CONCURRENT_WRITER'):
                launch.DurableBudget(journal, 'a'*64, max_bytes=4)
            with pytest.raises(BlockingIOError): store_open(root)
            return {'owner_rejected': True, 'closed_fds': len(parent_fds), 'state_unchanged': True}
        assert child(inherited)['closed_fds'] == 3
        assert budget_state(b) == before
        assert (journal/'gate3.jsonl').read_bytes() == raw
        assert [os.lseek(fd, 0, os.SEEK_CUR) for fd in parent_fds] == offsets
        with pytest.raises(launch.LaunchContractError, match='JOURNAL_CONCURRENT_WRITER'):
            launch.DurableBudget(journal, 'a'*64, max_bytes=4)
        with pytest.raises(BlockingIOError): store_open(root)
        if state in ('idle', 'complete'):
            b.reserve('after_child', 1, started_monotonic=4); b.consume('after_child', b'B'); b.complete('after_child')
        elif state in ('active', 'partial'):
            b.consume('parent', b'B'); b.complete('parent')
        else:
            with pytest.raises(launch.LaunchContractError, match='DURABILITY_UNCERTAIN' if state == 'poisoned' else 'STREAM_VIOLATION_HELD'):
                b.reserve('after_child', 1, started_monotonic=4)
        final = budget_state(b)
    finally:
        s.close(); b.close()
    raw_after = (journal/'gate3.jsonl').read_bytes()
    if state == 'poisoned':
        with pytest.raises(launch.LaunchContractError, match='JOURNAL_TORN_RECORD'):
            launch.DurableBudget(journal, 'a'*64, max_bytes=4)
    else:
        with launch.DurableBudget(journal, 'a'*64, max_bytes=4) as reopened:
            assert budget_state(reopened) == final
            if state == 'violated':
                with pytest.raises(launch.LaunchContractError, match='STREAM_VIOLATION_HELD'):
                    reopened.reserve('restart', 1, started_monotonic=6)
    assert (journal/'gate3.jsonl').read_bytes() == raw_after


def replace_object(path, kind, original):
    saved = path.with_name(path.name + '.preserved')
    path.rename(saved)  # Keep original rejected evidence and pin old inode.
    if kind == 'fifo': os.mkfifo(path, 0o600)
    elif kind == 'directory': path.mkdir(mode=0o700)
    elif kind == 'symlink': path.symlink_to(saved)
    elif kind == 'regular': path.write_bytes(original); path.chmod(0o600)
    else: raise AssertionError(kind)


@pytest.mark.parametrize('reference', ['identity', 'tzdata_second'])
@pytest.mark.parametrize('boundary', ['after_pin', 'after_read'])
@pytest.mark.parametrize('replacement', ['fifo', 'directory', 'symlink', 'regular'])
def test_public_manifest_path_substitution(tmp_path, monkeypatch, reference, boundary, replacement):
    payload, repo, root, start = author.candidate(tmp_path, monkeypatch)
    assert len(author.validate(payload, repo, root, start)) == 64
    ref = payload['identity']['created_ref'] if reference == 'identity' else payload['cohort']['tzdata']
    path = root/'objects'/ref['sha256']; original = path.read_bytes()
    target_number = 1 if reference == 'identity' else 2
    original_identity = (path.stat().st_dev, path.stat().st_ino)
    def action():
        before = len(os.listdir('/proc/self/fd'))
        real_open, real_read = os.open, os.read
        pins = 0; target_meta = None; target_data = None; swapped = False; reopened_original = False
        def swap():
            nonlocal swapped
            assert not swapped
            replace_object(path, replacement, original); swapped = True
        def race_open(name, flags, *args, **kwargs):
            nonlocal pins, target_meta, target_data, reopened_original
            fd = real_open(name, flags, *args, **kwargs)
            if str(name) == str(path) and flags & os.O_PATH:
                pins += 1
                if pins == target_number:
                    target_meta = fd
                    if boundary == 'after_pin': swap()
            elif target_meta is not None and str(name) == f'/proc/self/fd/{target_meta}':
                target_data = fd
                st = os.fstat(fd)
                reopened_original = (st.st_dev, st.st_ino) == original_identity
                assert stat.S_ISREG(st.st_mode)
                assert not os.get_inheritable(fd)
            return fd
        def race_read(fd, size):
            data = real_read(fd, size)
            if fd == target_data and boundary == 'after_read' and not swapped:
                assert data
                swap()
            return data
        with pytest.MonkeyPatch.context() as m:
            m.setattr(os, 'open', race_open); m.setattr(os, 'read', race_read)
            with pytest.raises(launch.LaunchContractError, match='ARTIFACT_LENGTH|ARTIFACT_IDENTITY|PINNED_TZDATA_IDENTITY') as exc:
                author.validate(payload, repo, root, start)
        assert swapped and reopened_original
        assert len(os.listdir('/proc/self/fd')) == before
        assert path.with_name(path.name+'.preserved').read_bytes() == original
        return {'reason': str(exc.value), 'pinned_original_inode': reopened_original, 'swapped': swapped}
    assert child(action)['pinned_original_inode']
    assert path.with_name(path.name+'.preserved').read_bytes() == original


@pytest.mark.parametrize('replacement', ['fifo', 'directory', 'symlink'])
def test_public_tzif_second_open_checks_type_again(tmp_path, monkeypatch, replacement):
    payload, repo, root, start = author.candidate(tmp_path, monkeypatch)
    assert len(author.validate(payload, repo, root, start)) == 64
    path = root/'objects'/payload['cohort']['tzdata']['sha256']; original = path.read_bytes()
    def action():
        real_open = os.open; count = 0; before = len(os.listdir('/proc/self/fd'))
        def replace_before_second(name, flags, *args, **kwargs):
            nonlocal count
            if str(name) == str(path) and flags & os.O_PATH:
                count += 1
                if count == 2: replace_object(path, replacement, original)
            return real_open(name, flags, *args, **kwargs)
        with pytest.MonkeyPatch.context() as m:
            m.setattr(os, 'open', replace_before_second)
            with pytest.raises(launch.LaunchContractError, match='PINNED_TZDATA_IDENTITY'):
                author.validate(payload, repo, root, start)
        assert count == 2 and len(os.listdir('/proc/self/fd')) == before
        return 'refused second TZif open'
    assert child(action) == 'refused second TZif open'


@pytest.mark.parametrize('error', [errno.ENOENT, errno.EACCES, errno.EMFILE])
def test_public_proc_unavailable_fails_closed_without_descriptor_leak(tmp_path, monkeypatch, error):
    payload, repo, root, start = author.candidate(tmp_path, monkeypatch)
    control = author.validate(payload, repo, root, start)
    def action():
        real_open = os.open; before = len(os.listdir('/proc/self/fd')); injected = 0
        def fail_proc(name, flags, *args, **kwargs):
            nonlocal injected
            if str(name).startswith('/proc/self/fd/'):
                injected += 1
                raise OSError(error, 'synthetic proc reopening failure')
            return real_open(name, flags, *args, **kwargs)
        with pytest.MonkeyPatch.context() as m:
            m.setattr(os, 'open', fail_proc)
            with pytest.raises(OSError) as exc: author.validate(payload, repo, root, start)
        assert exc.value.errno == error and injected == 1
        assert len(os.listdir('/proc/self/fd')) == before
        return 'fail-closed'
    assert child(action) == 'fail-closed'
    assert author.validate(payload, repo, root, start) == control


@pytest.mark.parametrize('reference', ['identity', 'tzdata'])
@pytest.mark.parametrize('defect', ['mode', 'hardlink', 'length', 'digest', 'owner'])
def test_public_regular_metadata_and_digest_controls(tmp_path, monkeypatch, reference, defect):
    payload, repo, root, start = author.candidate(tmp_path, monkeypatch)
    expected = author.validate(payload, repo, root, start)
    ref = payload['identity']['created_ref'] if reference == 'identity' else payload['cohort']['tzdata']
    path = root/'objects'/ref['sha256']; original = path.read_bytes(); inode = path.stat().st_ino
    def action():
        before = len(os.listdir('/proc/self/fd'))
        with pytest.MonkeyPatch.context() as m:
            if defect == 'mode': path.chmod(0o644)
            elif defect == 'hardlink': os.link(path, path.with_name(path.name+'.extra'))
            elif defect == 'length': path.write_bytes(original+b'x')
            elif defect == 'digest': path.write_bytes(bytes([original[0]^1])+original[1:])
            else:
                real_fstat = os.fstat
                def wrong_owner(fd):
                    st = real_fstat(fd)
                    if st.st_ino == inode:
                        values = list(st); values[4] = st.st_uid+1; return os.stat_result(values)
                    return st
                m.setattr(os, 'fstat', wrong_owner)  # No chown privilege requested.
            with pytest.raises(launch.LaunchContractError, match='ARTIFACT_LENGTH|ARTIFACT_DIGEST'):
                author.validate(payload, repo, root, start)
        assert len(os.listdir('/proc/self/fd')) == before
        return 'refused'
    assert child(action) == 'refused'
    # Keep mutated evidence intact; verify restoration only for injected-owner case.
    if defect == 'owner': assert author.validate(payload, repo, root, start) == expected


@pytest.mark.parametrize('fault', ['boot', 'manifest', 'limit', 'torn', 'sequence', 'hash'])
def test_restart_refusals_preserve_journal_and_release_descriptors(tmp_path, fault):
    journal = tmp_path/'budget'; journal.mkdir(mode=0o700)
    with launch.DurableBudget(journal, 'a'*64, max_bytes=2) as budget:
        budget.reserve('one', 2, started_monotonic=1); budget.consume('one', b'X')
    path = journal/'gate3.jsonl'
    raw = path.read_bytes()
    kwargs = {'max_bytes': 2}; digest = 'a'*64
    if fault == 'boot': kwargs['boot_id'] = 'different-boot'
    elif fault == 'manifest': digest = 'b'*64
    elif fault == 'limit': kwargs['max_bytes'] = 3
    elif fault == 'torn': path.write_bytes(raw+b'{')
    else:
        lines = raw.splitlines(); record = json.loads(lines[-1])
        if fault == 'sequence': record['seq'] += 1
        else: record['hash'] = '0'*64
        lines[-1] = launch.canonical(record); path.write_bytes(b'\n'.join(lines)+b'\n')
    rejected_bytes = path.read_bytes(); before = len(os.listdir('/proc/self/fd'))
    reason = 'JOURNAL_IDENTITY_MISMATCH' if fault in ('boot', 'manifest', 'limit') else {'torn':'JOURNAL_TORN_RECORD', 'sequence':'JOURNAL_SEQUENCE', 'hash':'JOURNAL_HASH'}[fault]
    for _ in range(2):
        with pytest.raises(launch.LaunchContractError, match=reason): launch.DurableBudget(journal, digest, **kwargs)
        assert path.read_bytes() == rejected_bytes
        assert len(os.listdir('/proc/self/fd')) == before
