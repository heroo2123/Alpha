"""Independent repair acceptance at d080eac / doc HEAD 1fa3902.
Disposable synthetic stores only. Prior defect oracles are excluded.
Run from candidate with PYTHONPATH=. python -m pytest -q <this file>.
"""
import importlib.util
from pathlib import Path
import json
import os
import select
import signal
import threading
from dataclasses import replace

import pytest
from tools import v11_r09_gate3_store_v1 as v1
from tools.v11_r09_gate3_launch import LaunchContractError

_spec = importlib.util.spec_from_file_location('previous_repair_review',
    Path(__file__).with_name('V11_R09_GATE3_STORE_RESTART_REVIEW_7bc627e_probes.py'))
prior = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(prior)
p = prior.p
for _name in dir(prior):
    if _name.startswith('test_') and not _name.startswith('test_defect_'):
        globals()[_name] = getattr(prior, _name)


@pytest.mark.parametrize('operation', ['read', 'seal', 'close_read', 'close_seal'])
def test_inherited_operations_reject_and_parent_lock_survives(tmp_path, operation):
    root = p.root_at(tmp_path)
    store = p.open_store(root)
    entered, release = threading.Event(), threading.Event()
    errors, receipts = [], []
    def recorder():
        entered.set()
        assert release.wait(10)
        return p.sample('durable_seal', 103, 4)
    def writer():
        try:
            receipts.append(p.seal(store, recorder=recorder))
        except BaseException as exc:
            errors.append(exc)
    thread = threading.Thread(target=writer)
    thread.start()
    pid, read_fd, write_fd = None, None, None
    try:
        assert entered.wait(5)
        read_fd, write_fd = os.pipe()
        pid = os.fork()
        if pid == 0:
            os.close(read_fd)
            try:
                assert store._pid == -1
                assert all(getattr(store, n) is None for n in ('root_fd', 'dir_fd', 'journal_fd'))
                if operation.startswith('close_'):
                    store.close()
                try:
                    if operation.endswith('read'):
                        store.read_receipt(None)
                    else:
                        p.seal(store)
                except LaunchContractError as exc:
                    assert str(exc) == 'STORE_OWNER_PROCESS'
                else:
                    raise AssertionError('inherited operation accepted')
                os.write(write_fd, b'REJECTED')
                os._exit(0)
            except BaseException:
                os._exit(2)
        os.close(write_fd)
        write_fd = None
        assert select.select([read_fd], [], [], 2)[0], 'child blocked'
        assert os.read(read_fd, 8) == b'REJECTED'
        # Child closes must not unlock the parent, even with its writer paused.
        with pytest.raises(BlockingIOError):
            p.open_store(root)
    finally:
        if pid and pid > 0:
            # Bounded cleanup also on assertion failure or a reintroduced deadlock.
            got, _ = os.waitpid(pid, os.WNOHANG)
            if got == 0:
                os.kill(pid, signal.SIGKILL)
                os.waitpid(pid, 0)
        for fd in (read_fd, write_fd):
            if fd is not None:
                os.close(fd)
        release.set()
        thread.join(5)
        store.close()
    assert not thread.is_alive() and not errors
    receipt = receipts[0]
    with p.open_store(root, expected_descriptor_sha256=store.descriptor_sha256) as recovered:
        old = recovered.receipts[receipt.object_sha256]
        assert old.clocks == receipt.clocks
        assert old.acknowledgement == 'UNKNOWN'
        assert recovered.read_receipt(old) == b'independent bytes'


def encoded_descriptor(root, values):
    st, obj = root.stat(), (root/'objects').stat()
    d = dict(version=1, store_id='0'*32, root_dev=st.st_dev, root_ino=st.st_ino,
        objects_dev=obj.st_dev, objects_ino=obj.st_ino,
        manifest='a'*64, policy='b'*64, build=values[0], clock_method=values[1],
        max_clock_age=10, original_host_id=values[2], original_boot_id=values[3],
        max_object=4*1024*1024, max_journal=64*1024*1024, max_record=64*1024,
        max_events=10000, max_objects=4096, max_clock_raw=16*1024)
    return json.dumps(d, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')


def context_at_size(root, size):
    chars = ['a']*512
    extra = size - len(encoded_descriptor(root, ['a'*128]*4))
    count, rest = divmod(extra, 11)
    assert extra >= 0 and count + rest <= 512
    chars[:count] = ['\U0001f680']*count  # 12 encoded bytes instead of one
    chars[count:count+rest] = ['"']*rest  # two encoded bytes instead of one
    values = [''.join(chars[i:i+128]) for i in range(0, 512, 128)]
    assert len(encoded_descriptor(root, values)) == size
    return dict(build_id=values[0], clock_method=values[1], host_id=values[2], boot_id=values[3])


@pytest.mark.parametrize('size', [4095, 4096, 4097])
def test_real_descriptor_byte_boundary_and_roundtrip(tmp_path, size, monkeypatch):
    root = p.root_at(tmp_path)
    kw = context_at_size(root, size)
    creates = []
    real_open = os.open
    def tracked_open(path, flags, *args, **kwargs):
        if flags & os.O_CREAT:
            creates.append(path)
        return real_open(path, flags, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(os, 'open', tracked_open)
        if size > 4096:
            with pytest.raises(LaunchContractError, match='STORE_DESCRIPTOR_CAPACITY'):
                p.open_store(root, **kw)
            assert creates == []
            assert list(root.iterdir()) == [root/'objects']
            with p.open_store(root) as fresh:
                assert fresh.report.classification == 'VALID'
            return
        with p.open_store(root, **kw) as store:
            assert (root/'store-v1.json').stat().st_size == size
            def adapt(c):
                return replace(c, reading=replace(c.reading, boot_id=kw['boot_id']),
                               method=kw['clock_method'])
            receipt = p.seal(store, clocks=tuple(adapt(c) for c in p.prefix()),
                             recorder=lambda: adapt(p.sample('durable_seal', 103, 4)))
            pin = store.descriptor_sha256
            assert store.read_receipt(receipt) == b'independent bytes'
    for _ in range(2):
        with p.open_store(root, expected_descriptor_sha256=pin, **kw) as reopened:
            assert reopened.report.classification == 'VALID'
            old = reopened.receipts[receipt.object_sha256]
            assert old.clocks == receipt.clocks
            assert old.acknowledgement == 'UNKNOWN'
            assert not old.historical_feature_eligible
            assert reopened.read_receipt(old) == b'independent bytes'


@pytest.mark.parametrize('fields', [
    ('build_id','clock_method','host_id'),
    ('build_id','clock_method','host_id','boot_id')])
def test_unicode_expansion_refuses_without_metadata(tmp_path, fields):
    root = p.root_at(tmp_path)
    with pytest.raises(LaunchContractError, match='STORE_DESCRIPTOR_CAPACITY'):
        p.open_store(root, **{f:'\U0001f680'*128 for f in fields})
    assert list(root.iterdir()) == [root/'objects']
    assert not list((root/'objects').iterdir())
    with p.open_store(root) as fresh:
        assert fresh.report.classification == 'VALID'


@pytest.mark.parametrize('operation', ['read', 'seal'])
def test_foreign_pid_guard_precedes_any_mutex_entry(tmp_path, operation):
    root = p.root_at(tmp_path)
    store = p.open_store(root)
    mutex, pid = store._mutex, store._pid
    class ForbiddenMutex:
        def __enter__(self):
            raise AssertionError('mutex attempted before owner rejection')
        def __exit__(self, *args):
            pass
    try:
        store._mutex, store._pid = ForbiddenMutex(), pid + 1
        with pytest.raises(LaunchContractError, match='STORE_OWNER_PROCESS'):
            store.read_receipt(None) if operation == 'read' else p.seal(store)
    finally:
        store._mutex, store._pid = mutex, pid
        store.close()
