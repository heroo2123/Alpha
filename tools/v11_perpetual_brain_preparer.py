#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import importlib.util
import json
import math
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

FORWARD_ROOT = Path("/home/alphaadmin/AlphaV11_BrainForward")
WORK_ROOT = Path("/home/alphaadmin/AlphaV11_BrainWork")
SHADOW_ROOT = Path("/home/alphaadmin/AlphaV11_ForwardShadow/continuous-shadow-v2")
PERPETUAL_STATUS = SHADOW_ROOT / "perpetual-preparer-status.json"
STATUS = FORWARD_ROOT / "perpetual-brain-preparer-status.json"
CAPTURE_SCRIPT = FORWARD_ROOT / "capture_forward.py"
PYTHON = "/home/alphaadmin/AlphaV11_Dev/venv/bin/python"
PYTHONPATH = "/home/alphaadmin/AlphaV11_ShadowRuntime"
ATL = ZoneInfo("America/New_York")
MIN_FREE_BYTES = 2_500_000_000
MAX_CENSUS_STEPS = 480
RUN_LAG_SECONDS = 12 * 3600

def atomic_json(path: Path, value: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)

def day_start(day: str) -> float:
    d = datetime.strptime(day, "%Y-%m-%d").date()
    return datetime.combine(d, datetime.min.time(), tzinfo=ATL).timestamp()

def choose_gefs_init(now: float, target_start: float) -> int:
    # Use a deliberately mature 6-hour GEFS cycle. This mirrors the successful
    # Oct-7 capture (Oct-5 00Z when staged on Oct-5) and avoids chasing a run
    # that may still be publishing.
    mature = now - RUN_LAG_SECONDS
    init = int(math.floor(mature / (6 * 3600)) * (6 * 3600))
    if init <= 0 or init >= target_start:
        raise RuntimeError("NO_MATURE_PRE_DAY_GEFS_RUN")
    if target_start + 26 * 3600 - init > 240 * 3600:
        raise RuntimeError("GEFS_RUN_CANNOT_COVER_TARGET_DAY")
    return init

def sqlite_snapshot(src: Path, dst: Path) -> None:
    tmp = dst.with_suffix(dst.suffix + ".tmp")
    if tmp.exists():
        tmp.unlink()
    s = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    d = sqlite3.connect(tmp)
    try:
        s.backup(d)
        d.commit()
    finally:
        d.close()
        s.close()
    os.chmod(tmp, 0o600)
    os.replace(tmp, dst)

def adapt_runner(source_text: str, day: str, event_id: str, init: int, root: Path, db_name: str, config_name: str) -> str:
    text = source_text
    text, n = re.subn(
        r"ROOT=Path\('[^']+'\)\nDB=ROOT/'[^']+'",
        f"ROOT=Path('{root}')\nDB=ROOT/'{db_name}'",
        text,
        count=1,
    )
    if n != 1:
        raise RuntimeError("RUNNER_ROOT_DB_ADAPTATION_FAILED")
    if f"EVENT_ID='{event_id}'" not in text:
        raise RuntimeError("RUNNER_EVENT_ID_MISMATCH")
    text, n1 = re.subn(
        r"static=json\.loads\(\(ROOT/'candidate-static-config(?:-[0-9-]+)?\.json'\)\.read_text\(\)\)",
        f"static=json.loads((ROOT/'{config_name}').read_text())",
        text,
        count=1,
    )
    text, n2 = re.subn(
        r"expected=json\.loads\(\(ROOT/'candidate-static-config(?:-[0-9-]+)?\.json'\)\.read_text\(\)\)",
        f"expected=json.loads((ROOT/'{config_name}').read_text())",
        text,
        count=1,
    )
    if n1 != 1 or n2 != 1:
        raise RuntimeError("RUNNER_CONFIG_ADAPTATION_FAILED")
    text, n3 = re.subn(
        r"gefs=GEFSPlan\(forecast,[0-9]+(?:\.0)?,86400\.\)",
        f"gefs=GEFSPlan(forecast,{init},86400.)",
        text,
        count=1,
    )
    if n3 != 1:
        raise RuntimeError("RUNNER_GEFS_ADAPTATION_FAILED")
    return text

def load_module(path: Path, day: str):
    spec = importlib.util.spec_from_file_location("perpetual_brain_" + day.replace("-", ""), path)
    if spec is None or spec.loader is None:
        raise RuntimeError("BRAIN_MODULE_LOAD_FAILED")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

async def collect_source(module_path: Path, day: str, ready_path: Path) -> dict:
    m = load_module(module_path, day)
    async with httpx.AsyncClient(
        headers={"User-Agent": "Alpha-V11-perpetual-brain-source/1"},
        timeout=10.0,
        follow_redirects=False,
    ) as client:
        store, plan, runner, shadow, gefs = m.build_runner(client)
        try:
            runner.runtime.queue.schedule_census("perpetual-brain:" + day + ":schedule")
        except Exception as exc:
            msg = str(exc)
            if "REPLAY" not in msg and "already" not in msg.lower():
                raise
        for i in range(MAX_CENSUS_STEPS):
            snap = runner.runtime.queue.snapshot()
            prep = snap.get("model_preparations", {}).get(m.EVENT_ID)
            need = (
                m.EVENT_ID in snap.get("needs_census", {})
                or prep is not None
                or (snap.get("active") or {}).get("event_id") == m.EVENT_ID
            )
            if not need:
                latest = store.latest_source(
                    kind="MODEL",
                    event_id=m.EVENT_ID,
                    provider="NOAA_GEFS_0P50",
                    source_identity=gefs.source_identity,
                )
                if latest is None:
                    raise RuntimeError("BRAIN_FORWARD_CENSUS_CLEARED_WITHOUT_MODEL")
                out = {
                    "state": "READY",
                    "date": day,
                    "event_id": m.EVENT_ID,
                    "model_id": latest["id"],
                    "model_sha256": latest["sha256"],
                    "received_at": latest["body"]["received_at"],
                    "issued_at": latest["body"]["issued_at"],
                    "gefs_initialization": gefs.initialized_at,
                    "financial_authority": False,
                }
                atomic_json(ready_path, out)
                return out
            cycle = f"perpetual-brain:{day}:census:{i}:{int(time.time())}"
            row = await runner.census.step(cycle)
            outcome = row["body"]["details"].get("outcome")
            if outcome == "MODEL_CENSUS_COLLECTION_PENDING":
                await asyncio.sleep(1.05)
            elif outcome == "MODEL_CENSUS_GATED":
                await asyncio.sleep(5)
            else:
                await asyncio.sleep(1)
    raise RuntimeError("BRAIN_FORWARD_COLLECTION_ITERATION_BOUND")

def run_capture(day: str) -> dict:
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": "/home/alphaadmin",
        "PYTHONPATH": PYTHONPATH,
        "LC_ALL": "C.UTF-8",
    }
    p = subprocess.run(
        [PYTHON, str(CAPTURE_SCRIPT), day],
        cwd=FORWARD_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=120,
        check=False,
    )
    if p.returncode != 0:
        raise RuntimeError("CAPTURE_FORWARD_FAILED:" + p.stdout[-2000:])
    cap = FORWARD_ROOT / day / "capture-status.json"
    if not cap.exists():
        raise RuntimeError("CAPTURE_STATUS_MISSING")
    return json.loads(cap.read_text())

async def stage_day(row: dict) -> dict:
    day = str(row["date"])
    event_id = str(row.get("event_id") or "")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day) or not event_id.isdigit():
        raise RuntimeError("PREPARED_DAY_IDENTITY_INVALID")
    dest = FORWARD_ROOT / day
    if (dest / "capture-status.json").exists():
        return {"date": day, "state": "ALREADY_CAPTURED", "financial_authority": False}
    start = day_start(day)
    now = time.time()
    if now >= start:
        return {
            "date": day,
            "state": "WINDOW_CLOSED_NO_RETROACTIVE_CAPTURE",
            "financial_authority": False,
        }
    if shutil.disk_usage("/").free < MIN_FREE_BYTES:
        raise RuntimeError("DISK_HEADROOM_GATED")

    runner_path = Path(str(row.get("module") or ""))
    daily_db = SHADOW_ROOT / f"daily-{day}.sqlite"
    daily_cfg = SHADOW_ROOT / f"candidate-static-config-{day}.json"
    if not runner_path.is_file() or not daily_db.is_file() or not daily_cfg.is_file():
        raise RuntimeError("PREPARED_SHADOW_ARTIFACTS_MISSING")

    os.makedirs(WORK_ROOT, mode=0o700, exist_ok=True)
    os.chmod(WORK_ROOT, 0o700)
    init = choose_gefs_init(now, start)
    stem = "katl-" + day
    work_db = WORK_ROOT / f"{stem}-source.sqlite"
    work_module = WORK_ROOT / f"{stem}-brain_source.py"
    work_cfg = WORK_ROOT / f"{stem}-candidate-static-config.json"
    ready = WORK_ROOT / f"{stem}-source-ready.json"

    if not work_db.exists():
        sqlite_snapshot(daily_db, work_db)
    if not work_cfg.exists():
        shutil.copy2(daily_cfg, work_cfg)
        os.chmod(work_cfg, 0o600)
    if not work_module.exists():
        adapted = adapt_runner(
            runner_path.read_text(),
            day,
            event_id,
            init,
            WORK_ROOT,
            work_db.name,
            work_cfg.name,
        )
        work_module.write_text(adapted)
        os.chmod(work_module, 0o600)

    if not ready.exists():
        source_ready = await collect_source(work_module, day, ready)
    else:
        source_ready = json.loads(ready.read_text())
    if source_ready.get("state") != "READY":
        raise RuntimeError("SOURCE_NOT_READY")

    os.makedirs(dest, mode=0o700, exist_ok=True)
    os.chmod(dest, 0o700)
    dest_db = dest / "source.sqlite"
    if not dest_db.exists():
        sqlite_snapshot(work_db, dest_db)
    dest_cfg = dest / "candidate-static-config.json"
    if not dest_cfg.exists():
        shutil.copy2(work_cfg, dest_cfg)
        os.chmod(dest_cfg, 0o600)
    dest_module = dest / "brain_source.py"
    if not dest_module.exists():
        adapted_dest = adapt_runner(
            runner_path.read_text(),
            day,
            event_id,
            int(source_ready["gefs_initialization"]),
            dest,
            dest_db.name,
            dest_cfg.name,
        )
        dest_module.write_text(adapted_dest)
        os.chmod(dest_module, 0o600)
    atomic_json(dest / "source-ready.json", source_ready)
    capture = run_capture(day)
    prep = {
        "version": "alpha_v11_perpetual_brain_day_staging_v1",
        "state": "FORWARD_CAPTURED_WAITING_LABEL",
        "date": day,
        "event_id": event_id,
        "gefs_initialization": source_ready["gefs_initialization"],
        "brain_capture_id": capture["capture_id"],
        "brain_capture_sha256": capture["capture_sha256"],
        "brain_model_id": capture["model_id"],
        "brain_model_sha256": capture["model_sha256"],
        "complete_event_vector": capture["complete_event_vector"],
        "labels_created": False,
        "financial_authority": False,
        "automatic_promotion": False,
        "source_database": str(dest_db),
        "source_module": str(dest_module),
        "prepared_at": time.time(),
    }
    atomic_json(dest / "prep-status.json", prep)
    return {"date": day, "state": prep["state"], "capture_id": capture["capture_id"], "financial_authority": False}

async def main() -> None:
    FORWARD_ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(FORWARD_ROOT, 0o700)
    if not PERPETUAL_STATUS.exists():
        atomic_json(STATUS, {"state": "WAITING_PERPETUAL_STATUS", "at": time.time(), "financial_authority": False})
        return
    p = json.loads(PERPETUAL_STATUS.read_text())
    candidates = [
        row for row in p.get("results", [])
        if row.get("state") in {"PREPARED", "ALREADY_REVIEWED"} and row.get("event_id")
    ]
    results = []
    for row in candidates:
        try:
            results.append(await stage_day(row))
        except Exception as exc:
            results.append({
                "date": row.get("date"),
                "state": "GATED",
                "error_type": type(exc).__name__,
                "error": str(exc),
                "financial_authority": False,
            })
    atomic_json(STATUS, {
        "version": "alpha_v11_perpetual_brain_preparer_v1",
        "at": time.time(),
        "results": results,
        "financial_authority": False,
        "automatic_promotion": False,
    })
    print(json.dumps(results, sort_keys=True))

if __name__ == "__main__":
    asyncio.run(main())
