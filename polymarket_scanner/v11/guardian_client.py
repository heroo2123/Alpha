"""Finite independent PAPER guardian client. No archive or candidate handle."""
from dataclasses import asdict
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import uuid

from .evidence import EvidenceError, canonical, finite
from .guardian_protocol import MAX_FRAME, VERSION, BrokerPolicy, GuardianClient, decode
from .paper_guardian import GuardianDeadline, _deadline, process_limits


def run(client,*,cycles,interval_seconds=.25):
    interval=finite(interval_seconds)
    if (type(cycles) is not int or not 1<=cycles<=200 or not .1<=interval<=2 or cycles*interval>60):
        raise EvidenceError('GUARDIAN_CLIENT_FINITE_RUN_BOUND')
    if (os.getuid(),os.getgid())!=(client.policy.guardian_uid,client.policy.guardian_gid):
        raise EvidenceError('GUARDIAN_CLIENT_LOCAL_IDENTITY')
    generation=uuid.uuid4().hex
    end=time.monotonic()+60
    for cycle in range(cycles):
        if time.monotonic()>=end:raise EvidenceError('GUARDIAN_CLIENT_WALL_TIME_BOUND')
        # The broker derives health, timestamps and targets. The client cannot
        # supply READY, extend old receipts or assert any source/account state.
        client.check(generation+':'+str(cycle))
        time.sleep(interval)


def launch_client(path,*,policy,config,cycles,interval_seconds=.25):
    raw=canonical(dict(version=VERSION,socket=str(path),policy=asdict(policy),config_sha256=config,
        cycles=cycles,interval_seconds=interval_seconds)).encode()
    if len(raw)>MAX_FRAME:raise EvidenceError('GUARDIAN_CLIENT_LAUNCH_BOUND')
    child=subprocess.Popen([sys.executable,'-s','-E','-m','polymarket_scanner.v11.guardian_client'],
        cwd=Path(__file__).resolve().parents[2],env={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8'},
        stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,close_fds=True,start_new_session=True)
    try:child.stdin.write(raw);child.stdin.close();child.stdin=None
    except BaseException:
        child.kill();child.communicate(timeout=5)
        raise
    return child


def main():
    try:
        process_limits();signal.signal(signal.SIGALRM,_deadline);signal.setitimer(signal.ITIMER_REAL,65.)
        raw=decode(sys.stdin.buffer.read(MAX_FRAME+1))
        if (set(raw)!={'version','socket','policy','config_sha256','cycles','interval_seconds'}
                or raw['version']!=VERSION):raise EvidenceError('GUARDIAN_CLIENT_LAUNCH_SCHEMA')
        client=GuardianClient(raw['socket'],BrokerPolicy(**raw['policy']),raw['config_sha256'])
        run(client,cycles=raw['cycles'],interval_seconds=raw['interval_seconds'])
    except (GuardianDeadline,EvidenceError,OSError,ValueError,TypeError,KeyError,MemoryError,RecursionError,OverflowError):
        print('{"outcome":"FAILED_CLOSED","financial_authority":false}')
        return 2
    print('{"outcome":"FINITE_CLIENT_RUN_ENDED","financial_authority":false}')
    return 0


if __name__=='__main__':raise SystemExit(main())
