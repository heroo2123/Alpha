from __future__ import annotations

"""Process-lifetime singleton lease for the isolated weather PAPER ledger.

Preflight process checks have an unavoidable time-of-check/time-of-use gap.  Every
retained weather-paper writer therefore holds the same non-blocking OS file lock in
the database state directory for its entire lifetime.  A second writer cannot start,
even if it appears after preflight but before the first scan.
"""

import fcntl
import json
import os
import time
from pathlib import Path


RUNTIME_LEASE_VERSION = "weather_paper_runtime_lease_v1_exclusive_flock"


class WeatherPaperRuntimeLeaseError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


class WeatherPaperRuntimeLease:
    def __init__(self, db_path: str | Path) -> None:
        db = Path(db_path).expanduser().resolve()
        state_dir = db.parent
        state_dir.mkdir(parents=True, exist_ok=True)
        self.path = state_dir / "weather-paper-runtime.lock"
        self._handle = self.path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self._handle.close()
            raise WeatherPaperRuntimeLeaseError("WEATHER_PAPER_RUNTIME_ALREADY_OWNED") from None
        except OSError:
            self._handle.close()
            raise WeatherPaperRuntimeLeaseError("WEATHER_PAPER_RUNTIME_LEASE_FAILED") from None
        try:
            self._handle.seek(0)
            self._handle.truncate(0)
            self._handle.write(json.dumps({
                "version": RUNTIME_LEASE_VERSION,
                "pid": os.getpid(),
                "db_path": str(db),
                "acquired_at": time.time(),
            }, sort_keys=True) + "\n")
            self._handle.flush()
            os.fsync(self._handle.fileno())
            os.chmod(self.path, 0o600)
        except Exception:
            self.close()
            raise

    @property
    def acquired(self) -> bool:
        return self._handle is not None and not self._handle.closed

    def close(self) -> None:
        handle = self._handle
        if handle is None or handle.closed:
            return
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()

    def __enter__(self) -> "WeatherPaperRuntimeLease":
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        self.close()
