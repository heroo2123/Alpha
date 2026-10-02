"""Independent offline probes of commit 28dd604; gap_ tests pass on reproduction.
Run from pinned checkout with PYTHONDONTWRITEBYTECODE=1, pytest no cache.
All mutations are confined to pytest temporary fixtures; no network endpoint.
"""
import ctypes
import errno
import hashlib
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import types
import pytest
from tests.test_v11_r09_gate3_a4_verify import source, _write_lock, _new_run, _sha, _git, _environment_digest
from tools import v11_r09_gate3_a4_verify as v


def commit(root, path, content):
    (root / path).write_text(content)
    subprocess.run(['git', '-C', str(root), 'add', path], check=True)
    subprocess.run(['git', '-C', str(root), '-c', 'user.name=Review', '-c',
                    'user.email=review@example.invalid', 'commit', '-qm', 'probe'], check=True)


def test_restart_new_build_new_process_refuses(source):
    path, pin = _write_lock(source)
    v.open_verified(path, expected_lock_sha256=pin).close()
    commit(source, 'pkg/helper.py', 'VALUE = 99\n')
    path, pin = _write_lock(source)
    code = ('from tools.v11_r09_gate3_a4_verify import *\n'
            'import sys\n'
            'try: open_verified(sys.argv[1], expected_lock_sha256=sys.argv[2])\n'
            'except VerificationError as e: print(e); sys.exit(42)\n')
    p = subprocess.run([sys.executable, '-c', code, str(path), pin], capture_output=True, text=True, timeout=15)
    assert p.returncode == 42 and p.stdout.strip() == 'RESTART_BUILD_CHANGED'


def test_same_lock_reopen_and_live_record_replacement(source):
    path, pin = _write_lock(source)
    v.open_verified(path, expected_lock_sha256=pin).close()
    with v.open_verified(path, expected_lock_sha256=pin) as s:
        assert s.run() == [7, 'reviewed-memfs']
    with v.open_verified(path, expected_lock_sha256=pin) as s:
        record = Path(str(path) + '.a4-first-lock')
        raw = record.read_bytes()
        record.unlink(); record.write_bytes(raw)
        with pytest.raises(v.VerificationError, match='RUN_RECORD_SUBSTITUTED'):
            s.check_all()


def test_restart_missing_record_refuses(source):
    path, pin = _write_lock(source)
    Path(str(path) + '.a4-first-lock').unlink()
    with pytest.raises(v.VerificationError, match='RUN_RECORD_MISSING'):
        v.open_verified(path, expected_lock_sha256=pin)


def test_git_environment_override_is_not_inherited(source, monkeypatch):
    path, pin = _write_lock(source)
    lock = json.loads(path.read_bytes())
    for key in ['GIT_DIR', 'GIT_WORK_TREE', 'GIT_OBJECT_DIRECTORY', 'GIT_ALTERNATE_OBJECT_DIRECTORIES',
                'GIT_REPLACE_REF_BASE', 'GIT_CONFIG', 'GIT_CONFIG_GLOBAL', 'GIT_CONFIG_SYSTEM', 'GIT_EXEC_PATH']:
        monkeypatch.setenv(key, '/nonexistent/a4-review-override')
    monkeypatch.setenv('GIT_CONFIG_COUNT', '1')
    monkeypatch.setenv('GIT_CONFIG_KEY_0', 'core.bare')
    monkeypatch.setenv('GIT_CONFIG_VALUE_0', 'true')
    lock['environment_sha256'] = _environment_digest()
    path, pin = _new_run(path, lock)
    with v.open_verified(path, expected_lock_sha256=pin) as s:
        assert s.run() == [7, 'reviewed-memfs']


def test_git_replacement_blob_refuses(source):
    genuine = _git(source, 'rev-parse', 'HEAD:pkg/helper.py')
    (source / 'pkg/helper.py').write_text('VALUE = 666\n')
    evil = _git(source, 'hash-object', '-w', 'pkg/helper.py')
    _git(source, 'replace', genuine, evil)
    path, pin = _write_lock(source)
    with pytest.raises(v.VerificationError, match='GIT_SOURCE_MISMATCH'):
        v.open_verified(path, expected_lock_sha256=pin)


def test_git_commit_replacement_is_ignored(source):
    path, pin = _write_lock(source)
    old = _git(source, 'rev-parse', 'HEAD')
    commit(source, 'pkg/helper.py', 'VALUE = 666\n')
    evil = _git(source, 'rev-parse', 'HEAD')
    _git(source, 'replace', old, evil)
    (source / 'pkg/helper.py').write_text('VALUE = 7\n')
    with v.open_verified(path, expected_lock_sha256=pin) as s:
        assert s.run() == [7, 'reviewed-memfs']


@pytest.mark.parametrize('case', ['duplicate_top', 'duplicate_nested', 'whitespace', 'bom', 'nan', 'infinity', 'negative_infinity'])
def test_canonical_rejections(source, case):
    path, pin = _write_lock(source)
    raw = path.read_bytes()
    expected = 'LOCK_'
    if case == 'duplicate_top':
        raw = raw.replace(b'"entrypoint":"pkg.main"', b'"entrypoint":"decoder","entrypoint":"pkg.main"')
        expected = 'LOCK_DUPLICATE_KEY'
    elif case == 'duplicate_nested':
        raw = raw.replace(b'"kind":"python"', b'"kind":"python","kind":"python"', 1)
        expected = 'LOCK_DUPLICATE_KEY'
    elif case == 'whitespace': raw += b'\n'; expected = 'LOCK_NONCANONICAL'
    elif case == 'bom': raw = b'\xef\xbb\xbf' + raw; expected = 'LOCK_NONCANONICAL'
    else:
        value = {'nan': b'NaN', 'infinity': b'Infinity', 'negative_infinity': b'-Infinity'}[case]
        raw = raw.replace(b'"schema":"ALPHA_V11_GATE3_A4_OFFLINE_LOCK_V1"', b'"schema":' + value)
        expected = 'LOCK_NONFINITE'
    path.write_bytes(raw)
    with pytest.raises(v.VerificationError, match=expected):
        v.open_verified(path, expected_lock_sha256=_sha(raw))


@pytest.mark.parametrize('case', ['changed', 'replacement', 'added', 'removed'])
def test_python_bootstrap_changes(source, tmp_path, case):
    name = 'a4_independent_bootstrap'
    p = tmp_path / (name + '.py')
    p.write_text('VALUE=1\n')
    sys.path.insert(0, str(tmp_path))
    try:
        if case != 'added': importlib.import_module(name)
        path, pin = _write_lock(source)
        with v.open_verified(path, expected_lock_sha256=pin) as s:
            if case == 'changed': p.write_text('VALUE=2\n')
            elif case == 'replacement': p.rename(p.with_suffix('.retained')); p.write_text('VALUE=1\n')
            elif case == 'added': importlib.import_module(name)
            else: sys.modules.pop(name)
            with pytest.raises(v.VerificationError, match='BOOTSTRAP_FILES_CHANGED'):
                s.check_all()
    finally:
        sys.path.remove(str(tmp_path)); sys.modules.pop(name, None)


def test_metadata_and_cross_process_syscalls_denied(source):
    commit(source, 'pkg/main.py', 'def main(runtime):\n    return runtime._probe()\n')
    libc = ctypes.CDLL(None, use_errno=True)
    lib = ctypes.CDLL('libseccomp.so.2')
    lib.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    names = ('chmod fchmod fchmodat fchmodat2 chown fchown lchown fchownat '
             'setxattr lsetxattr fsetxattr removexattr lremovexattr fremovexattr '
             'utime utimes futimesat utimensat mknod mknodat pidfd_open pidfd_getfd '
             'process_vm_readv process_vm_writev').split()
    numbers = [(name, lib.seccomp_syscall_resolve_name(name.encode())) for name in names]
    assert all(n >= 0 for _, n in numbers)
    path, pin = _write_lock(source)
    def probe():
        result = {}
        for name, number in numbers:
            ctypes.set_errno(0)
            rc = libc.syscall(ctypes.c_long(number), ctypes.c_long(-1), ctypes.c_long(0),
                              ctypes.c_long(0), ctypes.c_long(0), ctypes.c_long(0), ctypes.c_long(0))
            result[name] = [rc, ctypes.get_errno()]
        return result
    with v.open_verified(path, expected_lock_sha256=pin) as s:
        s._probe = probe
        assert s.run() == {name: [-1, errno.EPERM] for name in names}


def test_initial_host_submodule_collision_refuses(source):
    sys.modules['pkg.host'] = types.ModuleType('pkg.host')
    try:
        path, pin = _write_lock(source)
        with pytest.raises(v.VerificationError, match='HOST_MODULE_COLLISION'):
            v.open_verified(path, expected_lock_sha256=pin)
    finally: sys.modules.pop('pkg.host', None)


def test_missing_from_import_has_no_host_fallback(source):
    commit(source, 'pkg/main.py', 'from pkg import ghost\ndef main(runtime):\n    return ghost.VALUE\n')
    path, pin = _write_lock(source)
    with v.open_verified(path, expected_lock_sha256=pin) as s:
        with pytest.raises(v.VerificationError, match='cannot import name'):
            s.run()


def test_gap_late_host_submodule_fallback(source):
    commit(source, 'pkg/main.py', 'from pkg import ghost\ndef main(runtime):\n    return ghost.value()\n')
    path, pin = _write_lock(source)
    with v.open_verified(path, expected_lock_sha256=pin) as s:
        ghost = types.ModuleType('pkg.ghost')
        exec(compile('def value():\n    return 616\n', '<unlocked-host-submodule>', 'exec'), ghost.__dict__)
        sys.modules['pkg.ghost'] = ghost
        try:
            s.check_all()  # Added fileless module is absent from _bootstrap_files().
            assert s.run() == 616  # CPython IMPORT_FROM uses host sys.modules.
        finally: sys.modules.pop('pkg.ghost', None)


def test_gap_git_local_promisor_executes_unlocked_helper(source):
    path, pin = _write_lock(source)
    lock = json.loads(path.read_bytes())
    marker = source.parent / 'unlocked-git-helper-ran'
    settings = {'core.repositoryformatversion': '1', 'extensions.partialClone': 'origin',
                'remote.origin.promisor': 'true', 'remote.origin.partialclonefilter': 'blob:none',
                'remote.origin.url': 'ext::/usr/bin/touch ' + str(marker),
                'protocol.ext.allow': 'always'}
    for key, value in settings.items(): _git(source, 'config', key, value)
    oid = lock['commit']
    (source / '.git/objects' / oid[:2] / oid[2:]).unlink()
    with pytest.raises(v.VerificationError, match='GIT_IDENTITY_UNAVAILABLE'):
        v.open_verified(path, expected_lock_sha256=pin)
    assert marker.exists()  # No network: ext helper is only local /usr/bin/touch.


class BoundedCleanup:
    """Only remove each test's tmp_path after all its fixture finalizers finish."""
    def __init__(self, base):
        self.base = Path(base).resolve()
        self.peak_test_allocated_bytes = 0

    @pytest.hookimpl(hookwrapper=True, tryfirst=True)
    def pytest_runtest_teardown(self, item, nextitem):
        path = item.funcargs.get('tmp_path')
        yield
        if path is not None:
            path = Path(path)
            assert path.resolve().is_relative_to(self.base)
            allocated = sum(p.lstat().st_blocks * 512 for p in path.rglob('*'))
            self.peak_test_allocated_bytes = max(self.peak_test_allocated_bytes, allocated)
            import shutil
            shutil.rmtree(path)
            path.mkdir(mode=0o700)  # Preserve numbering: fixtures cache lineage by path.


if __name__ == '__main__':
    assert sys.argv[1] == '--adjacent-child'
    plugin = BoundedCleanup(sys.argv[2])
    rc = pytest.main(['-q', '-p', 'no:cacheprovider', '--rootdir=.',
                      '--basetemp=' + sys.argv[2], sys.argv[3]], plugins=[plugin])
    print('REVIEW_MAX_SINGLE_TEST_ALLOCATED_BYTES=' + str(plugin.peak_test_allocated_bytes))
    sys.exit(rc)
