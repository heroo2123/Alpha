"""Read-only filesystem admission checks; never deletes or repairs state.

The commissioning policy warns at 70% and stops openings at 85%. Availability
uses the service user's f_bavail, not root-reserved free blocks. This is a
point-in-time guard, not a guarantee against later concurrent disk consumption.
"""
from __future__ import annotations

import os
from pathlib import Path

WARN_PERCENT = 70
STOP_PERCENT = 85


def check_storage(paths: dict[str, Path]) -> dict:
    result = {"openings_allowed": True, "reason": None, "filesystems": []}
    for role, path in paths.items():
        try:
            value = os.statvfs(path)
            blocks, available, unit = value.f_blocks, value.f_bavail, value.f_frsize
            if any(type(n) is not int for n in (blocks, available, unit)):
                raise ValueError
            if blocks <= 0 or unit <= 0 or not 0 <= available <= blocks:
                raise ValueError
            files, free_files = value.f_files, value.f_favail
            if any(type(n) is not int for n in (files, free_files)):
                raise ValueError
            if files < 0 or free_files < 0 or files and free_files > files:
                raise ValueError
            used = blocks - available
            reason = None
            if used * 100 >= blocks * STOP_PERCENT:
                reason = "STORAGE_CAPACITY_OPENING_STOP"
            elif files and free_files == 0:
                reason = "STORAGE_INODES_EXHAUSTED"
            row = {"role": role, "available_bytes": available * unit,
                   "unavailable_percent": round(used * 100 / blocks, 2),
                   "available_inodes": free_files if files else None,
                   "warning": used * 100 >= blocks * WARN_PERCENT,
                   "reason": reason}
        except (OSError, ValueError, TypeError, AttributeError, OverflowError):
            reason = "STORAGE_HEALTH_UNAVAILABLE"
            row = {"role": role, "reason": reason, "warning": True}
        result["filesystems"].append(row)
        if reason:
            result["openings_allowed"] = False
            result["reason"] = result["reason"] or reason
    if not paths:
        result.update(openings_allowed=False, reason="STORAGE_HEALTH_UNAVAILABLE")
    return result
