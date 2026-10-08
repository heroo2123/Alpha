"""Offline author tests for the pinned historical six-row profile.

Set V11_HISTORICAL_SIX_SNAPSHOT to the private sealed MASTER backup for exact
replay. The repository contains no provider body fixture or hash override.
"""
import copy
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import socket
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from polymarket_scanner.v11.daily_seed_historical_six import (
    CATALOGUE, CATALOGUE_SHA256, PROFILE_ID, SeedPlanError, _validate_six,
    plan_historical_six_seed, verify_existing_historical_six_daily,
)
from polymarket_scanner.v11.daily_seed_plan import _json_unique, _memory_db
from polymarket_scanner.v11.evidence import EvidenceStore, canonical, digest


SNAPSHOT = os.environ.get("V11_HISTORICAL_SIX_SNAPSHOT")


@unittest.skipUnless(SNAPSHOT, "private sealed source snapshot not supplied")
class HistoricalSixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.snapshot = Path(SNAPSHOT)
        cls.source_sha = hashlib.sha256(cls.snapshot.read_bytes()).hexdigest()
        with _memory_db(cls.snapshot.read_bytes()) as db:
            cls.rows = [db.execute("SELECT * FROM v11_records WHERE seq=?", (i,)).fetchone()
                        for i in range(1, 7)]
        cls.bodies = [json.loads(row["body"]) for row in cls.rows]

    def plan(self, **overrides):
        args = dict(target_date="2026-10-10", target_event_id="future:distinct",
                    target_generation="offline-test-generation")
        args.update(overrides)
        return plan_historical_six_seed(self.snapshot, self.source_sha, **args)

    def test_exact_positive_and_generic_later_append(self):
        with patch.object(socket, "socket", side_effect=AssertionError("socket")), \
             patch.object(socket, "getaddrinfo", side_effect=AssertionError("dns")):
            plan = self.plan()
        self.assertEqual(len(plan.rows), 6)
        self.assertEqual([r[0] for r in plan.rows], list(range(1, 7)))
        self.assertEqual([(r[0], r[1], r[2], r[3], r[7]) for r in plan.rows], list(CATALOGUE))
        self.assertEqual([r[6] for r in plan.rows], [r["body"] for r in self.rows])
        self.assertEqual([r[4:6] for r in plan.rows],
                         [(r["recorded_at"], r["available_at"]) for r in self.rows])
        self.assertEqual(self.bodies[4]["details"]["source_receipt_seq"], 2)
        self.assertEqual(plan.manifest["profile_id"], PROFILE_ID)
        self.assertEqual(plan.manifest["profile_catalogue_sha256"], CATALOGUE_SHA256)
        self.assertEqual(plan.manifest["source_snapshot_sha256"], self.source_sha)
        self.assertEqual(plan.manifest["financial_authority"], False)
        self.assertEqual(plan.manifest["runtime_admission"], False)
        self.assertEqual(plan.manifest["barrier_reconciliation"], "UNPERFORMED")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "daily.sqlite"
            sealed = Path(tmp) / "sealed.sqlite"
            EvidenceStore(path, "CHALLENGER:katl-shadow")
            with sqlite3.connect(path) as db:
                db.executemany("INSERT INTO v11_records VALUES(?,?,?,?,?,?,?,?)", plan.rows)
                db.commit()
                with sqlite3.connect(sealed) as dst:
                    db.backup(dst)
            self.assertEqual(verify_existing_historical_six_daily(
                sealed, plan, plan.manifest_sha256), 6)
            tampered_manifest = dict(plan.manifest)
            tampered_manifest["rule_receipt_seq"] = 3
            with self.assertRaisesRegex(SeedPlanError, "PLAN_PROFILE"):
                verify_existing_historical_six_daily(
                    sealed, replace(plan, manifest=tampered_manifest), digest(tampered_manifest))
            with sqlite3.connect(path) as db:
                body = dict(namespace="CHALLENGER:katl-shadow", financial_authority=False,
                            record_id="future:append", kind="IDENTITY", event_id="future:distinct",
                            recorded_at=1792000000.0, available_at=1792000000.0,
                            details={"test": True}, evidence=[])
                db.execute("INSERT INTO v11_records VALUES(?,?,?,?,?,?,?,?)",
                           (7, body["record_id"], body["kind"], body["event_id"],
                            body["recorded_at"], body["available_at"], canonical(body), digest(body)))
                db.commit()
                sealed.unlink()
                with sqlite3.connect(sealed) as dst:
                    db.backup(dst)
            self.assertEqual(verify_existing_historical_six_daily(
                sealed, plan, plan.manifest_sha256), 7)

    def test_target_and_source_identity_refusals(self):
        for args in (dict(target_date="2026-10-04"), dict(target_date="2026-10-03"),
                     dict(target_event_id="1118070"), dict(target_generation=""),
                     dict(target_date="invalid")):
            with self.subTest(args=args), self.assertRaises(SeedPlanError):
                self.plan(**args)
        with self.assertRaisesRegex(SeedPlanError, "SOURCE_IDENTITY"):
            plan_historical_six_seed(self.snapshot, "0" * 64, target_date="2026-10-10",
                                     target_event_id="future:distinct", target_generation="g")

    def test_short_read_file_bound_and_sparse_daily_refuse(self):
        with patch("polymarket_scanner.v11.daily_seed_plan.os.read", return_value=b""):
            with self.assertRaisesRegex(SeedPlanError, "SNAPSHOT_SHORT_READ"):
                self.plan()
        with patch("polymarket_scanner.v11.daily_seed_plan.MAX_SNAPSHOT_BYTES", 1):
            with self.assertRaisesRegex(SeedPlanError, "SNAPSHOT_FILE_BOUNDS_OR_ALIAS"):
                self.plan()
        plan = self.plan()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "partial.sqlite"
            sealed = Path(tmp) / "partial-sealed.sqlite"
            EvidenceStore(path, "CHALLENGER:katl-shadow")
            with sqlite3.connect(path) as db:
                db.executemany("INSERT INTO v11_records VALUES(?,?,?,?,?,?,?,?)",
                               plan.rows[:5])
                db.commit()
                with sqlite3.connect(sealed) as dst:
                    db.backup(dst)
            with self.assertRaisesRegex(SeedPlanError, "EXISTING_DAILY_SPARSE_OR_PARTIAL"):
                verify_existing_historical_six_daily(sealed, plan, plan.manifest_sha256)

    def test_every_fixed_edge_and_relationship_refuses(self):
        changes = [
            (1, lambda b: b["payload"].update(source_capture_id="other")),
            (1, lambda b: b["payload"].update(source_capture_sha256="0" * 64)),
            (1, lambda b: b["payload"].update(event={})),
            (3, lambda b: b["details"]["metadata"].update(source_payload_sha256="0" * 64)),
            (3, lambda b: b["details"].update(metadata_fingerprint="0" * 64)),
            (4, lambda b: b["details"].update(source_receipt_seq=3)),
            (4, lambda b: b["details"].update(source_received_at=1.0)),
            (4, lambda b: b["details"].update(source_event_sha256="0" * 64)),
            (4, lambda b: b["details"].update(fingerprint="0" * 64)),
            (4, lambda b: b["details"]["preimage"].update(metadata_fingerprint="0" * 64)),
        ]
        for child in (3, 4, 5):
            for edge in range(len(self.bodies[child]["evidence"])):
                changes.append((child, lambda b, e=edge: b["evidence"][e].update(id="wrong")))
                changes.append((child, lambda b, e=edge: b["evidence"][e].update(sha256="0" * 64)))
        for index, alter in changes:
            with self.subTest(index=index, alter=len(str(alter))):
                bodies = copy.deepcopy(self.bodies)
                alter(bodies[index])
                with self.assertRaises(SeedPlanError):
                    _validate_six(self.rows, bodies)

    def test_unreviewed_shapes_types_and_provider_change_refuse(self):
        changes = [
            (0, lambda b: b.update(kind="SOURCE_RESULT", capture_ids=["x"])),
            (0, lambda b: b.update(unknown_wrapper={})),
            (0, lambda b: b["payload"].update(capture_ids=["x"])),
            (0, lambda b: b["payload"].update(response=[])),
            (0, lambda b: b["payload"].update(http_status=True)),
            (2, lambda b: b["payload"].update(unknown_reference_id="x")),
            (2, lambda b: b["payload"].update(type="changed")),
            (3, lambda b: b["details"].update(unknown_wrapper={})),
            (4, lambda b: b["details"].update(source_receipt_seq=True)),
            (5, lambda b: b["details"].update(live_authority=True)),
        ]
        for index, alter in changes:
            with self.subTest(index=index):
                bodies = copy.deepcopy(self.bodies)
                alter(bodies[index])
                with self.assertRaises(SeedPlanError):
                    _validate_six(self.rows, bodies)
        with self.assertRaises(SeedPlanError):
            _json_unique('{"a":1,"a":2}')
        with self.assertRaises(SeedPlanError):
            _json_unique('{"a":NaN}')

    def test_selected_catalogue_cannot_be_redefined(self):
        plan = self.plan()
        altered = list(self.rows)
        for index in range(6):
            with self.subTest(index=index):
                body = copy.deepcopy(self.bodies[index])
                body["financial_authority"] = True
                with self.assertRaises(SeedPlanError):
                    _validate_six(altered, self.bodies[:index] + [body] + self.bodies[index+1:])
        self.assertEqual(plan.manifest["profile_catalogue_sha256"], CATALOGUE_SHA256)

    def test_rehashed_source_mutations_missing_and_repositioned_refuse(self):
        def altered(name, sql):
            with tempfile.TemporaryDirectory() as tmp:
                target = Path(tmp) / name
                with _memory_db(self.snapshot.read_bytes()) as src:
                    # Use a private standalone copy so the source inode remains untouched.
                    with sqlite3.connect(target) as dst:
                        src.backup(dst)
                with sqlite3.connect(target) as db:
                    db.execute("DROP TRIGGER v11_no_update")
                    db.execute("DROP TRIGGER v11_no_delete")
                    sql(db)
                    db.execute("CREATE TRIGGER v11_no_update BEFORE UPDATE ON v11_records "
                               "BEGIN SELECT RAISE(ABORT,'APPEND_ONLY'); END")
                    db.execute("CREATE TRIGGER v11_no_delete BEFORE DELETE ON v11_records "
                               "BEGIN SELECT RAISE(ABORT,'APPEND_ONLY'); END")
                for suffix in ("-wal", "-shm", "-journal"):
                    Path(str(target) + suffix).unlink(missing_ok=True)
                pin = hashlib.sha256(target.read_bytes()).hexdigest()
                with self.assertRaises(SeedPlanError):
                    plan_historical_six_seed(target, pin, target_date="2026-10-10",
                                             target_event_id="future:distinct",
                                             target_generation="offline-test-generation")

        for index in range(6):
            def change_body(db, i=index):
                row = db.execute("SELECT body FROM v11_records WHERE seq=?", (i + 1,)).fetchone()
                body = json.loads(row[0])
                body["financial_authority"] = True
                db.execute("UPDATE v11_records SET body=?,body_sha256=? WHERE seq=?",
                           (canonical(body), digest(body), i + 1))
            with self.subTest(body=index):
                altered(f"rehashed-{index}.sqlite", change_body)

        altered("missing.sqlite", lambda db: db.execute("DELETE FROM v11_records WHERE seq=6"))
        altered("shifted.sqlite", lambda db: db.execute(
            "UPDATE v11_records SET seq=10000 WHERE seq=2"))
        altered("kind.sqlite", lambda db: db.execute(
            "UPDATE v11_records SET kind='SOURCE_RESULT' WHERE seq=1"))
        altered("event.sqlite", lambda db: db.execute(
            "UPDATE v11_records SET event_id='other' WHERE seq=5"))


if __name__ == "__main__":
    unittest.main()
