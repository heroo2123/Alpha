"""Finite local PAPER broker with a preemptible, separately authenticated producer.

Guardian requests retain the existing cancel-only stream protocol. Candidate
pulses use a separate packet endpoint and never run database/probe work on the
guardian event loop. This is not a deployed service or a live execution route.
"""
from dataclasses import asdict
import ctypes
import os
from pathlib import Path
import secrets
import selectors
import signal
import socket
import sqlite3
import struct
import subprocess
import sys
import time

from .candidate_liveness import CandidateLiveness, health_configuration, restore_health
from .evidence import EvidenceError, canonical, digest, finite
from . import guardian_protocol as guardian_wire, liveness_protocol as producer_wire, runtime_health
from .guardian_lease import process_identity
from .paper_guardian import GuardianDeadline, _deadline, process_limits, restore
from .paper_guardian_broker import PaperGuardianBroker


_CLOSED_ERRORS=(EvidenceError,OSError,sqlite3.Error,ValueError,TypeError,KeyError,MemoryError,RecursionError,OverflowError)


class LivenessBroker:
    def __init__(self,broker,producer):
        if not isinstance(producer,CandidateLiveness) or producer.broker is not broker:
            raise EvidenceError('LIVENESS_BROKER_BINDING')
        self.broker,self.producer=broker,producer
        self.peers={};self.job=None;self.last_dispatch=None

    def _close(self,conn):
        self.peers.pop(conn,None)
        try:self.selector.unregister(conn)
        except (KeyError,ValueError):pass
        conn.close()

    def _output(self,conn,value,*,challenge=False):
        state=self.peers.get(conn)
        if state is None:return
        if state['role']=='producer':raw=producer_wire.encode(value)
        else:
            body=canonical(value).encode()
            if not 1<=len(body)<=guardian_wire.MAX_FRAME:raise EvidenceError('BROKER_FRAME_BOUND')
            raw=struct.pack('!I',len(body))+body
        state.update(output=raw,offset=0,after_send='read' if challenge else 'close')
        self.selector.modify(conn,selectors.EVENT_WRITE,state)

    def _accept(self,listener,role,end):
        conn,_=listener.accept();conn.setblocking(False)
        try:
            maximum=4 if role=='guardian' else 2
            if sum(s['role']==role for s in self.peers.values())>=maximum:
                conn.close();return
            p=self.broker.policy
            if role=='guardian':
                peer=guardian_wire.peer_identity(conn,uid=p.guardian_uid,gid=p.guardian_gid)
                if {k:v for k,v in peer.items() if k!='gid'} in (self.broker.g.worker,process_identity(os.getpid())):
                    raise EvidenceError('BROKER_GUARDIAN_IDENTITY')
                deadline=min(end,time.monotonic()+p.timeout_seconds)
            else:
                peer=guardian_wire.peer_identity(conn,uid=self.producer.worker['uid'],gid=self.producer.policy.candidate_gid)
                if peer!=dict(self.producer.worker,gid=self.producer.policy.candidate_gid):
                    raise EvidenceError('LIVENESS_CANDIDATE_IDENTITY')
                deadline=min(end,time.monotonic()+.5)
            state=dict(role=role,peer=peer,deadline=deadline,buffer=bytearray(),size=None,output=None)
            self.peers[conn]=state;self.selector.register(conn,selectors.EVENT_READ,state)
            if role=='producer':
                state['challenge']=secrets.token_hex(32)
                state['challenge_stamp']=runtime_health.host_stamp(self.broker.store)
                self._output(conn,dict(version=producer_wire.VERSION,config_sha256=self.producer.config,
                    challenge=state['challenge']),challenge=True)
        except BaseException:
            if conn in self.peers:self._close(conn)
            else:conn.close()
            raise

    def _write(self,conn,state):
        guardian_wire.check_peer(conn,state['peer'])
        value=state['output'];sent=conn.send(value[state['offset']:])
        if sent<=0:raise EvidenceError('BROKER_DISCONNECTED_FRAME')
        if state['role']=='producer' and sent!=len(value):raise EvidenceError('LIVENESS_INCOMPLETE_SEND')
        state['offset']+=sent
        if state['offset']==len(value):
            if state['after_send']=='close':self._close(conn)
            else:
                state['output']=None;self.selector.modify(conn,selectors.EVENT_READ,state)

    def _guardian(self,conn,state):
        target=4 if state['size'] is None else 4+state['size']
        chunk=conn.recv(min(4096,target-len(state['buffer'])))
        if not chunk:raise EvidenceError('BROKER_DISCONNECTED_FRAME')
        state['buffer'].extend(chunk)
        if state['size'] is None and len(state['buffer'])==4:
            state['size']=struct.unpack('!I',state['buffer'])[0]
            if not 1<=state['size']<=guardian_wire.MAX_FRAME:raise EvidenceError('BROKER_FRAME_BOUND')
        if state['size'] is None or len(state['buffer'])!=4+state['size']:return False
        q=guardian_wire.decode(bytes(state['buffer'][4:]))
        guardian_wire.validate_request(q,self.broker.policy,self.broker.config)
        guardian_wire.check_peer(conn,state['peer'])
        # Only an authenticated valid safety request can preempt publication.
        self._stop_job()
        response=self.broker.handle(q,state['peer'])
        self._output(conn,response)
        return True

    def _producer(self,conn,state,end):
        q=producer_wire.receive_pulse(conn,state['peer'])
        producer_wire.validate_request(q,self.producer.config)
        if q['challenge']!=state['challenge'] or time.monotonic()>state['deadline']:
            raise EvidenceError('LIVENESS_CHALLENGE_EXPIRED')
        receipt=dict(stamp=runtime_health.host_stamp(self.broker.store),challenge_stamp=state['challenge_stamp'],
                     challenge_sha256=digest(state['challenge']),peer=state['peer'])
        if self.job is not None:raise EvidenceError('LIVENESS_PUBLICATION_BUSY')
        now=time.monotonic()
        if self.last_dispatch is not None and now-self.last_dispatch<self.producer.policy.minimum_interval_seconds:
            raise EvidenceError('LIVENESS_PUBLICATION_RATE')
        self.last_dispatch=now
        semantic={k:v for k,v in q.items() if k!='challenge'}
        state['deadline']=min(end,now+self.producer.policy.publication_timeout_seconds+.25)
        self.selector.unregister(conn)  # Waiting for one child, never spin on extra packets.
        self._start_job(conn,semantic,receipt,min(end,now+self.producer.policy.publication_timeout_seconds))

    def _start_job(self,conn,request,receipt,deadline):
        # The finite broker is single-threaded. EvidenceStore closes every SQLite
        # connection before returning; none may remain open across this fork.
        # Raw descriptor cleanup cannot repair an inherited SQLite connection.
        read_fd,write_fd=os.pipe2(os.O_CLOEXEC|os.O_NONBLOCK);parent=os.getpid()
        try:pid=os.fork()
        except BaseException:
            os.close(read_fd);os.close(write_fd);raise
        if pid==0:
            try:
                os.setsid()
                libc=ctypes.CDLL(None,use_errno=True)
                if libc.prctl(1,signal.SIGKILL,0,0,0)!=0 or os.getppid()!=parent:
                    os._exit(2)
                # Do not retain listeners, peers, archive flock or parent pipes.
                for entry in list(Path('/proc/self/fd').iterdir()):
                    fd=int(entry.name)
                    if fd!=write_fd:
                        try:os.close(fd)
                        except OSError:pass
                process_limits();signal.signal(signal.SIGALRM,_deadline)
                signal.setitimer(signal.ITIMER_REAL,max(.001,deadline-time.monotonic()))
                response=self.producer.publish(request,receipt)
                raw=producer_wire.encode(response)
                if os.write(write_fd,raw)!=len(raw):os._exit(2)
                os._exit(0)
            except BaseException:os._exit(2)
        os.close(write_fd)
        self.job=dict(pid=pid,fd=read_fd,conn=conn,request=request,deadline=deadline)

    def _reaped(self,job,status):
        raw=os.read(job['fd'],producer_wire.MAX_PULSE_BYTES+1)
        os.close(job['fd']);self.job=None
        conn=job['conn']
        if conn not in self.peers:return
        if status!=0 or not raw:
            self._close(conn);return
        response=guardian_wire.decode(raw);producer_wire.validate_response(response,job['request'])
        self.selector.register(conn,selectors.EVENT_READ,self.peers[conn])
        self._output(conn,response)

    def _poll_job(self):
        if self.job is None:return
        job=self.job
        pid,status=os.waitpid(job['pid'],os.WNOHANG)
        if pid:
            try:self._reaped(job,status)
            except _CLOSED_ERRORS:
                if self.job is not None:
                    os.close(job['fd']);self.job=None
                if job['conn'] in self.peers:self._close(job['conn'])
        elif time.monotonic()>=job['deadline']:self._stop_job()

    def _stop_job(self):
        if self.job is None:return
        job=self.job
        # Whole private process group includes a clock probe, if any. A fork
        # not yet past setsid has no descendants; kill its PID in that race.
        try:os.killpg(job['pid'],signal.SIGKILL)
        except ProcessLookupError:
            try:os.kill(job['pid'],signal.SIGKILL)
            except ProcessLookupError:pass
        end=time.monotonic()+.25
        while True:
            pid,_=os.waitpid(job['pid'],os.WNOHANG)
            if pid:break
            if time.monotonic()>=end:raise EvidenceError('LIVENESS_CHILD_REAP_TIMEOUT')
            time.sleep(.001)
        os.close(job['fd']);self.job=None
        if job['conn'] in self.peers:self._close(job['conn'])
        # Recovery is producer work for a later child. It cannot precede the
        # waiting safety request or rerun this accepted observation.

    def serve(self,path,liveness_path,*,guardian_connections=100,producer_connections=128,seconds=30):
        if (any(type(v) is not int or not 1<=v<=200 for v in (guardian_connections,producer_connections))
                or not .1<=finite(seconds)<=60 or Path(path)==Path(liveness_path)):
            raise EvidenceError('LIVENESS_FINITE_RUN_BOUND')
        # One archive/guardian lock; independent listener budgets and slots.
        with self.broker._listener(path) as guardian_listener, self.broker._socket(liveness_path,self.producer.config,
                socket_type=socket.SOCK_SEQPACKET,passcred=True) as producer_listener, selectors.DefaultSelector() as selector:
            self.selector=selector
            guardian_listener.setblocking(False);producer_listener.setblocking(False)
            selector.register(guardian_listener,selectors.EVENT_READ,'guardian')
            selector.register(producer_listener,selectors.EVENT_READ,'producer')
            end=time.monotonic()+seconds;counts=dict(guardian=0,producer=0)
            try:
                while time.monotonic()<end:
                    self._poll_job()
                    for conn,state in list(self.peers.items()):
                        if time.monotonic()>=state['deadline']:self._close(conn)
                    events=selector.select(min(.02,max(0,end-time.monotonic())))
                    # Guardian sockets (including pending output) always go first.
                    events.sort(key=lambda event:0 if (event[0].data=='guardian' or
                        isinstance(event[0].data,dict) and event[0].data['role']=='guardian') else 1)
                    for key,mask in events:
                        conn=key.fileobj;state=key.data
                        try:
                            if isinstance(state,str):
                                self._accept(conn,state,end);counts[state]+=1
                                limit=guardian_connections if state=='guardian' else producer_connections
                                if counts[state]>=limit:selector.unregister(conn)
                            elif conn in self.peers:
                                if mask&selectors.EVENT_WRITE:self._write(conn,state)
                                elif state['role']=='guardian':self._guardian(conn,state)
                                else:self._producer(conn,state,end)
                        except BlockingIOError:continue
                        except _CLOSED_ERRORS:
                            if not isinstance(state,str) and conn in self.peers:self._close(conn)
                    if not selector.get_map() and self.job is None:break
            finally:
                try:self._stop_job()
                finally:
                    for conn in list(self.peers):self._close(conn)


def launch_liveness_broker(guardian_config,*,policy,producer,health,path,liveness_path,
                          guardian_connections=100,producer_connections=128,seconds=30):
    raw=canonical(dict(version=producer_wire.VERSION,guardian=guardian_config,broker=asdict(policy),
        producer=asdict(producer),health=health_configuration(health),socket=str(path),liveness_socket=str(liveness_path),
        guardian_connections=guardian_connections,producer_connections=producer_connections,seconds=seconds)).encode()
    if len(raw)>guardian_wire.MAX_FRAME:raise EvidenceError('LIVENESS_LAUNCH_BOUND')
    child=subprocess.Popen([sys.executable,'-s','-E','-m','polymarket_scanner.v11.liveness_broker'],
        cwd=Path(__file__).resolve().parents[2],env={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8'},
        stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,close_fds=True,start_new_session=True)
    try:child.stdin.write(raw);child.stdin.close();child.stdin=None
    except BaseException:
        child.kill();child.communicate(timeout=5);raise
    return child


def main():
    try:
        process_limits();signal.signal(signal.SIGALRM,_deadline);signal.setitimer(signal.ITIMER_REAL,65.)
        raw=guardian_wire.decode(sys.stdin.buffer.read(guardian_wire.MAX_FRAME+1))
        if set(raw)!= {'version','guardian','broker','producer','health','socket','liveness_socket',
                       'guardian_connections','producer_connections','seconds'} or raw['version']!=producer_wire.VERSION:
            raise EvidenceError('LIVENESS_LAUNCH_SCHEMA')
        broker=PaperGuardianBroker(restore(raw['guardian']),guardian_wire.BrokerPolicy(**raw['broker']))
        producer=CandidateLiveness(broker,producer_wire.ProducerPolicy(**raw['producer']),restore_health(broker.store,raw['health']))
        LivenessBroker(broker,producer).serve(raw['socket'],raw['liveness_socket'],
            guardian_connections=raw['guardian_connections'],producer_connections=raw['producer_connections'],seconds=raw['seconds'])
    except (GuardianDeadline,*_CLOSED_ERRORS):
        print('{"outcome":"FAILED_CLOSED","financial_authority":false}')
        return 2
    print('{"outcome":"FINITE_RUN_ENDED_GATED","financial_authority":false}')
    return 0


if __name__=='__main__':raise SystemExit(main())
