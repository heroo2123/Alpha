"""Offline author tests for the pinned historical six-row profile.

Set V11_HISTORICAL_SIX_SNAPSHOT to the private sealed MASTER backup for exact
replay. The repository contains no provider body fixture or hash override.
"""
import copy
from contextlib import closing
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
    CATALOGUE, CATALOGUE_SHA256, EXPECTED_GRAPH_EDGES, EXPECTED_MAPPING,
    PROFILE_ID, SeedPlanError, _validate_six,
    plan_historical_six_seed, verify_existing_historical_six_daily,
)
from polymarket_scanner.v11.daily_seed_plan import (
    NAMESPACE, SeedPlan, _json_unique, _memory_db,
)
from polymarket_scanner.v11.evidence import EvidenceStore, canonical, digest


SNAPSHOT = os.environ.get("V11_HISTORICAL_SIX_SNAPSHOT")


def corrupt_hidden_duplicate(path, decoy_id, target_id):
    """Rewrite a table b-tree leaf's record_id text, leaving its UNIQUE index

    entry pointing at the old text: a second row visible only to a full
    table scan, invisible to an indexed lookup and to PRAGMA quick_check.
    """
    if len(decoy_id.encode()) != len(target_id.encode()):
        raise ValueError("decoy_id and target_id must be byte-equal length")
    data = bytearray(path.read_bytes())
    page_size = int.from_bytes(data[16:18], "big")
    page_size = 65536 if page_size == 1 else page_size
    needle = decoy_id.encode()
    patched = 0
    i = data.find(needle)
    while i != -1:
        page = i // page_size
        header = page * page_size + (100 if page == 0 else 0)
        if data[header] == 0x0D:  # table leaf page; index leaves are 0x0A
            data[i:i + len(needle)] = target_id.encode()
            patched += 1
        i = data.find(needle, i + 1)
    path.write_bytes(bytes(data))
    return patched


class HistoricalSixSourceIntegrityTests(unittest.TestCase):
    """Always-on: no private snapshot required.

    F2: a hidden conflicting copy of a catalogued record_id, absent from the
    UNIQUE autoindex but visible to a full table scan, must refuse even
    though PRAGMA quick_check alone reports 'ok'.
    """

    def test_hidden_conflicting_copy_outside_unique_index_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            master = root / "master.sqlite"
            EvidenceStore(master, NAMESPACE)
            target_id = CATALOGUE[4][1]  # decision-shadow:rule:1118070
            decoy_id = target_id[:-1] + ("9" if not target_id.endswith("9") else "8")

            def row(record_id, seq):
                body = dict(namespace=NAMESPACE, financial_authority=False,
                            record_id=record_id, kind="RULE_STATE", event_id="1118070",
                            recorded_at=float(seq), available_at=float(seq),
                            details={}, evidence=[])
                return (seq, record_id, body["kind"], body["event_id"],
                        body["recorded_at"], body["available_at"],
                        canonical(body), digest(body))

            with sqlite3.connect(master) as db:
                db.execute("INSERT INTO v11_records VALUES(?,?,?,?,?,?,?,?)", row(target_id, 5))
                db.execute("INSERT INTO v11_records VALUES(?,?,?,?,?,?,?,?)", row(decoy_id, 9000))
            sealed = root / "sealed.sqlite"
            with sqlite3.connect(master) as src, sqlite3.connect(sealed) as dst:
                src.backup(dst)
            self.assertGreaterEqual(corrupt_hidden_duplicate(sealed, decoy_id, target_id), 1)
            with closing(sqlite3.connect(sealed)) as db:
                self.assertEqual(db.execute("PRAGMA quick_check").fetchone()[0], "ok")
                self.assertNotEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            pin = hashlib.sha256(sealed.read_bytes()).hexdigest()
            with self.assertRaisesRegex(SeedPlanError, "HISTORICAL_SIX_SOURCE_INTEGRITY"):
                plan_historical_six_seed(sealed, pin, target_date="2026-10-10",
                                         target_event_id="future:distinct", target_generation="g")


class HistoricalSixManifestForgeryTests(unittest.TestCase):
    """Always-on: no private snapshot required.

    F1: verify_existing_historical_six_daily must refuse a forged-but-self-
    consistent manifest (the caller pins digest(forged) itself), matching the
    planner's own target-context bounds and using type-exact field equality.
    """

    @staticmethod
    def _plan(**manifest_overrides):
        manifest = dict(version="alpha_v11_daily_seed_plan_v2", profile_id=PROFILE_ID,
                        profile_catalogue_sha256=CATALOGUE_SHA256, namespace=NAMESPACE,
                        historical_role="HISTORICAL_EVIDENCE_ONLY",
                        barrier_reconciliation="UNPERFORMED", runtime_admission=False,
                        source_snapshot_sha256="0" * 64, target_date="2026-10-10",
                        target_event_id="future:distinct", target_generation="g",
                        mapping=copy.deepcopy(EXPECTED_MAPPING),
                        graph_edges=copy.deepcopy(EXPECTED_GRAPH_EDGES),
                        rule_receipt_seq=2, financial_authority=False)
        manifest.update(manifest_overrides)
        rows = tuple((pin[0], pin[1], pin[2], pin[3], float(pin[0]), float(pin[0]), "{}", pin[4])
                     for pin in CATALOGUE)
        return SeedPlan(rows, manifest, digest(manifest))

    def _assert_refused(self, plan, pattern):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "absent.sqlite"
            with self.assertRaisesRegex(SeedPlanError, pattern):
                verify_existing_historical_six_daily(missing, plan, plan.manifest_sha256)

    def test_legitimate_manifest_passes_profile_checks(self):
        # A genuinely matching manifest must clear the profile checks and
        # reach the sealed-image read, failing only because the path is absent.
        self._assert_refused(self._plan(), "SNAPSHOT_MISSING")

    def test_historical_target_day_rejected(self):
        self._assert_refused(self._plan(target_date="2026-10-04"), "HISTORICAL_SIX_TARGET_CONTEXT")

    def test_malformed_target_date_rejected(self):
        self._assert_refused(self._plan(target_date="not-a-date"), "HISTORICAL_SIX_TARGET_DATE")

    def test_empty_target_event_rejected(self):
        self._assert_refused(self._plan(target_event_id=""), "HISTORICAL_SIX_TARGET_CONTEXT")

    def test_oversized_target_generation_rejected(self):
        self._assert_refused(self._plan(target_generation="g" * 10_000),
                             "HISTORICAL_SIX_TARGET_CONTEXT")

    def test_rule_receipt_seq_float_type_confusion_rejected(self):
        self._assert_refused(self._plan(rule_receipt_seq=2.0), "HISTORICAL_SIX_PLAN_PROFILE")

    def test_mapping_source_seq_bool_type_confusion_rejected(self):
        mapping = copy.deepcopy(EXPECTED_MAPPING)
        mapping[0]["source_seq"] = True
        self._assert_refused(self._plan(mapping=mapping), "HISTORICAL_SIX_PLAN_PROFILE")

    def test_mapping_local_seq_float_type_confusion_rejected(self):
        mapping = copy.deepcopy(EXPECTED_MAPPING)
        mapping[0]["local_seq"] = 1.0
        self._assert_refused(self._plan(mapping=mapping), "HISTORICAL_SIX_PLAN_PROFILE")

    def test_graph_edges_float_type_confusion_rejected(self):
        edges = {key: [float(v) for v in value] for key, value in EXPECTED_GRAPH_EDGES.items()}
        self._assert_refused(self._plan(graph_edges=edges), "HISTORICAL_SIX_PLAN_PROFILE")

    def test_verifier_trust_anchor_is_immune_to_manifest_mutation(self):
        """Verifier-side guard: acceptance of a forged mapping/graph, or
        refusal of a legitimate one, must not depend on whether some other
        manifest object was mutated in place first. (This alone does not
        discriminate R1, which was in the planner's aliasing, not the
        verifier's comparison; see
        HistoricalSixPlannerTrustAnchorTests below for that.)
        """
        legit_before = self._plan()
        baseline_digest = legit_before.manifest_sha256

        # A consumer corrupts its own returned/deep manifest in place: the
        # exact forgery from the independent review's alias.py reproduction.
        legit_before.manifest["mapping"][0]["local_seq"] = 1.0
        legit_before.manifest["mapping"][2]["event_id"] = "forged"
        legit_before.manifest["graph_edges"]["6"] = [1.0, 4.0, 5.0, 2.0]

        # A separately built forgery with the identical values must still be
        # refused: the mutation above must not have made it pass the profile.
        forged_mapping = copy.deepcopy(EXPECTED_MAPPING)
        forged_mapping[0]["local_seq"] = 1.0
        forged_mapping[2]["event_id"] = "forged"
        self._assert_refused(self._plan(mapping=forged_mapping),
                              "HISTORICAL_SIX_PLAN_PROFILE")

        # A later, identically-constructed legitimate manifest must reach the
        # same digest as before the mutation, and must still clear the
        # profile checks rather than being refused.
        legit_after = self._plan()
        self.assertEqual(legit_after.manifest_sha256, baseline_digest)
        self._assert_refused(legit_after, "SNAPSHOT_MISSING")


class HistoricalSixPlannerTrustAnchorTests(unittest.TestCase):
    """Always-on: no private snapshot required.

    R1 regression (planner side, the actual bug location):
    plan_historical_six_seed must hand back fresh mapping/graph_edges
    objects, never EXPECTED_MAPPING/EXPECTED_GRAPH_EDGES themselves. This
    exercises the real planner — sealed-byte capture, schema and
    PRAGMA integrity_check, per-record _row/_validate_row lookups, and
    manifest construction — against a locally built, self-consistent six-row
    image. Only `_validate_six`'s semantic shape checks are patched out: they
    require the private retained provider-shaped fixture and are already
    exercised, snapshot-gated, elsewhere in this file; they are orthogonal to
    the aliasing bug under test here.
    """

    @staticmethod
    def _build_sealed_image(root):
        master = root / "master.sqlite"
        EvidenceStore(master, NAMESPACE)
        with sqlite3.connect(master) as db:
            for seq, record_id, kind, event_id, _ in CATALOGUE:
                body = dict(namespace=NAMESPACE, financial_authority=False,
                            record_id=record_id, kind=kind, event_id=event_id,
                            recorded_at=float(seq), available_at=float(seq),
                            details={}, evidence=[])
                db.execute("INSERT INTO v11_records VALUES(?,?,?,?,?,?,?,?)",
                           (seq, record_id, kind, event_id, body["recorded_at"],
                            body["available_at"], canonical(body), digest(body)))
        sealed = root / "sealed.sqlite"
        with sqlite3.connect(master) as src, sqlite3.connect(sealed) as dst:
            src.backup(dst)
        return sealed, hashlib.sha256(sealed.read_bytes()).hexdigest()

    def test_planner_output_is_not_aliased_to_the_trust_anchor(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch("polymarket_scanner.v11.daily_seed_historical_six._validate_six"):
            sealed, source_sha = self._build_sealed_image(Path(tmp))

            def plan():
                return plan_historical_six_seed(
                    sealed, source_sha, target_date="2026-10-10",
                    target_event_id="future:distinct", target_generation="g")

            legit_before = plan()
            self.assertIsNot(legit_before.manifest["mapping"], EXPECTED_MAPPING)
            self.assertIsNot(legit_before.manifest["graph_edges"], EXPECTED_GRAPH_EDGES)
            baseline_digest = legit_before.manifest_sha256
            baseline_mapping_sha256 = digest(legit_before.manifest["mapping"])
            baseline_graph_edges_sha256 = digest(legit_before.manifest["graph_edges"])

            # A consumer corrupts its own returned manifest in place: the
            # exact forgery from the independent review's alias.py
            # reproduction (mutating the planner's own output, not a
            # separately built forgery).
            legit_before.manifest["mapping"][0]["local_seq"] = 1.0
            legit_before.manifest["mapping"][2]["event_id"] = "forged"
            legit_before.manifest["graph_edges"]["6"] = [1.0, 4.0, 5.0, 2.0]

            # The module-level trust anchor must be untouched by that
            # mutation (this is what R1 broke: it was the same object).
            self.assertEqual(digest(EXPECTED_MAPPING), baseline_mapping_sha256)
            self.assertEqual(digest(EXPECTED_GRAPH_EDGES), baseline_graph_edges_sha256)

            # A fresh, identically-constructed plan must be unaffected: the
            # same manifest digest as before the mutation, with mapping and
            # graph_edges matching the trust anchor, not the forged values.
            legit_after = plan()
            self.assertEqual(legit_after.manifest_sha256, baseline_digest)
            self.assertEqual(digest(legit_after.manifest["mapping"]), baseline_mapping_sha256)
            self.assertEqual(digest(legit_after.manifest["graph_edges"]),
                              baseline_graph_edges_sha256)


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

    def test_hidden_conflicting_existing_daily_copy_outside_unique_index_refused(self):
        plan = self.plan()
        target_id = plan.rows[4][1]  # decision-shadow:rule:1118070
        decoy_id = target_id[:-1] + ("9" if not target_id.endswith("9") else "8")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "daily.sqlite"
            sealed = Path(tmp) / "sealed.sqlite"
            EvidenceStore(path, "CHALLENGER:katl-shadow")
            with sqlite3.connect(path) as db:
                db.executemany("INSERT INTO v11_records VALUES(?,?,?,?,?,?,?,?)", plan.rows)
                body = dict(namespace="CHALLENGER:katl-shadow", financial_authority=False,
                            record_id=decoy_id, kind="RULE_STATE", event_id="1118070",
                            recorded_at=1792000000.0, available_at=1792000000.0,
                            details={}, evidence=[])
                db.execute("INSERT INTO v11_records VALUES(?,?,?,?,?,?,?,?)",
                           (7, decoy_id, body["kind"], body["event_id"],
                            body["recorded_at"], body["available_at"], canonical(body), digest(body)))
                db.commit()
                with sqlite3.connect(sealed) as dst:
                    db.backup(dst)
            self.assertGreaterEqual(corrupt_hidden_duplicate(sealed, decoy_id, target_id), 1)
            with closing(sqlite3.connect(sealed)) as db:
                self.assertEqual(db.execute("PRAGMA quick_check").fetchone()[0], "ok")
                self.assertNotEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            with self.assertRaisesRegex(SeedPlanError, "EXISTING_DAILY_INTEGRITY_FAILED"):
                verify_existing_historical_six_daily(sealed, plan, plan.manifest_sha256)

    def test_planner_output_is_not_aliased_to_the_trust_anchor(self):
        """R1 regression against the real planner: its returned mapping/
        graph_edges must be fresh objects, not EXPECTED_MAPPING/
        EXPECTED_GRAPH_EDGES themselves, so mutating one plan's manifest
        cannot change a later identical-input plan's digest.
        """
        plan = self.plan()
        self.assertIsNot(plan.manifest["mapping"], EXPECTED_MAPPING)
        self.assertIsNot(plan.manifest["graph_edges"], EXPECTED_GRAPH_EDGES)
        baseline_digest = plan.manifest_sha256
        plan.manifest["mapping"][0]["local_seq"] = 1.0
        plan.manifest["graph_edges"]["6"] = [1.0, 4.0, 5.0, 2.0]
        again = self.plan()
        self.assertEqual(again.manifest_sha256, baseline_digest)
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(SeedPlanError, "SNAPSHOT_MISSING"):
                verify_existing_historical_six_daily(
                    Path(tmp) / "absent.sqlite", again, again.manifest_sha256)

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
