"""Immutable startup authority and cheap cgroup diagnostics for the builder."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import subprocess

from .universe_failures import SnapshotError


def _signature(path: Path) -> tuple[int, ...]:
    st = path.stat()
    return st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns, st.st_mode


@dataclass
class PinnedProducer:
    sha: str
    marker: Path
    head: Path
    sources: dict[Path, tuple[int, ...]]

    @classmethod
    def capture(cls):
        from .shadow_preflight import attest
        root = Path(__file__).resolve().parents[1]
        try:
            manifest = attest()  # Git/dependency/preflight authority is established ONCE.
        except SystemExit as exc:
            raise SnapshotError("RELEASE_MISMATCH") from exc
        marker = Path(os.environ.get("ALPHA_CONFIG_DIR", "~/.polymarket-edge-scanner")).expanduser() / "release.sha"
        paths = subprocess.check_output(["git", "-C", str(root), "ls-files", "-z"], timeout=3).decode().split("\0")
        guard = cls(manifest["git_head_sha"], marker, root / ".git" / "HEAD",
                    {root / name: _signature(root / name) for name in paths if name})
        guard.check()
        return guard

    def check(self) -> None:
        # Production release-pin requires detached HEAD. No git process occurs
        # during or between builds. File signatures detect checkout/source edits;
        # marker/HEAD checks prohibit publishing under a different release label.
        try:
            if (self.marker.read_text().strip() != self.sha or self.head.read_text().strip() != self.sha
                    or any(_signature(path) != original for path, original in self.sources.items())):
                raise SnapshotError("RELEASE_MISMATCH")
        except OSError as exc:
            raise SnapshotError("RELEASE_MISMATCH") from exc


def cgroup_diagnostics(*, proc_root: Path = Path("/proc"), cgroup_root: Path = Path("/sys/fs/cgroup")) -> dict:
    """Numeric allowlist only. Missing cgroup v2 files are explicit, not zeros."""
    result = {"available": False}
    try:
        relative = next(line[3:] for line in (proc_root / "self/cgroup").read_text().splitlines() if line.startswith("0::"))
        if ".." in Path(relative).parts:
            return result
        directory = cgroup_root / relative.lstrip("/")
        for filename in ("memory.current", "memory.peak", "memory.high", "memory.max", "memory.swap.current"):
            try:
                value = (directory / filename).read_text().strip()
                result[filename] = int(value) if value.isdigit() else None
            except OSError:
                result[filename] = None
        for filename, keys in {
                "memory.events": {"high", "max", "oom", "oom_kill"},
                "memory.stat": {"anon", "file", "kernel", "pgscan", "pgsteal"},
                "cpu.stat": {"usage_usec", "user_usec", "system_usec", "nr_throttled", "throttled_usec"}}.items():
            try:
                result[filename] = {key: int(value) for key, value in
                                    (line.split() for line in (directory / filename).read_text().splitlines()) if key in keys}
            except (OSError, ValueError):
                result[filename] = None
        for filename in ("memory.pressure", "cpu.pressure", "io.pressure"):
            try:
                result[filename] = {parts[0]: {k: float(v) for k, v in
                                              (item.split("=") for item in parts[1:]) if k in {"avg10", "total"}}
                                    for parts in (line.split() for line in (directory / filename).read_text().splitlines())
                                    if parts[0] in {"some", "full"}}
            except (OSError, ValueError):
                result[filename] = None
        result["available"] = result.get("memory.current") is not None
    except (OSError, StopIteration):
        pass
    return result
