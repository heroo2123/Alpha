import sys, os, tempfile, shutil
from pathlib import Path
import pytest
root=Path('/tmp/alpha-v11-gate3-a4-r3-review-95541da')
sys.path.insert(0,str(root))
class Cleanup:
    def __init__(self,base):
        self.base=Path(base).resolve()
        self.peak=0
        self.closed=0
    @pytest.hookimpl(hookwrapper=True,tryfirst=True)
    def pytest_runtest_teardown(self,item,nextitem):
        path=item.funcargs.get('tmp_path')
        yield
        if path is not None:
            path=Path(path)
            assert path.resolve().is_relative_to(self.base)
            self.peak=max(self.peak,sum(p.lstat().st_blocks*512 for p in path.rglob('*')))
            for entry in Path('/proc/self/fd').iterdir():
                try:
                    target=os.readlink(entry)
                    if target.startswith(str(path)+'/'):
                        os.close(int(entry.name))
                        self.closed+=1
                except (FileNotFoundError,OSError): pass
            shutil.rmtree(path)
            path.mkdir(mode=0o700)

def main():
    os.chdir(root)
    attempts=[]
    def audit(event,args):
        if event in ('socket.connect','socket.connect_ex','socket.getaddrinfo','socket.bind'):
            attempts.append(event)
            raise RuntimeError('OFFLINE_REVIEW_NETWORK_REFUSED')
    sys.addaudithook(audit)
    with tempfile.TemporaryDirectory(prefix='a4-r3-95541da-adjudication-') as base:
        plugin=Cleanup(base)
        rc=pytest.main(['-q','-p','no:cacheprovider','--rootdir=.', '--basetemp='+base]+sys.argv[1:],plugins=[plugin])
        print('REVIEW_MAX_SINGLE_TEST_ALLOCATED_BYTES='+str(plugin.peak))
        print('CLOSED_COMPLETED_FIXTURE_FDS='+str(plugin.closed))
        print('PYTHON_SOCKET_AUDIT_ATTEMPTS='+str(len(attempts)))
    return rc
if __name__=='__main__': sys.exit(main())
