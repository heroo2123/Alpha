import sys, os, json, tempfile, importlib.util
from pathlib import Path
import pytest
root=Path('/tmp/alpha-v11-gate3-a4-review-6ed21e8')
os.chdir(root)
sys.path.insert(0,str(root))
from tests.test_v11_r09_gate3_a4_review_28dd604 import BoundedCleanup
attempts=[]
def audit(event,args):
    if event in ('socket.connect','socket.connect_ex','socket.getaddrinfo','socket.bind'):
        attempts.append(event)
        raise RuntimeError('OFFLINE_REVIEW_NETWORK_REFUSED')
sys.addaudithook(audit)
with tempfile.TemporaryDirectory(prefix='a4-6ed21e8-adjudication-') as base:
    plugin=BoundedCleanup(base)
    rc=pytest.main(['-q','-p','no:cacheprovider','--rootdir=.', '--basetemp='+base]+sys.argv[1:],plugins=[plugin])
    print('REVIEW_MAX_SINGLE_TEST_ALLOCATED_BYTES='+str(plugin.peak_test_allocated_bytes))
    print('PYTHON_SOCKET_AUDIT_ATTEMPTS='+str(len(attempts)))
sys.exit(rc)
