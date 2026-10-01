"""Reviewer-only offline runner: exact candidate unmodified; no package install."""
import ctypes
import errno
import os
from pathlib import Path
import runpy
import socket
import stat
import sys

# Install additional restrictions before loading the candidate or native decoder.
for fd in os.listdir('/proc/self/fd'):
    try:
        if stat.S_ISSOCK(os.fstat(int(fd)).st_mode):
            raise RuntimeError('Refusing inherited socket descriptor')
    except OSError:
        pass
lib = ctypes.CDLL('libseccomp.so.2', use_errno=True)
lib.seccomp_init.argtypes = [ctypes.c_uint32]
lib.seccomp_init.restype = ctypes.c_void_p
lib.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
lib.seccomp_syscall_resolve_name.restype = ctypes.c_int
lib.seccomp_rule_add.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, ctypes.c_uint]
lib.seccomp_rule_add.restype = ctypes.c_int
lib.seccomp_load.argtypes = [ctypes.c_void_p]
lib.seccomp_load.restype = ctypes.c_int
lib.seccomp_release.argtypes = [ctypes.c_void_p]
ctx = lib.seccomp_init(0x7fff0000)
assert ctx
for name in ('socket', 'socketpair', 'connect', 'bind', 'listen', 'accept', 'accept4',
             'sendto', 'sendmsg', 'sendmmsg', 'io_uring_setup', 'io_uring_enter'):
    number = lib.seccomp_syscall_resolve_name(name.encode())
    assert number >= 0, name
    assert lib.seccomp_rule_add(ctx, 0x00050000 | errno.EPERM, number, 0) == 0, name
assert lib.seccomp_load(ctx) == 0
lib.seccomp_release(ctx)
libc = ctypes.CDLL(None, use_errno=True)
for family in (socket.AF_INET, socket.AF_INET6):
    ctypes.set_errno(0)
    assert libc.socket(family, socket.SOCK_STREAM, 0) == -1
    assert ctypes.get_errno() == errno.EPERM
print('Reviewer seccomp loaded; native IPv4/IPv6 socket creation denied; no inherited sockets.', file=sys.stderr)
probe = Path('/tmp/alpha-v11-decoder-review-20a42f7/docs/V11_R09_GATE3_DECODER_BUILD_OBSERVATION_20261001_probe.py')
try:
    runpy.run_path(str(probe), run_name='__main__')
finally:
    Path('/tmp/alpha-v11-decoder-review-20a42f7-all-maps.txt').write_text(Path('/proc/self/maps').read_text())
