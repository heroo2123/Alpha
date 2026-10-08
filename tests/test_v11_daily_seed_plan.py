"""Offline refusal tests for the deployed preparer's sparse baseline pattern."""
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from contextlib import closing
from unittest.mock import patch

from polymarket_scanner.v11.daily_seed_plan import (
    BASE_IDS, NAMESPACE, SeedPlanError, plan_daily_seed, verify_existing_daily,
)
from polymarket_scanner.v11.evidence import EvidenceStore, canonical, digest


def file_sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def backup(source, target):
    with closing(sqlite3.connect(source)) as src, closing(sqlite3.connect(target)) as dst:
        src.backup(dst)


def corrupt_hidden_duplicate(path, decoy_id, target_id):
    """Rewrite a table b-tree leaf's record_id text, leaving its UNIQUE index

    entry pointing at the old text. This reproduces a corrupt index that
    PRAGMA quick_check cannot see but PRAGMA integrity_check does: a second
    row visible only to a full table scan, invisible to an indexed lookup.
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


class DailySeedPlanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.master = self.root / "master.sqlite"
        self.snapshot = self.root / "sealed.sqlite"
        EvidenceStore(self.master, NAMESPACE)
        self.make_source()
        backup(self.master, self.snapshot)

    def make_source(self, missing=None, bad_ref=False, bad_hash=False, external_ref=False):
        with sqlite3.connect(self.master) as db:
            for seq, rid in zip((3, 4, 6), BASE_IDS):
                if rid == missing:
                    continue
                refs = []
                if seq == 4:
                    raw = self._body(BASE_IDS[0], 3, [])
                    refs = [{"id": BASE_IDS[0], "sha256": "0" * 64 if bad_ref else digest(raw)}]
                if seq == 6:
                    md = self._body(BASE_IDS[1], 4, [{"id": BASE_IDS[0], "sha256": digest(self._body(BASE_IDS[0], 3, []))}])
                    refs = [{"id": BASE_IDS[1], "sha256": digest(md)}]
                    if external_ref:
                        refs.append({"id": "outside:dependency", "sha256": "a" * 64})
                body = self._body(rid, seq, refs)
                db.execute("INSERT INTO v11_records VALUES(?,?,?,?,?,?,?,?)",
                           (seq, rid, body["kind"], body["event_id"], body["recorded_at"],
                            body["available_at"], canonical(body),
                            "f" * 64 if bad_hash and seq == 6 else digest(body)))

    @staticmethod
    def _body(rid, seq, refs):
        body = dict(namespace=NAMESPACE, financial_authority=False, record_id=rid,
                    kind="STATION_METADATA" if rid == BASE_IDS[0] else
                         "MEASUREMENT" if rid == BASE_IDS[2] else "REGISTRY",
                    event_id="station:KATL", recorded_at=float(seq), available_at=float(seq))
        if rid == BASE_IDS[0]:
            body.update(provider="synthetic", source_identity="KATL", revision="fixture",
                        payload={}, observed_at=None, issued_at=None, published_at=None,
                        received_at=float(seq), evidence_class="SYNTHETIC",
                        source_kind="STATION_METADATA")
        elif rid == BASE_IDS[1]:
            metadata = dict(station="KATL", city="Atlanta", country="US", latitude=33.0,
                            longitude=-84.0, elevation_m=300.0, timezone="America/New_York",
                            settlement_source="fixture", observation_providers=["fixture"],
                            forecast_providers=["fixture"], source_payload_sha256="a" * 64,
                            retrieved_at=float(seq))
            body.update(details=dict(action="METADATA", metadata=metadata,
                                     metadata_fingerprint="b" * 64, material_changed=False,
                                     state="DISCOVERED", reason="OBSERVED_NOT_CERTIFIED"),
                        evidence=refs)
        else:
            body.update(details=dict(financial_authority=False, real_orders=False,
                                     release_git_sha="a" * 40, release_tree_sha="b" * 40),
                        evidence=refs)
        return body

    def plan(self):
        return plan_daily_seed(self.snapshot, file_sha(self.snapshot))

    def altered_snapshot(self, name, alter, record_id=BASE_IDS[0]):
        target = self.root / name
        backup(self.snapshot, target)
        with closing(sqlite3.connect(target)) as db:
            with db:
                db.execute("DROP TRIGGER v11_no_update")
                raw = db.execute("SELECT body FROM v11_records WHERE record_id=?",
                                 (record_id,)).fetchone()[0]
                body = json.loads(raw)
                alter(body)
                db.execute("UPDATE v11_records SET body=?, body_sha256=?, kind=? WHERE record_id=?",
                           (canonical(body), digest(body), body["kind"], record_id))
                db.execute("CREATE TRIGGER v11_no_update BEFORE UPDATE ON v11_records "
                           "BEGIN SELECT RAISE(ABORT,'APPEND_ONLY'); END")
            db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            db.execute("PRAGMA journal_mode=DELETE")
        return target

    def test_sparse_source_plans_contiguous_local_genesis_and_preserves_envelopes(self):
        plan = self.plan()
        self.assertEqual([row[0] for row in plan.rows], [1, 2, 3])
        self.assertEqual([entry["source_seq"] for entry in plan.manifest["mapping"]], [3, 4, 6])
        self.assertEqual(plan.manifest_sha256, digest(plan.manifest))
        with sqlite3.connect(self.master) as db:
            original = db.execute("SELECT record_id,body,body_sha256,recorded_at,available_at FROM v11_records ORDER BY seq").fetchall()
        self.assertEqual([(row[1], row[6], row[7], row[4], row[5]) for row in plan.rows], original)
        self.assertEqual(file_sha(self.snapshot), plan.manifest["source_snapshot_sha256"])

    def test_existing_sparse_and_partial_refused_without_mutation(self):
        plan = self.plan()
        before = file_sha(self.snapshot)
        with self.assertRaisesRegex(SeedPlanError, "EXISTING_DAILY_SPARSE_OR_PARTIAL"):
            verify_existing_daily(self.snapshot, plan, plan.manifest_sha256)
        self.assertEqual(file_sha(self.snapshot), before)
        empty = self.root / "empty.sqlite"
        EvidenceStore(empty, NAMESPACE)
        backup(empty, self.root / "empty-sealed.sqlite")
        with self.assertRaisesRegex(SeedPlanError, "EXISTING_DAILY_SPARSE_OR_PARTIAL"):
            verify_existing_daily(self.root / "empty-sealed.sqlite", plan, plan.manifest_sha256)

    def test_contiguous_seed_and_append_are_admitted(self):
        plan = self.plan()
        destination = self.root / "new.sqlite"
        EvidenceStore(destination, NAMESPACE)
        with sqlite3.connect(destination) as db:
            db.executemany("INSERT INTO v11_records VALUES(?,?,?,?,?,?,?,?)", plan.rows)
            body = self._body("future:append", 7, [])
            db.execute("INSERT INTO v11_records VALUES(?,?,?,?,?,?,?,?)",
                       (4, body["record_id"], body["kind"], body["event_id"],
                        body["recorded_at"], body["available_at"], canonical(body), digest(body)))
        sealed = self.root / "new-sealed.sqlite"
        backup(destination, sealed)
        self.assertEqual(verify_existing_daily(sealed, plan, plan.manifest_sha256), 4)
        tampered = dict(plan.manifest)
        tampered["mapping"] = [dict(item) for item in plan.manifest["mapping"]]
        tampered["mapping"][0]["source_seq"] = 999
        with self.assertRaisesRegex(SeedPlanError, "SEED_MANIFEST_IDENTITY_MISMATCH"):
            verify_existing_daily(sealed, replace(plan, manifest=tampered), plan.manifest_sha256)
        with self.assertRaisesRegex(SeedPlanError, "SEED_MANIFEST_IDENTITY_MISMATCH"):
            verify_existing_daily(sealed, plan, "0" * 64)

    def test_middle_and_final_gaps_refused(self):
        plan = self.plan()
        for missing, remaining in ((2, (1, 3)), (3, (1, 2, 4))):
            target = self.root / f"gap-{missing}.sqlite"
            EvidenceStore(target, NAMESPACE)
            with sqlite3.connect(target) as db:
                db.executemany("INSERT INTO v11_records VALUES(?,?,?,?,?,?,?,?)",
                               [plan.rows[i - 1] for i in remaining if i <= 3])
                if 4 in remaining:
                    body = self._body("future:append", 7, [])
                    db.execute("INSERT INTO v11_records VALUES(?,?,?,?,?,?,?,?)",
                               (4, body["record_id"], body["kind"], body["event_id"],
                                body["recorded_at"], body["available_at"], canonical(body), digest(body)))
            sealed = self.root / f"gap-{missing}-sealed.sqlite"
            backup(target, sealed)
            with self.assertRaises(SeedPlanError):
                verify_existing_daily(sealed, plan, plan.manifest_sha256)

    def test_sidecars_and_hardlinks_refused(self):
        sidecar = Path(str(self.snapshot) + "-wal")
        sidecar.write_bytes(b"synthetic")
        with self.assertRaisesRegex(SeedPlanError, "SNAPSHOT_SIDECAR_REFUSED"):
            self.plan()
        sidecar.unlink()
        alias = self.root / "hardlink.sqlite"
        alias.hardlink_to(self.snapshot)
        with self.assertRaisesRegex(SeedPlanError, "SNAPSHOT_FILE_BOUNDS_OR_ALIAS"):
            self.plan()

    def test_source_identity_missing_closure_and_hash_refusals(self):
        with self.assertRaisesRegex(SeedPlanError, "SOURCE_SNAPSHOT_IDENTITY_CHANGED"):
            plan_daily_seed(self.snapshot, "0" * 64)
        for mode, message in (("missing", "BASELINE_MISSING"),
                              ("ref", "BASELINE_REFERENCE_HASH_MISMATCH"),
                              ("hash", "BASELINE_ENVELOPE_OR_HASH")):
            source = self.root / (mode + ".sqlite")
            sealed = self.root / (mode + "-sealed.sqlite")
            EvidenceStore(source, NAMESPACE)
            self.master = source
            self.make_source(missing=BASE_IDS[2] if mode == "missing" else None,
                             bad_ref=mode == "ref", bad_hash=mode == "hash")
            backup(source, sealed)
            with self.assertRaisesRegex(SeedPlanError, message):
                plan_daily_seed(sealed, file_sha(sealed))

    def test_zero_and_negative_source_sequences_refused(self):
        for invalid_seq in (0, -1):
            source = self.root / f"seq-{invalid_seq}.sqlite"
            sealed = self.root / f"seq-{invalid_seq}-sealed.sqlite"
            EvidenceStore(source, NAMESPACE)
            with sqlite3.connect(source) as db:
                for index, rid in enumerate(BASE_IDS):
                    body = self._body(rid, 3 + index, [])
                    db.execute("INSERT INTO v11_records VALUES(?,?,?,?,?,?,?,?)",
                               (invalid_seq if index == 0 else 4 + index, rid,
                                body["kind"], body["event_id"], body["recorded_at"],
                                body["available_at"], canonical(body), digest(body)))
            backup(source, sealed)
            with self.assertRaisesRegex(SeedPlanError, "BASELINE_ENVELOPE_OR_HASH"):
                plan_daily_seed(sealed, file_sha(sealed))

    def test_unreviewed_dependency_and_path_alias_refused(self):
        source = self.root / "external.sqlite"
        sealed = self.root / "external-sealed.sqlite"
        EvidenceStore(source, NAMESPACE)
        self.master = source
        self.make_source(external_ref=True)
        backup(source, sealed)
        with self.assertRaisesRegex(SeedPlanError, "BASELINE_CLOSURE_UNREVIEWED"):
            plan_daily_seed(sealed, file_sha(sealed))
        alias = self.root / "alias.sqlite"
        alias.symlink_to(self.snapshot)
        with self.assertRaisesRegex(SeedPlanError, "SNAPSHOT_SYMLINK_REFUSED"):
            plan_daily_seed(alias, file_sha(self.snapshot))

    def test_source_path_swap_cannot_change_pinned_rows(self):
        pinned = file_sha(self.snapshot)
        other = self.altered_snapshot("other.sqlite", lambda body: body.update(source_identity="changed"))
        original_connect = sqlite3.connect
        saved = self.root / "saved.sqlite"

        def swap_around_connect(*args, **kwargs):
            self.snapshot.rename(saved)
            other.rename(self.snapshot)
            try:
                return original_connect(*args, **kwargs)
            finally:
                self.snapshot.rename(other)
                saved.rename(self.snapshot)

        with patch("polymarket_scanner.v11.daily_seed_plan.sqlite3.connect", swap_around_connect):
            with self.assertRaisesRegex(SeedPlanError, "SNAPSHOT_PATH_CHANGED"):
                plan_daily_seed(self.snapshot, pinned)
        self.assertEqual(file_sha(self.snapshot), pinned)

    def test_existing_path_swap_cannot_admit_sparse_inode(self):
        plan = self.plan()
        good = self.root / "good.sqlite"
        EvidenceStore(good, NAMESPACE)
        with sqlite3.connect(good) as db:
            db.executemany("INSERT INTO v11_records VALUES(?,?,?,?,?,?,?,?)", plan.rows)
        good_sealed = self.root / "good-sealed.sqlite"
        backup(good, good_sealed)
        original_connect = sqlite3.connect
        saved = self.root / "saved.sqlite"

        def swap_around_connect(*args, **kwargs):
            self.snapshot.rename(saved)
            good_sealed.rename(self.snapshot)
            try:
                return original_connect(*args, **kwargs)
            finally:
                self.snapshot.rename(good_sealed)
                saved.rename(self.snapshot)

        with patch("polymarket_scanner.v11.daily_seed_plan.sqlite3.connect", swap_around_connect):
            with self.assertRaisesRegex(SeedPlanError, "EXISTING_DAILY_SPARSE_OR_PARTIAL"):
                verify_existing_daily(self.snapshot, plan, plan.manifest_sha256)
        with original_connect(self.snapshot) as db:
            self.assertEqual([r[0] for r in db.execute("SELECT seq FROM v11_records ORDER BY seq")],
                             [3, 4, 6])

    def test_reference_payload_shape_and_raw_dependency_refusals(self):
        changes = (
            ("raw-missing.sqlite", lambda b: b["payload"].update(
                raw_evidence_id="missing:raw", raw_evidence_sha256="a" * 64), "CLOSURE_UNREVIEWED"),
            ("raw-orphan-hash.sqlite", lambda b: b["payload"].update(
                raw_evidence_sha256="a" * 64), "REFERENCES_INVALID"),
            ("capture-orphan-hash.sqlite", lambda b: b["payload"].update(
                source_capture_sha256="a" * 64), "REFERENCES_INVALID"),
            ("nonobject.sqlite", lambda b: b.update(payload=["malformed"]), "REFERENCES_INVALID"),
            ("unknown-payload.sqlite", lambda b: b["payload"].update(
                unknown_reference_id="missing"), "PAYLOAD_UNREVIEWED"),
        )
        for name, alter, error in changes:
            with self.subTest(name=name):
                target = self.altered_snapshot(name, alter)
                with self.assertRaisesRegex(SeedPlanError, error):
                    plan_daily_seed(target, file_sha(target))

    def test_dangling_sidecars_refused_for_source_and_existing(self):
        plan = self.plan()
        for suffix in ("-wal", "-shm", "-journal"):
            with self.subTest(suffix=suffix):
                sidecar = Path(str(self.snapshot) + suffix)
                sidecar.symlink_to(self.root / ("absent" + suffix))
                try:
                    with self.assertRaisesRegex(SeedPlanError, "SNAPSHOT_SIDECAR_REFUSED"):
                        self.plan()
                    with self.assertRaisesRegex(SeedPlanError, "SNAPSHOT_SIDECAR_REFUSED"):
                        verify_existing_daily(self.snapshot, plan, plan.manifest_sha256)
                finally:
                    sidecar.unlink()

    def test_selected_baseline_kind_and_body_shapes_refused(self):
        def source_result(body):
            for field in ("revision", "payload", "observed_at", "issued_at", "published_at",
                          "received_at", "evidence_class", "source_kind", "source_identity"):
                body.pop(field)
            body.update(kind="SOURCE_RESULT", provider="synthetic", cycle_id="fixture",
                        state="SUCCESS", reason="fixture", elapsed_ms=1.0,
                        capture_ids=["missing:capture"], attempts=1, retry_not_before=None)

        cases = (
            ("source-result.sqlite", BASE_IDS[0], source_result),
            ("unknown-kind.sqlite", BASE_IDS[0], lambda b: b.update(
                kind="UNKNOWN_KIND", details=["malformed"])),
            ("top-level-dependency.sqlite", BASE_IDS[0], lambda b: b.update(capture_ids=["missing:capture"])),
            ("malformed-details.sqlite", BASE_IDS[1], lambda b: b.update(details=["malformed"])),
            ("details-dependency.sqlite", BASE_IDS[1], lambda b: b["details"].update(
                raw_evidence_id="missing:raw")),
            ("nested-metadata-dependency.sqlite", BASE_IDS[1], lambda b: b["details"]["metadata"].update(
                capture_ids=["missing:capture"])),
        )
        for name, record_id, alter in cases:
            with self.subTest(name=name):
                target = self.altered_snapshot(name, alter, record_id)
                with self.assertRaisesRegex(SeedPlanError, "BASELINE_BODY_UNREVIEWED"):
                    plan_daily_seed(target, file_sha(target))

    def test_hidden_conflicting_source_copy_outside_unique_index_refused(self):
        decoy = BASE_IDS[0][:-1] + ("9" if not BASE_IDS[0].endswith("9") else "8")
        body = self._body(decoy, 9000, [])
        with sqlite3.connect(self.master) as db:
            db.execute("INSERT INTO v11_records VALUES(?,?,?,?,?,?,?,?)",
                       (9000, decoy, body["kind"], body["event_id"], body["recorded_at"],
                        body["available_at"], canonical(body), digest(body)))
        corrupted = self.root / "corrupted-source.sqlite"
        backup(self.master, corrupted)
        self.assertGreaterEqual(corrupt_hidden_duplicate(corrupted, decoy, BASE_IDS[0]), 1)
        with closing(sqlite3.connect(corrupted)) as db:
            self.assertEqual(db.execute("PRAGMA quick_check").fetchone()[0], "ok")
            self.assertNotEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        with self.assertRaisesRegex(SeedPlanError, "SOURCE_INTEGRITY_FAILED"):
            plan_daily_seed(corrupted, file_sha(corrupted))

    def test_hidden_conflicting_existing_daily_copy_outside_unique_index_refused(self):
        plan = self.plan()
        target_id = plan.rows[0][1]
        decoy = target_id[:-1] + ("9" if not target_id.endswith("9") else "8")
        destination = self.root / "daily.sqlite"
        EvidenceStore(destination, NAMESPACE)
        with sqlite3.connect(destination) as db:
            db.executemany("INSERT INTO v11_records VALUES(?,?,?,?,?,?,?,?)", plan.rows)
            body = self._body(decoy, 9000, [])
            db.execute("INSERT INTO v11_records VALUES(?,?,?,?,?,?,?,?)",
                       (4, decoy, body["kind"], body["event_id"], body["recorded_at"],
                        body["available_at"], canonical(body), digest(body)))
        sealed = self.root / "daily-sealed.sqlite"
        backup(destination, sealed)
        self.assertGreaterEqual(corrupt_hidden_duplicate(sealed, decoy, target_id), 1)
        with closing(sqlite3.connect(sealed)) as db:
            self.assertEqual(db.execute("PRAGMA quick_check").fetchone()[0], "ok")
            self.assertNotEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        with self.assertRaisesRegex(SeedPlanError, "EXISTING_DAILY_INTEGRITY_FAILED"):
            verify_existing_daily(sealed, plan, plan.manifest_sha256)


if __name__ == "__main__":
    unittest.main()
