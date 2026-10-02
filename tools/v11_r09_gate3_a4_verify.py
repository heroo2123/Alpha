"""Offline A4 candidate: sealed, lock-pinned inputs for an injected test runtime.

This is a boundary for later A2/A3 lock integration, not a qualified decoder or
launch adapter. No provider, network, or live runtime is reachable from here.
The caller must supply the independently reviewed SHA-256 of the exact lock.
"""

from __future__ import annotations

import builtins
import ctypes
import errno
import fcntl
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import types
from dataclasses import dataclass
from pathlib import Path


class VerificationError(RuntimeError):
    pass


_HEX = re.compile(r"[0-9a-f]{64}\Z")
_MODULE = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*\Z")
_KINDS = {"python", "native", "data"}
_SEALS = (fcntl.F_SEAL_SEAL | fcntl.F_SEAL_SHRINK |
          fcntl.F_SEAL_GROW | fcntl.F_SEAL_WRITE)
_GIT_ENV = {"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "LC_ALL": "C",
            "GIT_NO_REPLACE_OBJECTS": "1", "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null",
            "GIT_NO_LAZY_FETCH": "1"}


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise VerificationError(reason)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode()


def _unique_pairs(pairs):
    value = {}
    for key, item in pairs:
        _require(key not in value, "LOCK_DUPLICATE_KEY")
        value[key] = item
    return value


def _nonfinite(_value):
    raise VerificationError("LOCK_NONFINITE")


def _blob_id(content: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest()


def _git(executable_fd: int, isolated_git_dir: Path, objects_fd: int,
         *args: str) -> bytes:
    try:
        return subprocess.run([f"/proc/self/fd/{executable_fd}", "--no-replace-objects",
                               "--git-dir=" + str(isolated_git_dir), *args], env=_GIT_ENV,
                              check=True, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE,
                              pass_fds=(executable_fd, objects_fd)).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise VerificationError("GIT_IDENTITY_UNAVAILABLE") from exc


def _git_object(executable_fd: int, isolated_git_dir: Path, objects_fd: int,
                kind: str, oid: str) -> bytes:
    content = _git(executable_fd, isolated_git_dir, objects_fd,
                   "cat-file", kind, oid)
    actual = hashlib.sha1(kind.encode() + b" " + str(len(content)).encode() +
                          b"\0" + content).hexdigest()
    _require(actual == oid, "GIT_OBJECT_MISMATCH")
    return content


def _isolated_git_store(root_fd: int) -> tuple[tempfile.TemporaryDirectory, int]:
    """Expose only local object bytes to Git, without the source repo's config.

    A bare temporary Git directory has no remote, transport, hook or include
    configuration. Its sole alternate is the held source object directory.
    Repositories using another object store refuse instead of following it.
    """
    try:
        git_fd = os.open(".git", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                         dir_fd=root_fd)
        temporary = None
        try:
            objects_fd = os.open("objects", os.O_RDONLY | os.O_DIRECTORY |
                                 os.O_NOFOLLOW, dir_fd=git_fd)
        finally:
            os.close(git_fd)
        try:
            info_fd = os.open("info", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                              dir_fd=objects_fd)
            try:
                try:
                    alternate_fd = os.open("alternates", os.O_RDONLY | os.O_NOFOLLOW |
                                           os.O_NONBLOCK, dir_fd=info_fd)
                except FileNotFoundError:
                    pass
                else:
                    os.close(alternate_fd)
                    raise VerificationError("GIT_IDENTITY_UNAVAILABLE")
            finally:
                os.close(info_fd)
            temporary = tempfile.TemporaryDirectory(prefix="a4-git-store-")
            directory = Path(temporary.name)
            (directory / "objects" / "info").mkdir(parents=True)
            (directory / "objects" / "pack").mkdir()
            (directory / "refs" / "heads").mkdir(parents=True)
            (directory / "config").write_text("[core]\n\trepositoryformatversion = 0\n\tbare = true\n")
            (directory / "HEAD").write_text("ref: refs/heads/unborn\n")
            (directory / "objects" / "info" / "alternates").write_text(
                f"/proc/self/fd/{objects_fd}\n")
            return temporary, objects_fd
        except BaseException:
            if temporary is not None:
                temporary.cleanup()
            os.close(objects_fd)
            raise
    except OSError as exc:
        raise VerificationError("GIT_IDENTITY_UNAVAILABLE") from exc


def _tree_entry(tree: bytes, name: str) -> tuple[bytes, str]:
    offset = 0
    found = None
    while offset < len(tree):
        end = tree.find(b"\0", offset)
        _require(end >= 0 and end + 21 <= len(tree), "GIT_TREE_INVALID")
        header = tree[offset:end]
        _require(b" " in header, "GIT_TREE_INVALID")
        mode, entry_name = header.split(b" ", 1)
        oid = tree[end + 1:end + 21].hex()
        if entry_name == name.encode():
            _require(found is None, "GIT_TREE_INVALID")
            found = (mode, oid)
        offset = end + 21
    _require(found is not None, "GIT_SOURCE_MISMATCH")
    return found


def _bootstrap_files() -> list[dict]:
    paths = set()
    for module in tuple(sys.modules.values()):
        if module is None:
            continue
        for attribute in ("__file__", "__cached__"):
            path = getattr(module, attribute, None)
            if isinstance(path, str) and path.endswith((".py", ".pyc")):
                if attribute == "__cached__" and not os.path.exists(path):
                    continue
                paths.add(os.path.realpath(path))
    result = []
    for path in sorted(paths):
        try:
            st = os.stat(path, follow_symlinks=False)
            _require(stat.S_ISREG(st.st_mode), "BOOTSTRAP_FILE_UNSAFE")
            with open(path, "rb") as handle:
                content = handle.read()
                _require(os.fstat(handle.fileno()).st_ino == st.st_ino and
                         os.fstat(handle.fileno()).st_dev == st.st_dev,
                         "BOOTSTRAP_FILE_SUBSTITUTED")
        except OSError as exc:
            raise VerificationError("BOOTSTRAP_FILE_MISSING") from exc
        result.append({"path": path, "device": st.st_dev, "inode": st.st_ino,
                       "size": len(content), "sha256": _sha(content)})
    return result


def initialize_run_record(record_path: Path, *, run_id: str, lock_sha256: str) -> None:
    """Create and sync the first-lock record once, before opening a run."""
    _require(type(run_id) is str and re.fullmatch("[A-Za-z0-9_-]{1,128}", run_id),
             "RUN_ID_INVALID")
    _require(type(lock_sha256) is str and _HEX.fullmatch(lock_sha256), "LOCK_PIN_REQUIRED")
    content = _canonical({"run_id": run_id, "lock_sha256": lock_sha256})
    path = Path(record_path).absolute()
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError as exc:
        raise VerificationError("RUN_RECORD_EXISTS") from exc
    try:
        os.write(fd, content)
        os.fsync(fd)
    finally:
        os.close(fd)
    parent_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(parent_fd)
    finally:
        os.close(parent_fd)


def _safe_parts(value: str) -> tuple[str, ...]:
    _require(type(value) is str and value and "\\" not in value and "\x00" not in value,
             "UNSAFE_PATH")
    parts = tuple(value.split("/"))
    _require(all(p not in ("", ".", "..") for p in parts), "UNSAFE_PATH")
    return parts


def _open_nofollow(root_fd: int, relative: str) -> int:
    """Walk every component by directory fd; never resolve a symbolic link."""
    parts = _safe_parts(relative)
    fd = os.dup(root_fd)
    try:
        for part in parts[:-1]:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                              dir_fd=fd)
            os.close(fd)
            fd = next_fd
        result = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                         dir_fd=fd)
        _require(stat.S_ISREG(os.fstat(result).st_mode), "NONREGULAR_INPUT")
        return result
    except OSError as exc:
        if "result" in locals():
            os.close(result)
        raise VerificationError("PATH_UNSAFE_OR_MISSING") from exc
    except BaseException:
        if "result" in locals():
            os.close(result)
        raise
    finally:
        os.close(fd)


def _read_exact(fd: int, size: int) -> bytes:
    _require(type(size) is int and 0 <= size <= 512 * 1024 * 1024, "INPUT_SIZE_LIMIT")
    _require(os.fstat(fd).st_size == size, "INPUT_SIZE_CHANGED")
    chunks = []
    count = 0
    while count < size:
        part = os.read(fd, min(size - count, 1024 * 1024))
        _require(bool(part), "INPUT_TRUNCATED")
        chunks.append(part)
        count += len(part)
    _require(not os.read(fd, 1), "INPUT_GREW")
    return b"".join(chunks)


def _snapshot(label: str, content: bytes) -> int:
    _require(hasattr(os, "memfd_create"), "SEALED_MEMFD_UNAVAILABLE")
    fd = os.memfd_create("a4-" + label[:40], os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
    try:
        view = memoryview(content)
        while view:
            written = os.write(fd, view)
            _require(written > 0, "SNAPSHOT_WRITE_FAILED")
            view = view[written:]
        fcntl.fcntl(fd, fcntl.F_ADD_SEALS, _SEALS)
        _require(fcntl.fcntl(fd, fcntl.F_GET_SEALS) == _SEALS, "SNAPSHOT_UNSEALED")
        os.lseek(fd, 0, os.SEEK_SET)
        return fd
    except BaseException:
        os.close(fd)
        raise


@dataclass(frozen=True)
class _Artifact:
    path: str
    kind: str
    sha256: str
    size: int
    module: str | None
    blob: str | None
    device: int
    inode: int
    original_fd: int
    fd: int


class VerifiedInputs:
    """Preflight all bytes, then expose only sealed handles to the test runtime.

    Lock format is intentionally independent of the pending A2/A3 dossier. A
    translator from a later accepted lock must itself be reviewed. Source
    modules must have an exact Git blob in the pinned commit/tree. The lock is
    accepted only when its entire file SHA-256 equals a caller-supplied pin.
    """

    def __init__(self, lock_path: Path, *, expected_lock_sha256: str,
                 resume_lock_sha256: str | None = None):
        _require(type(expected_lock_sha256) is str and _HEX.fullmatch(expected_lock_sha256),
                 "LOCK_PIN_REQUIRED")
        self._artifacts: dict[str, _Artifact] = {}
        self._modules: dict[str, str] = {}
        self._loaded: dict[str, types.ModuleType] = {}
        self._closed = False
        self._in_child = False
        self._used = False
        self._root_fd: int | None = None
        self._git_fd: int | None = None
        self._git_objects_fd: int | None = None
        self._git_store: tempfile.TemporaryDirectory | None = None
        self._lock_fd: int | None = None
        self._run_fd: int | None = None
        self._lock_path = Path(lock_path).absolute()
        try:
            self._lock_fd = os.open(self._lock_path,
                                    os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            _require(stat.S_ISREG(os.fstat(self._lock_fd).st_mode), "LOCK_NOT_REGULAR")
            raw = _read_exact(self._lock_fd, os.fstat(self._lock_fd).st_size)
            _require(_sha(raw) == expected_lock_sha256, "LOCK_CHANGED")
            _require(resume_lock_sha256 is None or resume_lock_sha256 == expected_lock_sha256,
                     "RESTART_BUILD_CHANGED")
            self.lock_sha256 = expected_lock_sha256
            lock = json.loads(raw, object_pairs_hook=_unique_pairs,
                              parse_constant=_nonfinite)
            _require(_canonical(lock) == raw, "LOCK_NONCANONICAL")
            _require(type(lock) is dict and set(lock) ==
                     {"schema", "root", "commit", "tree", "entrypoint", "environment_sha256",
                      "interpreter", "artifacts", "bootstrap_mappings", "bootstrap_files",
                      "git_tool", "run_id", "run_record"},
                     "LOCK_SHAPE")
            _require(lock["schema"] == "ALPHA_V11_GATE3_A4_OFFLINE_LOCK_V1", "LOCK_SCHEMA")
            self._run_id = lock["run_id"]
            _require(type(self._run_id) is str and
                     re.fullmatch("[A-Za-z0-9_-]{1,128}", self._run_id), "RUN_ID_INVALID")
            _require(type(lock["run_record"]) is str and
                     Path(lock["run_record"]).is_absolute(), "RUN_RECORD_PATH_INVALID")
            self._run_record = Path(lock["run_record"])
            _require(self._run_record == self._lock_path.with_name(
                self._lock_path.name + ".a4-first-lock"), "RUN_RECORD_PATH_INVALID")
            self._check_run_record(first=True)
            self._check_environment(lock["environment_sha256"])
            self.__environment_sha256 = lock["environment_sha256"]
            self._git_fd = self._snapshot_git_tool(lock["git_tool"])
            self.root = Path(lock["root"])
            _require(self.root.is_absolute() and not self.root.is_symlink(), "ROOT_UNSAFE")
            self.root = self.root.resolve(strict=True)
            self._root_fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            self._git_store, self._git_objects_fd = _isolated_git_store(self._root_fd)
            _require(type(lock["commit"]) is str and re.fullmatch("[0-9a-f]{40}", lock["commit"]),
                     "COMMIT_INVALID")
            _require(type(lock["tree"]) is str and re.fullmatch("[0-9a-f]{40}", lock["tree"]),
                     "TREE_INVALID")
            commit_content = self._git_object("commit", lock["commit"])
            _require(commit_content.startswith(b"tree " + lock["tree"].encode() + b"\n"),
                     "TREE_CHANGED")
            self._git_object("tree", lock["tree"])
            self._tree_id = lock["tree"]
            self.entrypoint = lock["entrypoint"]
            _require(type(self.entrypoint) is str and _MODULE.fullmatch(self.entrypoint),
                     "ENTRYPOINT_INVALID")
            self._check_interpreter(lock["interpreter"])
            self.__mappings = lock["bootstrap_mappings"]
            self._check_mappings()
            self.__bootstrap_files = lock["bootstrap_files"]
            self._check_bootstrap_files()
            items = lock["artifacts"]
            _require(type(items) is list and bool(items), "ARTIFACTS_REQUIRED")
            for item in items:
                self._add_artifact(item, lock["commit"])
            _require(self.entrypoint in self._modules, "ENTRYPOINT_UNLOCKED")
            for module in self._modules:
                parent = module.rpartition(".")[0]
                if parent:
                    _require(parent in self._modules, "PARENT_MODULE_UNLOCKED")
            self._check_host_collisions()
        except BaseException:
            self.close()
            raise

    @staticmethod
    def _check_environment(expected: object) -> None:
        _require(type(expected) is str and _HEX.fullmatch(expected), "ENV_SHAPE")
        current = json.dumps(dict(os.environ), sort_keys=True,
                             separators=(",", ":"), ensure_ascii=True).encode()
        _require(_sha(current) == expected, "ENV_OVERRIDE")

    def _git_object(self, kind: str, oid: str) -> bytes:
        return _git_object(self._git_fd, Path(self._git_store.name),
                           self._git_objects_fd, kind, oid)

    def _check_host_collisions(self) -> None:
        for top_level in {name.split(".")[0] for name in self._modules}:
            _require(not any(name == top_level or name.startswith(top_level + ".")
                             for name in sys.modules), "HOST_MODULE_COLLISION")

    @staticmethod
    def _check_interpreter(spec: object) -> None:
        _require(type(spec) is dict and set(spec) == {"sha256", "size"},
                 "INTERPRETER_SHAPE")
        _require(type(spec["sha256"]) is str and _HEX.fullmatch(spec["sha256"]),
                 "INTERPRETER_HASH")
        exe = Path("/proc/self/exe")
        content = exe.read_bytes()
        _require(len(content) == spec["size"] and _sha(content) == spec["sha256"],
                 "INTERPRETER_CHANGED")

    @staticmethod
    def _snapshot_git_tool(spec: object) -> int:
        _require(type(spec) is dict and set(spec) == {"path", "sha256", "size"},
                 "GIT_TOOL_SHAPE")
        path = spec["path"]
        _require(type(path) is str and path.startswith("/") and
                 type(spec["sha256"]) is str and _HEX.fullmatch(spec["sha256"]),
                 "GIT_TOOL_IDENTITY")
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except OSError as exc:
            raise VerificationError("GIT_TOOL_UNAVAILABLE") from exc
        try:
            content = _read_exact(fd, spec["size"])
            _require(_sha(content) == spec["sha256"], "GIT_TOOL_CHANGED")
            return _snapshot("git-tool", content)
        finally:
            os.close(fd)

    def _check_mappings(self) -> None:
        expected = self.__mappings
        _require(type(expected) is list and bool(expected), "MAPPINGS_REQUIRED")
        indexed = {}
        for item in expected:
            _require(type(item) is dict and set(item) == {"path", "sha256", "size"},
                     "MAPPING_SHAPE")
            path = item["path"]
            _require(type(path) is str and path.startswith("/") and path not in indexed and
                     type(item["sha256"]) is str and _HEX.fullmatch(item["sha256"]) and
                     type(item["size"]) is int, "MAPPING_IDENTITY")
            indexed[path] = item
        observed: dict[str, set[tuple[str, int]]] = {}
        for line in Path("/proc/self/maps").read_text().splitlines():
            parts = line.split(maxsplit=5)
            if len(parts) < 6 or "x" not in parts[1] or not parts[5].startswith("/"):
                continue
            path = parts[5]
            _require(not path.endswith(" (deleted)"), "MAPPING_DELETED")
            observed.setdefault(path, set()).add((parts[3], int(parts[4])))
        _require(set(observed) == set(indexed), "MAPPING_SET_CHANGED")
        for path, identities in observed.items():
            _require(len(identities) == 1, "MAPPING_ALIAS_COLLISION")
            device, inode = next(iter(identities))
            try:
                fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            except OSError as exc:
                raise VerificationError("MAPPING_PATH_UNSAFE") from exc
            try:
                st = os.fstat(fd)
                major, minor = device.split(":")
                _require(st.st_dev == os.makedev(int(major, 16), int(minor, 16)) and
                         st.st_ino == inode, "MAPPING_SUBSTITUTED")
                value = indexed[path]
                _require(st.st_size == value["size"] and
                         _sha(_read_exact(fd, value["size"])) == value["sha256"],
                         "MAPPING_CHANGED")
            finally:
                os.close(fd)

    def _check_bootstrap_files(self) -> None:
        expected = self.__bootstrap_files
        _require(type(expected) is list and bool(expected), "BOOTSTRAP_FILES_REQUIRED")
        _require(expected == _bootstrap_files(), "BOOTSTRAP_FILES_CHANGED")

    def _check_run_record(self, *, first: bool = False) -> None:
        try:
            fd = os.open(self._run_record, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                st = os.fstat(fd)
                _require(stat.S_ISREG(st.st_mode), "RUN_RECORD_INVALID")
                if not first:
                    held = os.fstat(self._run_fd)
                    _require((st.st_dev, st.st_ino) == (held.st_dev, held.st_ino),
                             "RUN_RECORD_SUBSTITUTED")
                raw = _read_exact(fd, st.st_size)
                _require(raw == _canonical({"run_id": self._run_id,
                                           "lock_sha256": self.lock_sha256}),
                         "RESTART_BUILD_CHANGED")
                if first:
                    self._run_fd = fd
                    fd = None
            finally:
                if fd is not None:
                    os.close(fd)
        except OSError as exc:
            raise VerificationError("RUN_RECORD_MISSING") from exc

    def _verify_git_path(self, path: str, blob: str, content: bytes) -> None:
        _require(_blob_id(content) == blob, "GIT_SOURCE_MISMATCH")
        oid = self._tree_id
        parts = _safe_parts(path)
        for index, part in enumerate(parts):
            tree = self._git_object("tree", oid)
            mode, oid = _tree_entry(tree, part)
            _require(mode == (b"40000" if index < len(parts) - 1 else b"100644") or
                     (index == len(parts) - 1 and mode == b"100755"),
                     "GIT_SOURCE_MISMATCH")
        _require(oid == blob and self._git_object("blob", oid) == content,
                 "GIT_SOURCE_MISMATCH")

    def _add_artifact(self, item: object, commit: str) -> None:
        _require(type(item) is dict and set(item) ==
                 {"path", "kind", "sha256", "size", "module", "git_blob"}, "ARTIFACT_SHAPE")
        path, kind, digest, size = (item[k] for k in ("path", "kind", "sha256", "size"))
        _safe_parts(path)
        _require(kind in _KINDS and type(digest) is str and _HEX.fullmatch(digest),
                 "ARTIFACT_INVALID")
        _require(type(size) is int and 0 <= size <= 512 * 1024 * 1024,
                 "ARTIFACT_SIZE")
        _require(path not in self._artifacts, "ARTIFACT_DUPLICATE")
        module, blob = item["module"], item["git_blob"]
        if kind == "python":
            _require(type(module) is str and _MODULE.fullmatch(module) and
                     module not in self._modules and type(blob) is str and
                     re.fullmatch("[0-9a-f]{40}", blob), "PYTHON_IDENTITY")
            module_path = module.replace(".", "/")
            _require(path in (module_path + ".py", module_path + "/__init__.py"),
                     "PYTHON_PATH")
        else:
            _require(module is None, "NONPYTHON_MODULE")
            _require(blob is None or (type(blob) is str and re.fullmatch("[0-9a-f]{40}", blob)),
                     "GIT_BLOB_INVALID")
        fd = _open_nofollow(self._root_fd, path)
        retained = False
        try:
            st = os.fstat(fd)
            content = _read_exact(fd, size)
            _require(_sha(content) == digest, "ARTIFACT_CHANGED")
            if blob is not None:
                self._verify_git_path(path, blob, content)
            sealed_fd = _snapshot(path.replace("/", "-"), content)
            self._artifacts[path] = _Artifact(path, kind, digest, size, module, blob,
                                              st.st_dev, st.st_ino, fd, sealed_fd)
            retained = True
            if module is not None:
                self._modules[module] = path
        finally:
            if not retained:
                os.close(fd)

    def _check_live(self, artifact: _Artifact) -> None:
        _require(not self._closed, "SESSION_CLOSED")
        fd = _open_nofollow(self._root_fd, artifact.path)
        try:
            st = os.fstat(fd)
            _require((st.st_dev, st.st_ino) == (artifact.device, artifact.inode),
                     "PATH_SUBSTITUTED")
            _require(_sha(_read_exact(fd, artifact.size)) == artifact.sha256,
                     "ARTIFACT_CHANGED")
            _require(fcntl.fcntl(artifact.fd, fcntl.F_GET_SEALS) == _SEALS,
                     "SNAPSHOT_UNSEALED")
        finally:
            os.close(fd)

    def check_all(self) -> None:
        _require(not self._in_child, "PATH_CHECK_IN_CONFINED_CHILD")
        self._check_lock_path()
        try:
            live_root = self.root.stat()
            held_root = os.fstat(self._root_fd)
        except OSError as exc:
            raise VerificationError("ROOT_SUBSTITUTED") from exc
        _require(not self.root.is_symlink() and
                 (live_root.st_dev, live_root.st_ino) ==
                 (held_root.st_dev, held_root.st_ino), "ROOT_SUBSTITUTED")
        self._check_mappings()
        self._check_bootstrap_files()
        self._check_run_record()
        for artifact in self._artifacts.values():
            self._check_live(artifact)
        self._check_environment(self.__environment_sha256)

    def _check_lock_path(self) -> None:
        try:
            live = os.stat(self._lock_path, follow_symlinks=False)
            held = os.fstat(self._lock_fd)
            _require(stat.S_ISREG(live.st_mode) and
                     (live.st_dev, live.st_ino) == (held.st_dev, held.st_ino),
                     "LOCK_PATH_SUBSTITUTED")
            fd = os.open(self._lock_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                st = os.fstat(fd)
                _require((st.st_dev, st.st_ino) == (held.st_dev, held.st_ino),
                         "LOCK_PATH_SUBSTITUTED")
                content = _read_exact(fd, live.st_size)
                _require(_sha(content) == self.lock_sha256, "LOCK_CHANGED")
            finally:
                os.close(fd)
        except OSError as exc:
            raise VerificationError("LOCK_PATH_SUBSTITUTED") from exc

    def sealed_data(self, path: str) -> int:
        """Return a duplicate sealed fd, never a mutable pathname."""
        item = self._artifacts.get(path)
        _require(item is not None and item.kind == "data", "DATA_NOT_LOCKED")
        if not self._in_child:
            self.check_all()
        return os.dup(item.fd)

    def read_data(self, path: str) -> bytes:
        item = self._artifacts.get(path)
        _require(item is not None and item.kind == "data", "DATA_NOT_LOCKED")
        if not self._in_child:
            self.check_all()
        value = os.pread(item.fd, item.size, 0)
        _require(len(value) == item.size and _sha(value) == item.sha256,
                 "SNAPSHOT_CHANGED")
        return value

    def _import(self, name: str, globals=None, locals=None, fromlist=(), level=0):
        self._check_host_collisions()
        if level:
            package = (globals or {}).get("__package__", "")
            _require(type(package) is str and package, "UNEXPECTED_LAZY_IMPORT")
            parts = package.split(".")
            _require(level <= len(parts), "UNEXPECTED_LAZY_IMPORT")
            base = ".".join(parts[:len(parts) - level + 1])
            name = base + ("." + name if name else "")
        _require(name in self._modules, "UNEXPECTED_LAZY_IMPORT")
        module = self._load_module(name)
        if fromlist:
            for child in fromlist:
                if child == "*":
                    continue
                _require(type(child) is str, "UNEXPECTED_LAZY_IMPORT")
                if name + "." + child in self._modules:
                    self._load_module(name + "." + child)
                else:
                    _require(child in vars(module), "UNEXPECTED_LAZY_IMPORT")
            self._check_host_collisions()
            return module
        top_level = self._load_module(name.split(".")[0])
        self._check_host_collisions()
        return top_level

    def _load_module(self, name: str) -> types.ModuleType:
        if name in self._loaded:
            return self._loaded[name]
        parent = name.rpartition(".")[0]
        if parent:
            self._load_module(parent)
        item = self._artifacts[self._modules[name]]
        if not self._in_child:
            self.check_all()
        source = os.pread(item.fd, item.size, 0)
        _require(_sha(source) == item.sha256, "SNAPSHOT_CHANGED")
        module = types.ModuleType(name)
        module.__package__ = name if item.path.endswith("/__init__.py") else parent
        module.__file__ = "sealed://" + item.path
        if item.path.endswith("/__init__.py"):
            module.__path__ = []
        safe = dict(vars(builtins))
        safe["__import__"] = self._import
        safe.pop("open", None)
        module.__dict__["__builtins__"] = safe
        self._loaded[name] = module
        try:
            exec(compile(source, module.__file__, "exec"), module.__dict__)
        except BaseException:
            self._loaded.pop(name, None)
            raise
        if parent:
            setattr(self._loaded[parent], name.rsplit(".", 1)[1], module)
        return module

    def _run_child(self, function: str, args: tuple):
        _require(type(function) is str and function.isidentifier(), "FUNCTION_INVALID")
        module = self._load_module(self.entrypoint)
        target = getattr(module, function, None)
        _require(callable(target), "ENTRYPOINT_FUNCTION_MISSING")
        return target(self, *args)

    def run(self, function: str = "main", *args):
        """Run one reviewed entrypoint in a child with file opens denied.

        Only JSON-compatible return values cross the pipe. Preflight and final
        path checks happen in the parent; the child uses sealed snapshots only.
        This is an offline test executor, not a provider or launch adapter.
        """
        self.check_all()
        _require(type(function) is str and function.isidentifier(), "FUNCTION_INVALID")
        _require(not self._used, "SESSION_ALREADY_USED")
        lib = _seccomp_library()
        self.check_all()
        self._used = True
        read_fd, write_fd = os.pipe()
        pid = os.fork()
        if pid == 0:
            os.close(read_fd)
            try:
                self._in_child = True
                keep = {0, 1, 2, write_fd} | {item.fd for item in self._artifacts.values()}
                for name in os.listdir("/proc/self/fd"):
                    fd = int(name)
                    if fd not in keep:
                        try:
                            os.close(fd)
                        except OSError:
                            pass
                _deny_new_files(lib)
                self._check_host_collisions()
                value = self._run_child(function, args)
                message = {"ok": True, "value": value}
            except BaseException as exc:
                message = {"ok": False, "error": str(exc), "type": type(exc).__name__}
            try:
                payload = json.dumps(message, sort_keys=True, allow_nan=False).encode()
                _require(len(payload) <= 1024 * 1024, "RESULT_TOO_LARGE")
                view = memoryview(payload)
                while view:
                    written = os.write(write_fd, view)
                    _require(written > 0, "RESULT_PIPE_FAILED")
                    view = view[written:]
            finally:
                os._exit(0)
        os.close(write_fd)
        child_reaped = False
        try:
            parts = []
            total = 0
            while True:
                part = os.read(read_fd, 65536)
                if not part:
                    break
                total += len(part)
                _require(total <= 1024 * 1024, "RESULT_TOO_LARGE")
                parts.append(part)
            _, status = os.waitpid(pid, 0)
            child_reaped = True
            self.check_all()
            _require(os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0,
                     "CONFINED_CHILD_FAILED")
            _require(bool(parts), "CONFINED_CHILD_NO_RESULT")
            message = json.loads(b"".join(parts))
            _require(message.get("ok") is True, message.get("error", "CHILD_REFUSED"))
            return message["value"]
        finally:
            os.close(read_fd)
            if not child_reaped:
                os.waitpid(pid, 0)

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            for item in self._artifacts.values():
                os.close(item.original_fd)
                os.close(item.fd)
            if self._root_fd is not None:
                os.close(self._root_fd)
            if self._git_fd is not None:
                os.close(self._git_fd)
            if self._git_objects_fd is not None:
                os.close(self._git_objects_fd)
            if self._git_store is not None:
                self._git_store.cleanup()
            if self._lock_fd is not None:
                os.close(self._lock_fd)
            if self._run_fd is not None:
                os.close(self._run_fd)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def open_verified(lock_path: Path, *, expected_lock_sha256: str,
                  resume_lock_sha256: str | None = None) -> VerifiedInputs:
    """Pin a lock before any target source is imported or decoded."""
    session = VerifiedInputs(lock_path, expected_lock_sha256=expected_lock_sha256,
                             resume_lock_sha256=resume_lock_sha256)
    return session


def _seccomp_library():
    try:
        # RTLD_NOLOAD must not execute a loader or constructor before the
        # reviewed bootstrap mapping check. A later launcher must arrange its
        # own independently verified bootstrap; this module cannot mint one.
        lib = ctypes.CDLL("libseccomp.so.2", mode=os.RTLD_NOLOAD | os.RTLD_NOW)
    except OSError as exc:
        raise VerificationError("SECCOMP_UNAVAILABLE") from exc
    lib.seccomp_init.argtypes = [ctypes.c_uint32]
    lib.seccomp_init.restype = ctypes.c_void_p
    lib.seccomp_rule_add.argtypes = [ctypes.c_void_p, ctypes.c_uint32,
                                     ctypes.c_int, ctypes.c_uint]
    lib.seccomp_rule_add.restype = ctypes.c_int
    lib.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    lib.seccomp_syscall_resolve_name.restype = ctypes.c_int
    lib.seccomp_load.argtypes = [ctypes.c_void_p]
    lib.seccomp_load.restype = ctypes.c_int
    lib.seccomp_release.argtypes = [ctypes.c_void_p]
    return lib


def _deny_new_files(lib) -> None:
    # Deny paths to new executable/data bytes, process escape, and network.
    # Already-open sealed data fds remain readable. The rule is installed in
    # the child before any locked Python source is compiled or executed.
    ctx = lib.seccomp_init(0x7fff0000)  # SCMP_ACT_ALLOW
    _require(bool(ctx), "SECCOMP_INIT_FAILED")
    try:
        deny = 0x00050000 | errno.EPERM  # SCMP_ACT_ERRNO(EPERM)
        for name in (b"open", b"openat", b"openat2", b"creat",
                     b"execve", b"execveat", b"socket", b"connect",
                     b"fork", b"vfork", b"clone", b"clone3", b"ptrace",
                     b"memfd_create", b"open_by_handle_at", b"name_to_handle_at",
                     b"io_uring_setup", b"io_uring_enter", b"io_uring_register",
                     b"unlink", b"unlinkat", b"rename", b"renameat", b"renameat2",
                     b"link", b"linkat", b"symlink", b"symlinkat",
                     b"mkdir", b"mkdirat", b"rmdir", b"truncate", b"ftruncate",
                     b"chmod", b"fchmod", b"fchmodat", b"fchmodat2",
                     b"chown", b"fchown", b"lchown", b"fchownat",
                     b"setxattr", b"lsetxattr", b"fsetxattr",
                     b"removexattr", b"lremovexattr", b"fremovexattr",
                     b"utime", b"utimes", b"futimesat", b"utimensat",
                     b"mknod", b"mknodat", b"pidfd_open", b"pidfd_getfd",
                     b"process_vm_readv", b"process_vm_writev",
                     b"mount", b"umount2", b"pivot_root", b"setns", b"unshare"):
            number = lib.seccomp_syscall_resolve_name(name)
            _require(number >= 0 and lib.seccomp_rule_add(ctx, deny, number, 0) == 0,
                     "SECCOMP_RULE_FAILED")
        _require(lib.seccomp_load(ctx) == 0, "SECCOMP_LOAD_FAILED")
    finally:
        lib.seccomp_release(ctx)
