from __future__ import annotations

"""Secret-free point-in-time process/host telemetry for shadow-run evidence."""

import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

RESOURCE_SNAPSHOT_VERSION = "linux_proc_v1"
_KIB = 1024


def _status_bytes(status_text: str, key: str) -> int | None:
    prefix = f"{key}:"
    for line in status_text.splitlines():
        if not line.startswith(prefix):
            continue
        parts = line[len(prefix):].strip().split()
        if not parts:
            return None
        try:
            value = int(parts[0])
        except ValueError:
            return None
        unit = parts[1].lower() if len(parts) > 1 else ""
        if unit in {"kb", "kib"}:
            return value * _KIB
        return value
    return None


def _status_int(status_text: str, key: str) -> int | None:
    prefix = f"{key}:"
    for line in status_text.splitlines():
        if line.startswith(prefix):
            try:
                return int(line[len(prefix):].strip().split()[0])
            except (ValueError, IndexError):
                return None
    return None


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size if path.is_file() else 0
    except OSError:
        return 0


def runtime_resource_snapshot(
    *,
    db_path: str | Path | None = None,
    proc_root: str | Path = "/proc",
    disk_path: str | Path | None = None,
) -> dict:
    """Collect cheap local resource evidence without raising on missing procfs fields.

    Values describe this process/host at one instant. They are useful for shadow-run
    capacity evidence but are not a substitute for GCP billing/network telemetry.
    """
    proc = Path(proc_root)
    errors: list[str] = []
    status_text = ""
    try:
        status_text = (proc / "self" / "status").read_text(encoding="utf-8")
    except OSError as exc:
        errors.append(f"proc status unavailable: {type(exc).__name__}")

    load_1 = load_5 = load_15 = None
    try:
        parts = (proc / "loadavg").read_text(encoding="utf-8").split()
        if len(parts) >= 3:
            load_1, load_5, load_15 = (float(parts[0]), float(parts[1]), float(parts[2]))
    except (OSError, ValueError) as exc:
        errors.append(f"loadavg unavailable: {type(exc).__name__}")

    open_fds = None
    try:
        open_fds = sum(1 for _ in (proc / "self" / "fd").iterdir())
    except OSError as exc:
        errors.append(f"fd count unavailable: {type(exc).__name__}")

    db = Path(db_path).expanduser().resolve() if db_path is not None else None
    target_disk = (
        Path(disk_path).expanduser().resolve()
        if disk_path is not None
        else (db.parent if db is not None else Path.cwd())
    )
    disk_total = disk_used = disk_free = None
    try:
        usage = shutil.disk_usage(target_disk)
        disk_total, disk_used, disk_free = usage.total, usage.used, usage.free
    except OSError as exc:
        errors.append(f"disk usage unavailable: {type(exc).__name__}")

    db_bytes = wal_bytes = shm_bytes = 0
    if db is not None:
        db_bytes = _file_size(db)
        wal_bytes = _file_size(db.with_name(db.name + "-wal"))
        shm_bytes = _file_size(db.with_name(db.name + "-shm"))

    return {
        "version": RESOURCE_SNAPSHOT_VERSION,
        "measured_at": datetime.now(timezone.utc).isoformat(),
        "scope": "LOCAL_PROCESS_AND_HOST_POINT_IN_TIME_NOT_CLOUD_BILLING",
        "procfs_available": bool(status_text),
        "process_rss_bytes": _status_bytes(status_text, "VmRSS") if status_text else None,
        "process_virtual_bytes": _status_bytes(status_text, "VmSize") if status_text else None,
        "process_swap_bytes": _status_bytes(status_text, "VmSwap") if status_text else None,
        "process_threads": _status_int(status_text, "Threads") if status_text else None,
        "open_file_descriptors": open_fds,
        "logical_cpu_count": os.cpu_count(),
        "load_average_1m": load_1,
        "load_average_5m": load_5,
        "load_average_15m": load_15,
        "disk_path": str(target_disk),
        "disk_total_bytes": disk_total,
        "disk_used_bytes": disk_used,
        "disk_free_bytes": disk_free,
        "database_bytes": db_bytes,
        "database_wal_bytes": wal_bytes,
        "database_shm_bytes": shm_bytes,
        "errors": errors,
    }
