import errno, os, sys, tempfile
sys.path.insert(0, os.getcwd())
from tools import v11_r09_gate3_launch as launch
from tools.v11_r09_gate3_launch import DurableBudget, LaunchContractError

def scenario_write_raises():
    root = tempfile.mkdtemp(); os.chmod(root, 0o700)
    with DurableBudget(root, '7'*64, max_bytes=10) as b:
        b.reserve('one', 10, started_monotonic=0)
        jfd = b.fd
        real = os.write
        def w(fd, data):
            if fd == jfd: raise OSError(errno.ENOSPC, 'synthetic ENOSPC, zero bytes written')
            return real(fd, data)
        launch.os.write = w
        try:
            b.consume('one', b'xyz')
        except LaunchContractError as e:
            print('in-process consume:', e, 'failed=', b.failed, 'held=', b.delivery_held, 'uncertain=', b.uncertain_received_bytes)
        finally:
            launch.os.write = real
    with DurableBudget(root, '7'*64, max_bytes=10) as r:
        print('reopened: reserved', r.reserved, 'received', r.received, 'in_flight', r.in_flight, 'held', r.delivery_held, 'marker', os.path.exists(os.path.join(root, launch.DELIVERY_HELD_MARKER)))
        try:
            r.complete('one'); print('RESTART complete() SUCCEEDED: reserved now', r.reserved, 'in_flight', r.in_flight, '=> uncertain 3 bytes erased/refunded')
        except LaunchContractError as e:
            print('restart complete rejected:', e)

def scenario_identity_fault():
    root = tempfile.mkdtemp(); os.chmod(root, 0o700)
    with DurableBudget(root, '8'*64, max_bytes=10) as b:
        b.reserve('one', 10, started_monotonic=0)
        os.chmod(root, 0o755)  # transient directory-mode drift detected by _healthy
        try:
            b.consume('one', b'xyz')
        except LaunchContractError as e:
            print('in-process consume:', e, 'failed=', b.failed, 'held=', b.delivery_held, 'uncertain=', b.uncertain_received_bytes)
        os.chmod(root, 0o700)
    with DurableBudget(root, '8'*64, max_bytes=10) as r:
        try:
            r.complete('one'); print('RESTART complete() SUCCEEDED after identity fault: reserved', r.reserved)
        except LaunchContractError as e:
            print('restart complete rejected:', e)

scenario_write_raises()
scenario_identity_fault()
