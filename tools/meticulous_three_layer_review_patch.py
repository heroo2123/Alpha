from __future__ import annotations

from pathlib import Path


def rep(path: str, old: str, new: str, count: int = 1) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    found = text.count(old)
    if found < count:
        raise SystemExit(
            f"{path}: expected at least {count} occurrences, found {found}: {old[:100]!r}"
        )
    p.write_text(text.replace(old, new, count), encoding="utf-8")


# 1. Combined Layer-2 + Layer-3 evidence cannot predate its latest required component.
rep(
    "polymarket_scanner/weather_only_three_layer.py",
    "issued_at=min(float(near.issued_at), float(ensemble_path.issued_at)),",
    "issued_at=max(float(near.issued_at), float(ensemble_path.issued_at)),",
)

# 2. Explicit fail-closed NWS Layer-2 freshness policy.
rep(
    "polymarket_scanner/weather_only_nws_near_term.py",
    'NWS_NEAR_TERM_HYPOTHESIS = "NWS_GRID_INTERVAL_VALUE_SAMPLED_15MIN_LEFT_CLOSED_V1"\nMAX_RESPONSE_BYTES = 2 * 1024 * 1024',
    'NWS_NEAR_TERM_HYPOTHESIS = "NWS_GRID_INTERVAL_VALUE_SAMPLED_15MIN_LEFT_CLOSED_V1"\n'
    '# Engineering freshness gate for the short-horizon Layer-2 lane. This is a\n'
    '# conservative research policy, not a claim about NWS update cadence. Older grids\n'
    '# fail closed rather than being treated as current near-term evidence.\n'
    'NWS_NEAR_TERM_MAX_UPDATE_AGE_SECONDS = 6 * 3600.0\n'
    'MAX_RESPONSE_BYTES = 2 * 1024 * 1024',
)
rep(
    "polymarket_scanner/weather_only_nws_near_term.py",
    '    if update_at > receipt + 1e-6:\n'
    '        raise NWSNearTermError("NWS_NEAR_TERM_UPDATE_AFTER_RECEIPT")\n\n'
    '    temperature = properties.get("temperature")',
    '    if update_at > receipt + 1e-6:\n'
    '        raise NWSNearTermError("NWS_NEAR_TERM_UPDATE_AFTER_RECEIPT")\n'
    '    update_age = float(segment.start) - update_at\n'
    '    if update_age > NWS_NEAR_TERM_MAX_UPDATE_AGE_SECONDS + 1e-6:\n'
    '        raise NWSNearTermError("NWS_NEAR_TERM_UPDATE_STALE")\n\n'
    '    temperature = properties.get("temperature")',
)

# 3. Durable same-day attempt audit and hard storage limits.
rep(
    "polymarket_scanner/weather_only_same_day_capture_store.py",
    "import json\nimport os\nimport sqlite3",
    "import json\nimport math\nimport os\nimport sqlite3\nimport time",
)
rep(
    "polymarket_scanner/weather_only_same_day_capture_store.py",
    'SAME_DAY_CAPTURE_STORE_VERSION = "weather_same_day_capture_store_v2_persistent_cadence"\n',
    'SAME_DAY_CAPTURE_STORE_VERSION = "weather_same_day_capture_store_v3_attempt_audit_capacity"\n'
    'SAME_DAY_ATTEMPT_VERSION = "weather_same_day_capture_attempt_v1"\n'
    '_ATTEMPT_FINAL_OUTCOMES = {"SAVED", "DUPLICATE", "FAILED"}\n\n\n'
    'def _optional_positive_int(value: int | None, code: str) -> int | None:\n'
    '    if value is None:\n'
    '        return None\n'
    '    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:\n'
    '        raise SameDayCaptureStoreError(code)\n'
    '    return value\n\n\n'
    'def _nonnegative_time(value: object, code: str) -> float:\n'
    '    if isinstance(value, bool) or not isinstance(value, (int, float)):\n'
    '        raise SameDayCaptureStoreError(code)\n'
    '    number = float(value)\n'
    '    if not math.isfinite(number) or number < 0.0:\n'
    '        raise SameDayCaptureStoreError(code)\n'
    '    return number\n',
)
rep(
    "polymarket_scanner/weather_only_same_day_capture_store.py",
    "class SameDayCaptureStore:\n    def __init__(self, path: str | Path) -> None:\n        self.path = Path(path)",
    "class SameDayCaptureStore:\n"
    "    def __init__(\n"
    "        self,\n"
    "        path: str | Path,\n"
    "        *,\n"
    "        max_capture_rows: int | None = None,\n"
    "        max_capture_json_bytes: int | None = None,\n"
    "        max_attempt_rows: int | None = None,\n"
    "    ) -> None:\n"
    "        self.path = Path(path)\n"
    "        self.max_capture_rows = _optional_positive_int(\n"
    '            max_capture_rows, "SAME_DAY_CAPTURE_STORE_MAX_CAPTURE_ROWS_INVALID"\n'
    "        )\n"
    "        self.max_capture_json_bytes = _optional_positive_int(\n"
    '            max_capture_json_bytes, "SAME_DAY_CAPTURE_STORE_MAX_CAPTURE_BYTES_INVALID"\n'
    "        )\n"
    "        self.max_attempt_rows = _optional_positive_int(\n"
    '            max_attempt_rows, "SAME_DAY_CAPTURE_STORE_MAX_ATTEMPT_ROWS_INVALID"\n'
    "        )",
)
rep(
    "polymarket_scanner/weather_only_same_day_capture_store.py",
    "                CREATE INDEX IF NOT EXISTS idx_weather_same_day_capture_status\n"
    "                    ON weather_same_day_captures(status,id);\n"
    '                """',
    "                CREATE INDEX IF NOT EXISTS idx_weather_same_day_capture_status\n"
    "                    ON weather_same_day_captures(status,id);\n\n"
    "                CREATE TABLE IF NOT EXISTS weather_same_day_capture_attempts (\n"
    "                    id INTEGER PRIMARY KEY AUTOINCREMENT,\n"
    "                    attempt_version TEXT NOT NULL,\n"
    "                    event_id TEXT NOT NULL,\n"
    "                    station TEXT NOT NULL,\n"
    "                    target_date TEXT NOT NULL,\n"
    "                    family TEXT NOT NULL,\n"
    "                    unit TEXT NOT NULL,\n"
    "                    attempted_at REAL NOT NULL,\n"
    "                    completed_at REAL,\n"
    "                    outcome TEXT NOT NULL CHECK(outcome IN ('STARTED','SAVED','DUPLICATE','FAILED')),\n"
    "                    error_code TEXT,\n"
    "                    capture_sha256 TEXT,\n"
    "                    included_in_validated_pnl INTEGER NOT NULL DEFAULT 0 CHECK(included_in_validated_pnl=0),\n"
    "                    same_day_delivery_enabled INTEGER NOT NULL DEFAULT 0 CHECK(same_day_delivery_enabled=0),\n"
    "                    financial_authority INTEGER NOT NULL DEFAULT 0 CHECK(financial_authority=0)\n"
    "                );\n"
    "                CREATE INDEX IF NOT EXISTS idx_weather_same_day_attempt_event_time\n"
    "                    ON weather_same_day_capture_attempts(event_id,attempted_at);\n"
    "                CREATE INDEX IF NOT EXISTS idx_weather_same_day_attempt_outcome\n"
    "                    ON weather_same_day_capture_attempts(outcome,id);\n"
    '                """',
)
rep(
    "polymarket_scanner/weather_only_same_day_capture_store.py",
    "        with self._conn() as db:\n"
    "            try:\n"
    "                cur = db.execute(\n"
    '                    """\n'
    "                    INSERT INTO weather_same_day_captures(",
    "        with self._conn() as db:\n"
    "            existing = db.execute(\n"
    '                "SELECT id FROM weather_same_day_captures WHERE capture_sha256=?",\n'
    "                (value.capture_sha256,),\n"
    "            ).fetchone()\n"
    "            if existing is not None:\n"
    "                return None\n"
    "            capacity = db.execute(\n"
    '                "SELECT COUNT(*) AS rows, COALESCE(SUM(LENGTH(capture_json)),0) AS bytes "\n'
    '                "FROM weather_same_day_captures"\n'
    "            ).fetchone()\n"
    '            current_rows = int(capacity["rows"] or 0)\n'
    '            current_bytes = int(capacity["bytes"] or 0)\n'
    "            if self.max_capture_rows is not None and current_rows >= self.max_capture_rows:\n"
    '                raise SameDayCaptureStoreError("SAME_DAY_CAPTURE_STORE_CAPTURE_ROW_CAP_EXHAUSTED")\n'
    "            if (\n"
    "                self.max_capture_json_bytes is not None\n"
    "                and current_bytes + len(payload) > self.max_capture_json_bytes\n"
    "            ):\n"
    '                raise SameDayCaptureStoreError("SAME_DAY_CAPTURE_STORE_CAPTURE_BYTE_CAP_EXHAUSTED")\n'
    "            try:\n"
    "                cur = db.execute(\n"
    '                    """\n'
    "                    INSERT INTO weather_same_day_captures(",
)

attempt_methods = '''
    def start_attempt(
        self,
        *,
        event_id: str,
        station: str,
        target_date: str,
        family: str,
        unit: str,
        attempted_at: float,
    ) -> int:
        identity = str(event_id or "").strip()
        station_id = str(station or "").strip().upper()
        target = str(target_date or "").strip()
        family_id = str(family or "").strip()
        unit_id = str(unit or "").strip()
        if not all((identity, station_id, target, family_id, unit_id)):
            raise SameDayCaptureStoreError("SAME_DAY_CAPTURE_STORE_ATTEMPT_IDENTITY_INVALID")
        started = _nonnegative_time(attempted_at, "SAME_DAY_CAPTURE_STORE_ATTEMPT_TIME_INVALID")
        with self._conn() as db:
            row = db.execute(
                "SELECT COUNT(*) AS total FROM weather_same_day_capture_attempts"
            ).fetchone()
            total = int(row["total"] or 0)
            if self.max_attempt_rows is not None and total >= self.max_attempt_rows:
                raise SameDayCaptureStoreError("SAME_DAY_CAPTURE_STORE_ATTEMPT_ROW_CAP_EXHAUSTED")
            cur = db.execute(
                """
                INSERT INTO weather_same_day_capture_attempts(
                    attempt_version,event_id,station,target_date,family,unit,
                    attempted_at,completed_at,outcome,error_code,capture_sha256,
                    included_in_validated_pnl,same_day_delivery_enabled,financial_authority
                ) VALUES(?,?,?,?,?,?,?,NULL,'STARTED',NULL,NULL,0,0,0)
                """,
                (
                    SAME_DAY_ATTEMPT_VERSION,
                    identity,
                    station_id,
                    target,
                    family_id,
                    unit_id,
                    started,
                ),
            )
        return int(cur.lastrowid)

    def finish_attempt(
        self,
        attempt_id: int,
        *,
        outcome: str,
        completed_at: float | None = None,
        error_code: str | None = None,
        capture_sha256: str | None = None,
    ) -> None:
        if isinstance(attempt_id, bool) or not isinstance(attempt_id, int) or attempt_id <= 0:
            raise SameDayCaptureStoreError("SAME_DAY_CAPTURE_STORE_ATTEMPT_ID_INVALID")
        final = str(outcome or "").strip().upper()
        if final not in _ATTEMPT_FINAL_OUTCOMES:
            raise SameDayCaptureStoreError("SAME_DAY_CAPTURE_STORE_ATTEMPT_OUTCOME_INVALID")
        finished = _nonnegative_time(
            time.time() if completed_at is None else completed_at,
            "SAME_DAY_CAPTURE_STORE_ATTEMPT_TIME_INVALID",
        )
        error = None if error_code is None else str(error_code).strip()
        digest = None if capture_sha256 is None else str(capture_sha256).strip().lower()
        if final == "FAILED":
            if not error:
                raise SameDayCaptureStoreError("SAME_DAY_CAPTURE_STORE_ATTEMPT_ERROR_REQUIRED")
            if digest is not None:
                raise SameDayCaptureStoreError("SAME_DAY_CAPTURE_STORE_FAILED_ATTEMPT_HAS_CAPTURE")
        else:
            if error:
                raise SameDayCaptureStoreError("SAME_DAY_CAPTURE_STORE_SUCCESS_ATTEMPT_HAS_ERROR")
            if digest is None or len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
                raise SameDayCaptureStoreError("SAME_DAY_CAPTURE_STORE_ATTEMPT_CAPTURE_DIGEST_INVALID")
        with self._conn() as db:
            row = db.execute(
                "SELECT attempted_at,outcome FROM weather_same_day_capture_attempts WHERE id=?",
                (attempt_id,),
            ).fetchone()
            if row is None or row["outcome"] != "STARTED":
                raise SameDayCaptureStoreError("SAME_DAY_CAPTURE_STORE_ATTEMPT_STATE_INVALID")
            if finished < float(row["attempted_at"]):
                raise SameDayCaptureStoreError("SAME_DAY_CAPTURE_STORE_ATTEMPT_TIME_INVALID")
            cur = db.execute(
                """
                UPDATE weather_same_day_capture_attempts
                   SET completed_at=?,outcome=?,error_code=?,capture_sha256=?
                 WHERE id=? AND outcome='STARTED'
                """,
                (finished, final, error, digest, attempt_id),
            )
            if cur.rowcount != 1:
                raise SameDayCaptureStoreError("SAME_DAY_CAPTURE_STORE_ATTEMPT_STATE_INVALID")

    def latest_attempt_at_for_event(self, event_id: str) -> float | None:
        identity = str(event_id or "").strip()
        if not identity:
            raise SameDayCaptureStoreError("SAME_DAY_CAPTURE_STORE_EVENT_ID_INVALID")
        with self._conn() as db:
            row = db.execute(
                "SELECT MAX(attempted_at) AS latest FROM weather_same_day_capture_attempts WHERE event_id=?",
                (identity,),
            ).fetchone()
        if row is None or row["latest"] is None:
            return None
        return float(row["latest"])

    def reconcile_started_attempts(self, *, completed_at: float | None = None) -> int:
        finished = _nonnegative_time(
            time.time() if completed_at is None else completed_at,
            "SAME_DAY_CAPTURE_STORE_ATTEMPT_TIME_INVALID",
        )
        with self._conn() as db:
            recovered = db.execute(
                """
                UPDATE weather_same_day_capture_attempts AS a
                   SET completed_at=(
                           SELECT MAX(a.attempted_at,c.as_of)
                             FROM weather_same_day_captures AS c
                            WHERE c.event_id=a.event_id AND c.as_of>=a.attempted_at
                            ORDER BY c.as_of ASC LIMIT 1
                       ),
                       outcome='SAVED',
                       capture_sha256=(
                           SELECT c.capture_sha256
                             FROM weather_same_day_captures AS c
                            WHERE c.event_id=a.event_id AND c.as_of>=a.attempted_at
                            ORDER BY c.as_of ASC LIMIT 1
                       )
                 WHERE a.outcome='STARTED'
                   AND EXISTS(
                       SELECT 1 FROM weather_same_day_captures AS c
                        WHERE c.event_id=a.event_id AND c.as_of>=a.attempted_at
                   )
                """
            )
            interrupted = db.execute(
                """
                UPDATE weather_same_day_capture_attempts
                   SET completed_at=CASE WHEN attempted_at>? THEN attempted_at ELSE ? END,
                       outcome='FAILED',
                       error_code='PROCESS_INTERRUPTED_BEFORE_ATTEMPT_FINALIZATION'
                 WHERE outcome='STARTED'
                """,
                (finished, finished),
            )
        return int(recovered.rowcount) + int(interrupted.rowcount)

    def attempt_summary(self) -> dict:
        with self._conn() as db:
            row = db.execute(
                """
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN outcome='STARTED' THEN 1 ELSE 0 END) AS started,
                       SUM(CASE WHEN outcome='SAVED' THEN 1 ELSE 0 END) AS saved,
                       SUM(CASE WHEN outcome='DUPLICATE' THEN 1 ELSE 0 END) AS duplicates,
                       SUM(CASE WHEN outcome='FAILED' THEN 1 ELSE 0 END) AS failed,
                       MIN(attempted_at) AS oldest_attempt,
                       MAX(attempted_at) AS newest_attempt
                  FROM weather_same_day_capture_attempts
                """
            ).fetchone()
        total = int(row["total"] or 0)
        return {
            "version": SAME_DAY_ATTEMPT_VERSION,
            "total": total,
            "started": int(row["started"] or 0),
            "saved": int(row["saved"] or 0),
            "duplicates": int(row["duplicates"] or 0),
            "failed": int(row["failed"] or 0),
            "oldest_attempt": row["oldest_attempt"],
            "newest_attempt": row["newest_attempt"],
            "max_attempt_rows": self.max_attempt_rows,
            "capacity_exhausted": (
                self.max_attempt_rows is not None and total >= self.max_attempt_rows
            ),
            "included_in_validated_pnl": False,
            "same_day_delivery_enabled": False,
            "financial_authority": False,
        }

'''
rep(
    "polymarket_scanner/weather_only_same_day_capture_store.py",
    "    def latest_as_of_for_event(self, event_id: str) -> float | None:\n",
    attempt_methods + "    def latest_as_of_for_event(self, event_id: str) -> float | None:\n",
)
rep(
    "polymarket_scanner/weather_only_same_day_capture_store.py",
    '        return {\n            "version": SAME_DAY_CAPTURE_STORE_VERSION,\n            "total": int(row["total"] or 0),',
    '        capture_total = int(row["total"] or 0)\n'
    '        capture_json_bytes = int(row["capture_json_bytes"] or 0)\n'
    '        attempts = self.attempt_summary()\n'
    '        return {\n'
    '            "version": SAME_DAY_CAPTURE_STORE_VERSION,\n'
    '            "total": capture_total,',
)
rep(
    "polymarket_scanner/weather_only_same_day_capture_store.py",
    '            "capture_json_bytes": int(row["capture_json_bytes"] or 0),\n'
    '            "database_file_bytes": int(database_bytes),',
    '            "capture_json_bytes": capture_json_bytes,\n'
    '            "max_capture_rows": self.max_capture_rows,\n'
    '            "max_capture_json_bytes": self.max_capture_json_bytes,\n'
    '            "capture_capacity_exhausted": (\n'
    '                (self.max_capture_rows is not None and capture_total >= self.max_capture_rows)\n'
    '                or (\n'
    '                    self.max_capture_json_bytes is not None\n'
    '                    and capture_json_bytes >= self.max_capture_json_bytes\n'
    '                )\n'
    '            ),\n'
    '            "attempts": attempts,\n'
    '            "database_file_bytes": int(database_bytes),',
)

# 4. Corrective collector uses durable attempts for cadence and source-failure provenance.
rep(
    "polymarket_scanner/weather_only_live_paper_corrective.py",
    "            self.same_day_captures = SameDayCaptureStore(self.db_path)\n"
    "            self.settlement = CorrectiveSettlementEngine",
    "            self.same_day_captures = SameDayCaptureStore(self.db_path)\n"
    "            self._same_day_attempt_recovery = self.same_day_captures.reconcile_started_attempts()\n"
    "            self.settlement = CorrectiveSettlementEngine",
)
rep(
    "polymarket_scanner/weather_only_live_paper_corrective.py",
    "            # In-memory throttle protects repeated source failures inside one process.\n"
    "            # Successful captures are additionally throttled from durable SQLite state.\n"
    "            self._same_day_last_attempt: dict[str, float] = {}\n",
    "",
)
rep(
    "polymarket_scanner/weather_only_live_paper_corrective.py",
    "        now = time.time()\n"
    "        bundle_cache: dict[tuple, tuple] = {}\n\n"
    "        for event_id, _event, compiled, semantics, metadata in eligible:\n"
    "            try:\n"
    "                persisted_last = await asyncio.to_thread(\n"
    "                    self.same_day_captures.latest_as_of_for_event, event_id\n"
    "                )",
    "        bundle_cache: dict[tuple, tuple | Exception] = {}\n\n"
    "        for event_id, _event, compiled, semantics, metadata in eligible:\n"
    "            now = time.time()\n"
    "            try:\n"
    "                persisted_capture = await asyncio.to_thread(\n"
    "                    self.same_day_captures.latest_as_of_for_event, event_id\n"
    "                )\n"
    "                persisted_attempt = await asyncio.to_thread(\n"
    "                    self.same_day_captures.latest_attempt_at_for_event, event_id\n"
    "                )",
)
rep(
    "polymarket_scanner/weather_only_live_paper_corrective.py",
    "            memory_last = float(self._same_day_last_attempt.get(event_id, 0.0))\n"
    "            last = max(memory_last, float(persisted_last or 0.0))",
    "            last = max(float(persisted_capture or 0.0), float(persisted_attempt or 0.0))",
)
rep(
    "polymarket_scanner/weather_only_live_paper_corrective.py",
    "            self._same_day_last_attempt[event_id] = now\n"
    "            attempted += 1\n"
    "            key = (",
    "            try:\n"
    "                attempt_id = await asyncio.to_thread(\n"
    "                    self.same_day_captures.start_attempt,\n"
    "                    event_id=event_id,\n"
    "                    station=str(compiled.station_hint).upper(),\n"
    "                    target_date=compiled.target_date.isoformat(),\n"
    "                    family=str(compiled.family),\n"
    "                    unit=str(compiled.unit),\n"
    "                    attempted_at=now,\n"
    "                )\n"
    "            except Exception as exc:\n"
    "                code = getattr(exc, \"code\", type(exc).__name__)\n"
    "                errors.append(f\"SAME_DAY_ATTEMPT_AUDIT_START:{event_id}:{code}\")\n"
    "                continue\n\n"
    "            attempted += 1\n"
    "            key = (",
)
rep(
    "polymarket_scanner/weather_only_live_paper_corrective.py",
    "                if key not in bundle_cache:\n"
    "                    bundle_cache[key] = await self._fetch_same_day_source_bundle(compiled, metadata)\n"
    "                wrh_result, nws_snapshot, hourly_gefs = bundle_cache[key]",
    "                if key not in bundle_cache:\n"
    "                    try:\n"
    "                        bundle_cache[key] = await self._fetch_same_day_source_bundle(\n"
    "                            compiled, metadata\n"
    "                        )\n"
    "                    except Exception as source_exc:\n"
    "                        bundle_cache[key] = source_exc\n"
    "                bundle_value = bundle_cache[key]\n"
    "                if isinstance(bundle_value, Exception):\n"
    "                    raise bundle_value\n"
    "                wrh_result, nws_snapshot, hourly_gefs = bundle_value",
)
rep(
    "polymarket_scanner/weather_only_live_paper_corrective.py",
    "            except Exception as exc:\n"
    "                code = getattr(exc, \"code\", type(exc).__name__)\n"
    "                errors.append(f\"SAME_DAY_CAPTURE:{event_id}:{code}\")\n"
    "                continue\n\n"
    "            if row_id is None:\n"
    "                duplicates += 1\n"
    "            else:\n"
    "                saved += 1",
    "            except Exception as exc:\n"
    "                code = getattr(exc, \"code\", type(exc).__name__)\n"
    "                errors.append(f\"SAME_DAY_CAPTURE:{event_id}:{code}\")\n"
    "                try:\n"
    "                    await asyncio.to_thread(\n"
    "                        self.same_day_captures.finish_attempt,\n"
    "                        attempt_id,\n"
    "                        outcome=\"FAILED\",\n"
    "                        completed_at=time.time(),\n"
    "                        error_code=str(code),\n"
    "                    )\n"
    "                except Exception as audit_exc:\n"
    "                    audit_code = getattr(audit_exc, \"code\", type(audit_exc).__name__)\n"
    "                    errors.append(\n"
    "                        f\"SAME_DAY_ATTEMPT_AUDIT_FINISH:{event_id}:{audit_code}\"\n"
    "                    )\n"
    "                continue\n\n"
    "            if row_id is None:\n"
    "                duplicates += 1\n"
    "                audit_outcome = \"DUPLICATE\"\n"
    "            else:\n"
    "                saved += 1\n"
    "                audit_outcome = \"SAVED\"\n"
    "            try:\n"
    "                await asyncio.to_thread(\n"
    "                    self.same_day_captures.finish_attempt,\n"
    "                    attempt_id,\n"
    "                    outcome=audit_outcome,\n"
    "                    completed_at=time.time(),\n"
    "                    capture_sha256=record.capture_sha256,\n"
    "                )\n"
    "            except Exception as audit_exc:\n"
    "                audit_code = getattr(audit_exc, \"code\", type(audit_exc).__name__)\n"
    "                errors.append(f\"SAME_DAY_ATTEMPT_AUDIT_FINISH:{event_id}:{audit_code}\")",
)
rep(
    "polymarket_scanner/weather_only_live_paper_corrective.py",
    '            "capture_cadence_persisted_in_sqlite": True,\n'
    '            "max_events_per_cycle": DEFAULT_MAX_SAME_DAY_CAPTURE_EVENTS,',
    '            "capture_cadence_persisted_in_sqlite": True,\n'
    '            "attempt_audit_persisted_in_sqlite": True,\n'
    '            "attempt_recovery_at_startup": int(self._same_day_attempt_recovery),\n'
    '            "max_events_per_cycle": DEFAULT_MAX_SAME_DAY_CAPTURE_EVENTS,',
)

# 5. Three-layer wrapper hard-bounds research persistence and rejects partial >12 sampling.
rep(
    "polymarket_scanner/weather_only_live_paper_three_layer_validation.py",
    "from .weather_only_same_day_contract import build_same_day_contract_semantics\n",
    "from .weather_only_same_day_contract import build_same_day_contract_semantics\n"
    "from .weather_only_same_day_capture_store import SameDayCaptureStore\n",
)
rep(
    "polymarket_scanner/weather_only_live_paper_three_layer_validation.py",
    "THREE_LAYER_31D_CAPTURE_ROW_BOUND = THREE_LAYER_SELECTION_UNIVERSE_CAP * 24 * 31\n",
    "THREE_LAYER_31D_CAPTURE_ROW_BOUND = THREE_LAYER_SELECTION_UNIVERSE_CAP * 24 * 31\n"
    "THREE_LAYER_CAPTURE_JSON_BYTES_CAP = 512 * 1024 * 1024\n"
    "THREE_LAYER_ATTEMPT_ROW_CAP = THREE_LAYER_31D_CAPTURE_ROW_BOUND\n",
)
rep(
    "polymarket_scanner/weather_only_live_paper_three_layer_validation.py",
    "        super().__init__(**kwargs)\n\n"
    "        # These constructors perform no network I/O.",
    "        super().__init__(**kwargs)\n\n"
    "        # Re-open the same SQLite-backed capture facade with explicit hard limits.\n"
    "        # Existing rows are preserved; exhaustion fails closed instead of pruning.\n"
    "        self.same_day_captures = SameDayCaptureStore(\n"
    "            self.db_path,\n"
    "            max_capture_rows=THREE_LAYER_31D_CAPTURE_ROW_BOUND,\n"
    "            max_capture_json_bytes=THREE_LAYER_CAPTURE_JSON_BYTES_CAP,\n"
    "            max_attempt_rows=THREE_LAYER_ATTEMPT_ROW_CAP,\n"
    "        )\n\n"
    "        # These constructors perform no network I/O.",
)
rep(
    "polymarket_scanner/weather_only_live_paper_three_layer_validation.py",
    "        universe = eligible[:THREE_LAYER_SELECTION_UNIVERSE_CAP]\n"
    "        self._three_layer_last_universe_truncated = len(eligible) > len(universe)\n"
    "        cursor = self.positions.get_state(THREE_LAYER_CURSOR_KEY, \"\")",
    "        if len(eligible) > THREE_LAYER_SELECTION_UNIVERSE_CAP:\n"
    "            self._three_layer_last_universe_truncated = True\n"
    "            self._three_layer_last_selected_ids = ()\n"
    "            errors.append(\n"
    "                f\"SAME_DAY_SELECTION_UNIVERSE_CAP_EXCEEDED:{len(eligible)}>\"\n"
    "                f\"{THREE_LAYER_SELECTION_UNIVERSE_CAP}\"\n"
    "            )\n"
    "            return [], errors\n"
    "        universe = eligible\n"
    "        self._three_layer_last_universe_truncated = False\n"
    "        cursor = self.positions.get_state(THREE_LAYER_CURSOR_KEY, \"\")",
)
rep(
    "polymarket_scanner/weather_only_live_paper_three_layer_validation.py",
    "    async def close(self) -> None:\n"
    "        self._same_day_wrh.close()\n"
    "        await asyncio.gather(",
    "    async def close(self) -> None:\n"
    "        try:\n"
    "            self._same_day_wrh.close()\n"
    "        except Exception:\n"
    "            pass\n"
    "        await asyncio.gather(",
)
rep(
    "polymarket_scanner/weather_only_live_paper_three_layer_validation.py",
    '                "theoretical_31_day_row_bound_at_full_daily_eligibility": (\n'
    "                    THREE_LAYER_31D_CAPTURE_ROW_BOUND\n"
    "                ),",
    '                "theoretical_31_day_row_bound_at_full_daily_eligibility": (\n'
    "                    THREE_LAYER_31D_CAPTURE_ROW_BOUND\n"
    "                ),\n"
    '                "capture_json_bytes_cap": THREE_LAYER_CAPTURE_JSON_BYTES_CAP,\n'
    '                "attempt_row_cap": THREE_LAYER_ATTEMPT_ROW_CAP,',
)

# 6. Deployment acceptance verifies hard capacity policy.
rep(
    "deploy/verify-three-layer-validation-status.py",
    "    THREE_LAYER_31D_CAPTURE_ROW_BOUND,\n    THREE_LAYER_SELECTION_POLICY,",
    "    THREE_LAYER_31D_CAPTURE_ROW_BOUND,\n"
    "    THREE_LAYER_CAPTURE_JSON_BYTES_CAP,\n"
    "    THREE_LAYER_ATTEMPT_ROW_CAP,\n"
    "    THREE_LAYER_SELECTION_POLICY,",
)
rep(
    "deploy/verify-three-layer-validation-status.py",
    '    if lane.get("theoretical_31_day_row_bound_at_full_daily_eligibility") != THREE_LAYER_31D_CAPTURE_ROW_BOUND:\n'
    '        raise ThreeLayerStatusError("THREE_LAYER_STORAGE_BOUND_MISMATCH")\n'
    '    if list(lane.get("errors") or []):',
    '    if lane.get("theoretical_31_day_row_bound_at_full_daily_eligibility") != THREE_LAYER_31D_CAPTURE_ROW_BOUND:\n'
    '        raise ThreeLayerStatusError("THREE_LAYER_STORAGE_BOUND_MISMATCH")\n'
    '    if lane.get("capture_json_bytes_cap") != THREE_LAYER_CAPTURE_JSON_BYTES_CAP:\n'
    '        raise ThreeLayerStatusError("THREE_LAYER_CAPTURE_BYTE_CAP_MISMATCH")\n'
    '    if lane.get("attempt_row_cap") != THREE_LAYER_ATTEMPT_ROW_CAP:\n'
    '        raise ThreeLayerStatusError("THREE_LAYER_ATTEMPT_ROW_CAP_MISMATCH")\n'
    '    store = lane.get("store")\n'
    '    if not isinstance(store, dict):\n'
    '        raise ThreeLayerStatusError("THREE_LAYER_STORE_STATUS_MISSING")\n'
    '    if store.get("max_capture_rows") != THREE_LAYER_31D_CAPTURE_ROW_BOUND:\n'
    '        raise ThreeLayerStatusError("THREE_LAYER_STORE_ROW_CAP_MISMATCH")\n'
    '    if store.get("max_capture_json_bytes") != THREE_LAYER_CAPTURE_JSON_BYTES_CAP:\n'
    '        raise ThreeLayerStatusError("THREE_LAYER_STORE_BYTE_CAP_MISMATCH")\n'
    '    attempts = store.get("attempts")\n'
    '    if not isinstance(attempts, dict) or attempts.get("max_attempt_rows") != THREE_LAYER_ATTEMPT_ROW_CAP:\n'
    '        raise ThreeLayerStatusError("THREE_LAYER_STORE_ATTEMPT_CAP_MISMATCH")\n'
    '    if store.get("capture_capacity_exhausted") is True or attempts.get("capacity_exhausted") is True:\n'
    '        raise ThreeLayerStatusError("THREE_LAYER_STORE_CAPACITY_EXHAUSTED")\n'
    '    if list(lane.get("errors") or []):',
)

# 7. Regression tests.
p = Path("tests/test_weather_only_three_layer.py")
p.write_text(
    p.read_text(encoding="utf-8")
    + '''\n\n
def test_combined_layer_provenance_uses_latest_component_issue_time(monkeypatch):
    import polymarket_scanner.weather_only_three_layer as module

    as_of = _ts(4, 20)
    observed = _observed(as_of)
    coverage = _coverage(as_of)
    near = _near_term(coverage, as_of)
    path = _ensemble_path(coverage, as_of)
    captured = {}
    real = module.build_remaining_hours_ensemble

    def recording_builder(**kwargs):
        captured["issued_at"] = kwargs["issued_at"]
        return real(**kwargs)

    monkeypatch.setattr(module, "build_remaining_hours_ensemble", recording_builder)
    build_three_layer_research_decision(
        _compiled(), observed, coverage, near, path,
        EnsembleMappingPolicy(policy_id="fixture-map", include_control=True),
        as_of=as_of,
    )
    assert captured["issued_at"] == max(float(near.issued_at), float(path.issued_at))
''',
    encoding="utf-8",
)

p = Path("tests/test_weather_only_nws_near_term.py")
p.write_text(
    p.read_text(encoding="utf-8")
    + '''\n\n
def test_stale_grid_update_is_not_accepted_as_current_layer2_evidence():
    stale = _grid(update="2026-09-13T03:00:00+00:00")
    with pytest.raises(NWSNearTermError, match="NWS_NEAR_TERM_UPDATE_STALE"):
        _parse(grid=stale)
''',
    encoding="utf-8",
)

p = Path("tests/test_weather_only_same_day_capture_cadence.py")
p.write_text(
    p.read_text(encoding="utf-8")
    + '''\n\n
def test_failed_attempt_is_durable_and_survives_restart(tmp_path):
    db = tmp_path / "weather-paper.sqlite"
    first = SameDayCaptureStore(db)
    attempt = first.start_attempt(
        event_id="event-x", station="KLGA", target_date="2026-09-14",
        family="DAILY_HIGH", unit="F", attempted_at=1000.0,
    )
    first.finish_attempt(
        attempt, outcome="FAILED", completed_at=1001.0, error_code="SOURCE_TIMEOUT"
    )
    restarted = SameDayCaptureStore(db)
    assert restarted.latest_attempt_at_for_event("event-x") == pytest.approx(1000.0)
    summary = restarted.attempt_summary()
    assert summary["total"] == 1
    assert summary["failed"] == 1
    assert summary["started"] == 0
    assert summary["included_in_validated_pnl"] is False


def test_interrupted_attempt_is_reconciled_as_failed_not_silently_lost(tmp_path):
    db = tmp_path / "weather-paper.sqlite"
    first = SameDayCaptureStore(db)
    first.start_attempt(
        event_id="event-x", station="KLGA", target_date="2026-09-14",
        family="DAILY_HIGH", unit="F", attempted_at=1000.0,
    )
    restarted = SameDayCaptureStore(db)
    assert restarted.reconcile_started_attempts(completed_at=1002.0) == 1
    summary = restarted.attempt_summary()
    assert summary["started"] == 0
    assert summary["failed"] == 1


def test_attempt_and_capture_capacity_limits_fail_closed(tmp_path):
    db = tmp_path / "weather-paper.sqlite"
    store = SameDayCaptureStore(
        db, max_capture_rows=1, max_capture_json_bytes=1, max_attempt_rows=1
    )
    attempt = store.start_attempt(
        event_id="event-x", station="KLGA", target_date="2026-09-14",
        family="DAILY_HIGH", unit="F", attempted_at=1000.0,
    )
    store.finish_attempt(
        attempt, outcome="FAILED", completed_at=1001.0, error_code="TEST"
    )
    with pytest.raises(SameDayCaptureStoreError, match="ATTEMPT_ROW_CAP_EXHAUSTED"):
        store.start_attempt(
            event_id="event-y", station="KLGA", target_date="2026-09-14",
            family="DAILY_HIGH", unit="F", attempted_at=1002.0,
        )
    with pytest.raises(SameDayCaptureStoreError, match="CAPTURE_BYTE_CAP_EXHAUSTED"):
        store.save(_capture())
''',
    encoding="utf-8",
)

p = Path("tests/test_weather_only_gefs_hourly.py")
p.write_text(
    p.read_text(encoding="utf-8")
    + '''\n\n
def _dst_payload(times, timezone_name):
    hourly = {"time": list(times)}
    units = {"time": "iso8601"}
    for member, key in enumerate(_keys()):
        hourly[key] = [60.0 + member * 0.1 + index * 0.01 for index in range(len(times))]
        units[key] = "°F"
    return {
        "latitude": 40.78,
        "longitude": -73.87,
        "timezone": timezone_name,
        "hourly": hourly,
        "hourly_units": units,
    }


def test_spring_forward_23_hour_local_day_is_accepted_when_grid_is_complete_in_real_time():
    target = date(2026, 3, 8)
    times = ["2026-03-08T00:00", "2026-03-08T01:00"] + [
        f"2026-03-08T{hour:02d}:00" for hour in range(3, 24)
    ]
    result = parse_open_meteo_gefs_hourly_target_day(
        _dst_payload(times, "America/New_York"),
        station="KLGA", target_date=target, unit="F", timezone="America/New_York",
        requested_latitude=40.7769, requested_longitude=-73.8740, received_at=RECEIVED,
    )
    assert len(result.valid_times) == 23
    assert all(
        after - before == GEFS_HOURLY_STEP_SECONDS
        for before, after in zip(result.valid_times, result.valid_times[1:])
    )


def test_fall_back_25_hour_day_accepts_explicit_offsets_but_rejects_ambiguous_naive_duplicate():
    target = date(2026, 11, 1)
    explicit = ["2026-11-01T00:00-04:00", "2026-11-01T01:00-04:00", "2026-11-01T01:00-05:00"] + [
        f"2026-11-01T{hour:02d}:00-05:00" for hour in range(2, 24)
    ]
    result = parse_open_meteo_gefs_hourly_target_day(
        _dst_payload(explicit, "America/New_York"),
        station="KLGA", target_date=target, unit="F", timezone="America/New_York",
        requested_latitude=40.7769, requested_longitude=-73.8740, received_at=RECEIVED,
    )
    assert len(result.valid_times) == 25

    ambiguous = ["2026-11-01T00:00", "2026-11-01T01:00", "2026-11-01T01:00"] + [
        f"2026-11-01T{hour:02d}:00" for hour in range(2, 24)
    ]
    with pytest.raises(GEFSHourlyError, match="GEFS_HOURLY_TIME_DUPLICATE_OR_DST_AMBIGUOUS"):
        parse_open_meteo_gefs_hourly_target_day(
            _dst_payload(ambiguous, "America/New_York"),
            station="KLGA", target_date=target, unit="F", timezone="America/New_York",
            requested_latitude=40.7769, requested_longitude=-73.8740, received_at=RECEIVED,
        )
''',
    encoding="utf-8",
)

p = Path("tests/test_weather_only_three_layer_validation_guarded.py")
p.write_text(
    p.read_text(encoding="utf-8")
    + '''\n\n
def test_selection_universe_over_cap_fails_closed_in_source_not_partial_first_twelve():
    from pathlib import Path
    import polymarket_scanner.weather_only_live_paper_three_layer_validation as runtime_module

    runtime = Path(runtime_module.__file__).read_text(encoding="utf-8")
    assert "SAME_DAY_SELECTION_UNIVERSE_CAP_EXCEEDED" in runtime
    assert "universe = eligible[:THREE_LAYER_SELECTION_UNIVERSE_CAP]" not in runtime
''',
    encoding="utf-8",
)

print("meticulous review patch applied")
