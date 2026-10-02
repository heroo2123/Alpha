"""Independent local-only A4 repair controls and availability regression."""
import os
import signal
import sys
import types
import pytest
from tests.test_v11_r09_gate3_a4_verify import source, _write_lock
from tools import v11_r09_gate3_a4_verify as v

@pytest.mark.parametrize('kind', ['regular', 'directory', 'symlink'])
def test_existing_alternate_refuses(source, kind):
    path,pin=_write_lock(source)
    alternate=source/'.git/objects/info/alternates'
    if kind=='regular': alternate.write_text('')
    elif kind=='directory': alternate.mkdir()
    else: alternate.symlink_to(source/'.git/objects')
    with pytest.raises(v.VerificationError,match='GIT_IDENTITY_UNAVAILABLE'):
        v.open_verified(path,expected_lock_sha256=pin)

class ReviewDeadline(Exception):
    pass

def test_gap_fifo_alternate_blocks_instead_of_refusing(source):
    path,pin=_write_lock(source)
    os.mkfifo(source/'.git/objects/info/alternates',0o600)
    blocked=[]
    def deadline(signum,frame):
        blocked.append((frame.f_code.co_name, frame.f_lineno))
        raise ReviewDeadline('review-only one-second timeout')
    previous=signal.signal(signal.SIGALRM,deadline)
    signal.setitimer(signal.ITIMER_REAL,1.0)
    try:
        # This deliberately asserts the defect, not acceptance.
        with pytest.raises(ReviewDeadline):
            v.open_verified(path,expected_lock_sha256=pin)
        assert blocked == [('_isolated_git_store',115)], blocked
    finally:
        signal.setitimer(signal.ITIMER_REAL,0)
        signal.signal(signal.SIGALRM,previous)

@pytest.mark.parametrize('name', ['pkg', 'pkg.after_fork'])
def test_collision_introduced_only_in_child_refuses(source,monkeypatch,name):
    path,pin=_write_lock(source)
    with v.open_verified(path,expected_lock_sha256=pin) as session:
        original=os.fork
        def child_collision():
            pid=original()
            if pid==0: sys.modules[name]=types.ModuleType(name)
            return pid
        monkeypatch.setattr(os,'fork',child_collision)
        with pytest.raises(v.VerificationError,match='HOST_MODULE_COLLISION'):
            session.run()
    assert name not in sys.modules

def test_worktree_gitfile_refuses_explicitly(source):
    path,pin=_write_lock(source)
    (source/'.git').rename(source/'.retained-git')
    (source/'.git').write_text('gitdir: '+str(source/'.retained-git')+'\n')
    with pytest.raises(v.VerificationError,match='GIT_IDENTITY_UNAVAILABLE'):
        v.open_verified(path,expected_lock_sha256=pin)
