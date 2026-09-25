"""Disposable Linux custody fixture runner; deliberately not a collected test.

The outer caller stays unprivileged. Approved uidmap helpers map only its assigned
subordinate IDs. Namespace-root performs setup, then each fixture role runs with
distinct real/effective/saved IDs, no supplementary groups and zero capabilities.
No host account, service, permission, mount or network configuration is changed.

``run_namespace_fixture(script, payload)`` calls ``script.run(context, payload)``.
The context supplies ``spawn(role, argv)``, ``python``, ``repository``, ``private``
directories, ``socket_directory``, and custody-owned fixture-file helpers. Every
role process is inside a PID namespace whose init exit terminates all descendants.
Missing prerequisites raise NamespaceUnavailable, never a successful proof.
"""
from __future__ import annotations

import ctypes
import importlib.util
import json
import os
from pathlib import Path
import selectors
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import traceback

if sys.platform == "linux":
    import pwd
    import resource


ROLE_IDS = {"broker": 1001, "guardian": 1002, "candidate": 1003}
MAX_OUTPUT = 256 * 1024
MAX_INPUT = 32768
ENV = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1"}
CAP_FIELDS = ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb")
REPOSITORY = Path(__file__).resolve().parents[1]


class NamespaceUnavailable(RuntimeError):
    """Environment cannot establish the required kernel boundary."""


class NamespaceFailure(RuntimeError):
    """A supported fixture failed or exceeded its explicit bounds."""


def _libc_call(name, *args):
    libc = ctypes.CDLL(None, use_errno=True)
    if getattr(libc, name)(*args) != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code))


def _prctl(option, arg=0):
    _libc_call("prctl", option, arg, 0, 0, 0)


def _mount(source, target, filesystem=None, flags=0, data=None):
    def encoded(value):
        return None if value is None else os.fsencode(value)
    _libc_call("mount", encoded(source), encoded(target), encoded(filesystem), ctypes.c_ulong(flags), encoded(data))


def _bind_readonly(source, target):
    target.mkdir(parents=True, exist_ok=True)
    _mount(source, target, flags=4096)  # MS_BIND
    _mount(None, target, flags=4096 | 32 | 1 | 2 | 4)  # REMOUNT, RDONLY, NOSUID, NODEV


def _limits():
    os.umask(0o077)
    for kind, desired in ((resource.RLIMIT_CORE, 0), (resource.RLIMIT_NOFILE, 64),
                          (resource.RLIMIT_AS, 512 * 1024**2), (resource.RLIMIT_CPU, 20),
                          (resource.RLIMIT_FSIZE, 16 * 1024**2)):
        _, hard = resource.getrlimit(kind)
        value = desired if hard == resource.RLIM_INFINITY else min(hard, desired)
        resource.setrlimit(kind, (value, value))


def _credentials():
    status = dict(line.split(":", 1) for line in Path("/proc/self/status").read_text().splitlines() if ":" in line)
    return {"pid": os.getpid(), "uids": list(os.getresuid()), "gids": list(os.getresgid()),
            "groups": os.getgroups(), "capabilities": {key: int(status[key].strip(), 16) for key in CAP_FIELDS},
            "no_new_privileges": int(status["NoNewPrivs"].strip())}


def _validate_role(actual, role):
    number = ROLE_IDS[role]
    if (actual["uids"] != [number] * 3 or actual["gids"] != [number] * 3 or actual["groups"]
            or any(actual["capabilities"].values()) or actual["no_new_privileges"] != 1):
        raise NamespaceFailure("role credential/capability boundary failed")


def _readline(stream, deadline, maximum=8192):
    result = bytearray()
    with selectors.DefaultSelector() as selector:
        selector.register(stream, selectors.EVENT_READ)
        while len(result) <= maximum:
            if not selector.select(max(0, deadline - time.monotonic())):
                raise NamespaceFailure("bounded child handshake timed out")
            char = os.read(stream.fileno(), 1)
            if not char:
                raise NamespaceFailure("child exited before credential handshake")
            if char == b"\n":
                return bytes(result)
            result.extend(char)
    raise NamespaceFailure("oversized child handshake")


def _collect(process, deadline):
    chunks = {"stdout": bytearray(), "stderr": bytearray()}
    with selectors.DefaultSelector() as selector:
        for name in chunks:
            selector.register(getattr(process, name), selectors.EVENT_READ, name)
        while selector.get_map():
            if time.monotonic() >= deadline:
                raise NamespaceFailure("namespace fixture time bound")
            for key, _ in selector.select(min(.1, max(0, deadline - time.monotonic()))):
                data = os.read(key.fileobj.fileno(), 8192)
                if not data:
                    selector.unregister(key.fileobj)
                    continue
                chunks[key.data].extend(data)
                if sum(map(len, chunks.values())) > MAX_OUTPUT:
                    raise NamespaceFailure("namespace fixture output bound")
    process.wait(timeout=max(.01, deadline - time.monotonic()))
    return {key: bytes(value).decode("utf-8", errors="replace") for key, value in chunks.items()}


def _subordinate(kind):
    uid = os.getuid()
    user = pwd.getpwuid(uid).pw_name
    path = Path("/etc") / kind
    try:
        lines = path.read_text().splitlines()
    except OSError as exc:
        raise NamespaceUnavailable("assigned subordinate ID metadata is unavailable: " + kind) from exc
    for line in lines:
        parts = line.split(":")
        if len(parts) == 3 and parts[0] in {user, str(uid)}:
            try:
                start, count = map(int, parts[1:])
            except ValueError as exc:
                raise NamespaceUnavailable("invalid assigned subordinate ID metadata: " + kind) from exc
            if start > 0 and count >= 3:
                return start
    raise NamespaceUnavailable("three assigned subordinate IDs are required in /etc/" + kind)


def prerequisites():
    if sys.platform != "linux" or os.getuid() == 0:
        raise NamespaceUnavailable("an unprivileged native Linux caller is required")
    helpers = {}
    for name in ("newuidmap", "newgidmap"):
        path = shutil.which(name, path="/usr/bin:/bin")
        if path is None:
            raise NamespaceUnavailable("owner prerequisite: install the standard uidmap package (missing " + name + ")")
        info = Path(path).stat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise NamespaceUnavailable("uidmap helper custody is not package-safe")
        helpers[name] = path
    return {"helpers": helpers, "subuid": _subordinate("subuid"), "subgid": _subordinate("subgid")}


def run_namespace_fixture(fixture_script, payload=None, *, timeout=30):
    """Run an explicit trusted fixture script; return its result plus role proofs."""
    if type(timeout) not in (int, float) or not 5 <= timeout <= 60:
        raise ValueError("fixture timeout must be 5..60 seconds")
    setup = prerequisites()
    fixture = Path(fixture_script).resolve()
    relative = fixture.relative_to(REPOSITORY)
    if not fixture.is_file() or relative.parts[0] != "tests":
        raise ValueError("fixture must be an explicit script within repository tests")
    deadline = time.monotonic() + timeout
    # Avoid a runner's private TMPDIR ancestor and AF_UNIX path-length inflation.
    # The isolated mount below supplies the bounded in-memory fixture contents.
    with tempfile.TemporaryDirectory(prefix="a11-custody-", dir="/tmp") as scratch:
        request = {"root": scratch, "repository": str(REPOSITORY), "fixture": relative.as_posix(),
                   "base_python": sys.base_prefix, "site_packages": str(Path(sys.prefix) / "lib" /
                   ("python" + sys.version.split()[0].rsplit(".", 1)[0]) / "site-packages"),
                   "payload": payload, "timeout": timeout}
        encoded = json.dumps(request, allow_nan=False).encode()
        if len(encoded) > MAX_INPUT:
            raise ValueError("fixture input bound")
        process = subprocess.Popen([sys.executable, "-s", "-E", "-B", str(Path(__file__).resolve()), "--namespace-child"],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   env=ENV, close_fds=True, start_new_session=True)
        try:
            ready = json.loads(_readline(process.stdout, min(deadline, time.monotonic() + 5)))
            if ready != {"mapping_ready": process.pid}:
                raise NamespaceUnavailable("user namespace setup rejected: " + str(ready))
            for helper, own_id, subordinate in (("newuidmap", os.getuid(), setup["subuid"]),
                                                ("newgidmap", os.getgid(), setup["subgid"])):
                result = subprocess.run([setup["helpers"][helper], str(process.pid), "0", str(own_id), "1",
                                         "1001", str(subordinate), "3"], stdin=subprocess.DEVNULL,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=ENV,
                                        timeout=min(5, max(.1, deadline - time.monotonic())), check=False)
                if result.returncode:
                    raise NamespaceUnavailable(helper + " refused the assigned mapping: " + result.stderr.decode(errors="replace").strip())
            process.stdin.write(encoded + b"\n")
            process.stdin.close()
            process.stdin = None
            output = _collect(process, deadline)
            if process.returncode != 0:
                raise NamespaceFailure("namespace fixture failed: " + output["stdout"] + output["stderr"])
            result = json.loads(output["stdout"])
            if result.get("status") != "PASSED" or output["stderr"]:
                raise NamespaceFailure("namespace fixture did not return clean proof: " + str(result))
            result["outer_mapping"] = {"caller_uid": os.getuid(), "caller_gid": os.getgid(),
                                       "role_host_uid_start": setup["subuid"], "role_host_gid_start": setup["subgid"]}
            return result
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    stream.close()


class CustodyContext:
    def __init__(self, request):
        self.root = Path(request["root"])
        self.repository = self.root / "repo"
        self.python = str(self.root / "runtime" / "bin" / "python")
        self.ids = dict(ROLE_IDS)
        self.private = {role: self.root / (role + "-private") for role in ROLE_IDS}
        self.socket_directory = self.root / "socket"
        self.processes = []
        self.role_proofs = []
        self.deadline = time.monotonic() + request["timeout"] - 1
        self._setup(request)

    def _setup(self, request):
        _mount(None, "/", flags=(1 << 18) | 16384)  # MS_PRIVATE | MS_REC
        # Preserve the archive's 64 MiB free-space safety floor in this bounded fixture.
        _mount("tmpfs", self.root, "tmpfs", 2 | 4, "size=128m,nr_inodes=8192,mode=0755")
        _mount("proc", "/proc", "proc", 2 | 4 | 8)
        _bind_readonly(Path(request["repository"]), self.repository)
        base = self.root / "python-base"
        _bind_readonly(Path(request["base_python"]), base)
        runtime = self.root / "runtime"
        (runtime / "bin").mkdir(parents=True, mode=0o755)
        runtime.chmod(0o755)
        (runtime / "bin").chmod(0o755)
        (runtime / "bin" / "python").symlink_to(base / "bin" / ("python" + str(sys.version_info.major) + "." + str(sys.version_info.minor)))
        (runtime / "pyvenv.cfg").write_text("home = " + str(base / "bin") + "\ninclude-system-site-packages = false\n")
        (runtime / "pyvenv.cfg").chmod(0o644)
        packages = runtime / "lib" / ("python" + str(sys.version_info.major) + "." + str(sys.version_info.minor)) / "site-packages"
        _bind_readonly(Path(request["site_packages"]), packages)
        for directory in (packages.parent, packages.parent.parent):
            directory.chmod(0o755)
        for role, directory in self.private.items():
            directory.mkdir(mode=0o700)
            os.chown(directory, ROLE_IDS[role], ROLE_IDS[role])
        self.socket_directory.mkdir(mode=0o755)
        self.socket_directory.chmod(0o755)
        os.chown(self.socket_directory, ROLE_IDS["broker"], ROLE_IDS["broker"])
        _limits()

    def write_private(self, role, name, data):
        if role not in ROLE_IDS or Path(name).name != name or name in {"", ".", ".."}:
            raise ValueError("private fixture filename")
        value = data.encode() if isinstance(data, str) else data
        if not isinstance(value, bytes) or len(value) > 1024 * 1024:
            raise ValueError("private fixture byte bound")
        path = self.private[role] / name
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        try:
            os.fchown(fd, ROLE_IDS[role], ROLE_IDS[role])
            with os.fdopen(fd, "wb", closefd=False) as stream:
                stream.write(value)
        finally:
            os.close(fd)
        return path

    def spawn(self, role, argv, *, stdin=subprocess.PIPE):
        if role not in ROLE_IDS or len(self.processes) >= 8 or not isinstance(argv, (list, tuple)) or not argv:
            raise ValueError("bounded fixture role/argv required")
        argv = list(map(os.fspath, argv))
        if len(argv) > 64 or any(len(item) > 8192 for item in argv) or not Path(argv[0]).is_absolute():
            raise ValueError("bounded absolute executable required")
        helper = self.repository / "tests" / "guardian_custody_namespace.py"
        process = subprocess.Popen([self.python, "-s", "-E", "-B", str(helper), "--role-child", role, *argv],
                                   cwd=self.repository, stdin=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   env=ENV, close_fds=True)
        self.processes.append(process)
        proof = json.loads(_readline(process.stdout, min(self.deadline, time.monotonic() + 5)))
        _validate_role(proof, role)
        if proof["pid"] != process.pid:
            raise NamespaceFailure("role process identity changed")
        process.custody_identity = proof
        self.role_proofs.append({"role": role, **proof})
        return process

    def close(self):
        for process in self.processes:
            if process.poll() is None:
                process.kill()
        for process in self.processes:
            try:
                process.wait(timeout=2)
            finally:
                for stream in (process.stdin, process.stdout, process.stderr):
                    if stream is not None:
                        stream.close()


def _role_child(role, argv):
    number = ROLE_IDS[role]
    if os.getuid() != 0 or os.getgroups():
        raise NamespaceFailure("role bootstrap requires the cleared namespace controller")
    _prctl(47, 4)  # PR_CAP_AMBIENT, CLEAR_ALL
    last_cap = int(Path("/proc/sys/kernel/cap_last_cap").read_text())
    for capability in range(last_cap + 1):
        _prctl(24, capability)  # PR_CAPBSET_DROP
    _prctl(38, 1)  # PR_SET_NO_NEW_PRIVS
    os.setresgid(number, number, number)
    os.setresuid(number, number, number)
    class Header(ctypes.Structure):
        _fields_ = [("version", ctypes.c_uint32), ("pid", ctypes.c_int)]
    class Capabilities(ctypes.Structure):
        _fields_ = [("effective", ctypes.c_uint32), ("permitted", ctypes.c_uint32), ("inheritable", ctypes.c_uint32)]
    header = Header(0x20080522, 0)
    values = (Capabilities * 2)()
    _libc_call("capset", ctypes.byref(header), ctypes.byref(values))
    _limits()
    proof = _credentials()
    _validate_role(proof, role)
    print(json.dumps(proof), flush=True)
    os.execve(argv[0], argv, ENV)


def _enter_custody_namespace():
    """Clear inherited groups, then deny setgroups before an inner GID map.

    Linux requires a populated GID map before setgroups can clear inherited
    groups, but forbids switching setgroups to deny after populating that map.
    The helper-authorized outer namespace clears groups; its controller maps
    an inner namespace with the same IDs after the child irrevocably denies
    setgroups. All capability-bearing controllers remain namespace-only root.
    """
    os.setgroups([])
    if os.getgroups():
        raise NamespaceUnavailable("outer namespace supplementary groups remain")
    ready_read, ready_write = os.pipe()
    mapped_read, mapped_write = os.pipe()
    child = os.fork()
    if child:
        os.close(ready_write)
        os.close(mapped_read)
        try:
            if os.read(ready_read, 1) != b"1":
                raise NamespaceFailure("inner namespace mapping handshake failed")
            identity_map = "0 0 1\n1001 1001 3\n"
            (Path("/proc") / str(child) / "uid_map").write_text(identity_map)
            (Path("/proc") / str(child) / "gid_map").write_text(identity_map)
            os.write(mapped_write, b"1")
            _, result = os.waitpid(child, 0)
            return os.waitstatus_to_exitcode(result)
        finally:
            os.close(ready_read)
            os.close(mapped_write)
    os.close(ready_read)
    os.close(mapped_write)
    try:
        _libc_call("unshare", 0x10000000 | 0x20000 | 0x40000000)  # USER, MOUNT, NET
        Path("/proc/self/setgroups").write_text("deny")
        os.write(ready_write, b"1")
        if os.read(mapped_read, 1) != b"1":
            raise NamespaceFailure("inner namespace mapping was not completed")
    finally:
        os.close(ready_write)
        os.close(mapped_read)
    if (os.getresuid() != (0, 0, 0) or os.getresgid() != (0, 0, 0)
            or os.getgroups() or Path("/proc/self/setgroups").read_text().strip() != "deny"):
        raise NamespaceUnavailable("inner namespace group/identity boundary failed")
    try:
        os.setgroups([])
    except PermissionError:
        pass
    else:
        raise NamespaceFailure("inner namespace setgroups remained permitted")
    return None


def _namespace_child():
    try:
        if os.getuid() == 0:
            raise NamespaceUnavailable("host root launch is forbidden")
        _libc_call("unshare", 0x10000000)  # Outer USER namespace for helper maps.
        print(json.dumps({"mapping_ready": os.getpid()}), flush=True)
        raw = sys.stdin.buffer.readline(MAX_INPUT + 2)
        if len(raw) > MAX_INPUT + 1:
            raise NamespaceFailure("namespace input bound")
        request = json.loads(raw)
        if os.getresuid() != (0, 0, 0) or os.getresgid() != (0, 0, 0):
            raise NamespaceUnavailable("caller mapping did not establish namespace-only root")
        proxy_result = _enter_custody_namespace()
        if proxy_result is not None:
            return proxy_result
        _libc_call("unshare", 0x20000000)  # PID; the next fork becomes namespace init.
        child = os.fork()
        if child:
            _, result = os.waitpid(child, 0)
            return os.waitstatus_to_exitcode(result)
        context = None
        try:
            context = CustodyContext(request)
            _prctl(38, 1)
            def expired(*_):
                raise NamespaceFailure("namespace fixture time bound")
            signal.signal(signal.SIGALRM, expired)
            signal.setitimer(signal.ITIMER_REAL, max(1, request["timeout"] - 2))
            fixture = context.repository / request["fixture"]
            sys.path.insert(0, str(context.repository))
            sys.path.insert(0, str(context.repository / "tests"))
            spec = importlib.util.spec_from_file_location("_explicit_guardian_custody_fixture", fixture)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            result = module.run(context, request["payload"])
            print(json.dumps({"status": "PASSED", "result": result, "roles": context.role_proofs,
                              "namespace_uid_map": Path("/proc/self/uid_map").read_text().splitlines(),
                              "namespace_gid_map": Path("/proc/self/gid_map").read_text().splitlines(),
                              "setgroups": Path("/proc/self/setgroups").read_text().strip(),
                              "network_namespace": os.readlink("/proc/self/ns/net")}, allow_nan=False), flush=True)
            return 0
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            if context is not None:
                context.close()
    except BaseException as exc:
        trace = [{"function": frame.name, "line": frame.lineno}
                 for frame in traceback.extract_tb(exc.__traceback__)[-8:]]
        print(json.dumps({"status": "FAILED", "error_type": type(exc).__name__,
                          "reason": str(exc)[-1000:], "stage_trace": trace}), flush=True)
        return 2


if __name__ == "__main__":
    if len(sys.argv) >= 4 and sys.argv[1] == "--role-child":
        _role_child(sys.argv[2], sys.argv[3:])
    elif sys.argv[1:] == ["--namespace-child"]:
        raise SystemExit(_namespace_child())
    else:
        raise SystemExit("Import run_namespace_fixture() from an explicit custody test; no standalone actions.")
