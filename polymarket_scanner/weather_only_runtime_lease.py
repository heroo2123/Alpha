from __future__ import annotations

"""Process-lifetime singleton lease for the isolated weather PAPER ledger.

Preflight process checks have an unavoidable time-of-check/time-of-use gap.  The
canonical runtime therefore holds a non-blocking OS file lock in the same state
directory for its entire lifetime.  A second writer cannot start even if it appears
after preflight but before the first scan.

The lease is re-entrant only inside the same process for the same database path.  This
allows layered runtime classes to enforce the same singleton boundary independently
without deadlocking each other.  A different process still has to acquire the kernel
flock and therefore fails closed while the owner is alive.
"""

import fcntl
import json
import os
import time
from pathlib import Path


RUNTIME_LEASE_VERSION = "weather_paper_runtime_lease_v2_process_reentrant_exclusive_flock"


class WeatherPaperRuntimeLeaseError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


# Re-entrancy is intentionally process-local.  The kernel flock remains the
# cross-process authority.  Values are mutable dictionaries so nested leases can
# share one file handle and a reference count.
_LOCAL_LEASES: dict[str, dict[str, object]] = {}


class WeatherPaperRuntimeLease:
    def __init__(self, db_path: str | Path) -> None:
        db = Path(db_path).expanduser().resolve()
        state_dir = db.parent
        state_dir.mkdir(parents=True, exist_ok=True)
        self.path = state_dir / "weather-paper-runtime.lock"
        self._key = str(self.path)
        self._pid = os.getpid()
        self._closed = False

        existing = _LOCAL_LEASES.get(self._key)
        if existing is not None and int(existing.get("pid") or -1) == self._pid:
            handle = existing.get("handle")
            if handle is not None and not getattr(handle, "closed", True):
                existing["count"] = int(existing.get("count") or 0) + 1
                self._handle = handle
                return
            _LOCAL_LEASES.pop(self._key, None)

        self._handle = self.path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self._handle.close()
            self._closed = True
            raise WeatherPaperRuntimeLeaseError("WEATHER_PAPER_RUNTIME_ALREADY_OWNED") from None
        except OSError:
            self._handle.close()
            self._closed = True
            raise WeatherPaperRuntimeLeaseError("WEATHER_PAPER_RUNTIME_LEASE_FAILED") from None
        try:
            self._handle.seek(0)
            self._handle.truncate(0)
            self._handle.write(json.dumps({
                "version": RUNTIME_LEASE_VERSION,
                "pid": self._pid,
                "db_path": str(db),
                "acquired_at": time.time(),
            }, sort_keys=True) + "\n")
            self._handle.flush()
            os.fsync(self._handle.fileno())
            os.chmod(self.path, 0o600)
            _LOCAL_LEASES[self._key] = {
                "pid": self._pid,
                "handle": self._handle,
                "count": 1,
            }
        except Exception:
            try:
                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
            finally:
                self._handle.close()
                self._closed = True
            raise

    @property
    def acquired(self) -> bool:
        if self._closed:
            return False
        entry = _LOCAL_LEASES.get(self._key)
        return (
            entry is not None
            and int(entry.get("pid") or -1) == os.getpid()
            and int(entry.get("count") or 0) > 0
            and entry.get("handle") is self._handle
            and not getattr(self._handle, "closed", True)
        )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        entry = _LOCAL_LEASES.get(self._key)
        if (
            entry is None
            or int(entry.get("pid") or -1) != os.getpid()
            or entry.get("handle") is not self._handle
        ):
            # Defensive only: never unlock a handle whose registry ownership is not
            # provably this process's current lease generation.
            return
        count = int(entry.get("count") or 0)
        if count > 1:
            entry["count"] = count - 1
            return
        _LOCAL_LEASES.pop(self._key, None)
        handle = self._handle
        if handle is None or getattr(handle, "closed", True):
            return
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()

    def __enter__(self) -> "WeatherPaperRuntimeLease":
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        self.close()
