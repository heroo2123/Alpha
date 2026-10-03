"""Synthetic supplied-byte comparisons only; no native probe or real witness."""
import ast
import copy
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from tools import v11_gate3_clock_checkpoint as cp
from tools import v11_gate3_clock_custody as writer
from test_v11_gate3_clock_dossier import _raw, _record


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("ascii")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def fixture(nonce="fixture-1", raws=None, reason="CALLER_FINISHED"):
    """TEST ONLY local construction, never an external checkpoint or receipt."""
    metadata = dict(session_nonce=nonce, method_id="method", profile_id="profile",
                    build_id="build", host_id="host", event_kind="SYNTHETIC")
    header = writer._header(metadata)
    objects, head, total = [], sha(header), len(header)
    for sequence, raw in enumerate([_raw(_record())] if raws is None else raws, 1):
        frame, _ = writer._object(raw, nonce, sequence, head)
        objects.append(frame)
        head = sha(frame)
        total += len(frame)
    terminal = writer._terminal(nonce, len(objects), head, total, reason)
    summary = dict(session_nonce=nonce, header_sha256=sha(header), count=len(objects),
                   object_sha256=[sha(frame) for frame in objects], head=head,
                   chain_byte_length=total, terminal_sha256=sha(terminal))
    return (header, tuple(objects), terminal), summary


def packet(items=None, generation=2):
    items = [fixture()] if items is None else items
    context = b"SYNTHETIC ONLY: no witness, receipt, authority or time evidence"
    checkpoint = dict(writer._flags(), schema="CLOCK_CHECKPOINT_FIXTURE_V1",
                      scope_id="synthetic-scope", custodian_id="synthetic-custodian",
                      generation=generation, receipt_context_sha256=sha(context),
                      receipt_context_byte_length=len(context),
                      sessions=[item[1] for item in items])
    raw = encode(checkpoint)
    anchor = dict(schema="CLOCK_CHECKPOINT_FIXTURE_PIN_V1", scope_id="synthetic-scope",
                  custodian_id="synthetic-custodian", generation=generation,
                  checkpoint_sha256=sha(raw), checkpoint_byte_length=len(raw))
    return [raw, encode(anchor), context, tuple(item[0] for item in items)]


def repin(args, checkpoint):
    """TEST ONLY attacker-controlled matching pin; still must stay unqualified."""
    args = list(args)
    args[0] = encode(checkpoint)
    anchor = json.loads(args[1])
    anchor.update(checkpoint_sha256=sha(args[0]), checkpoint_byte_length=len(args[0]))
    args[1] = encode(anchor)
    return args


class CheckpointTests(unittest.TestCase):
    def boundary(self, report):
        for key in ("execution_authority", "provider_authority", "capture_eligibility",
                    "clock_qualification", "custody_qualification", "rollback_protection",
                    "external_checkpoint_authenticated", "diagnostic_persisted"):
            self.assertIs(report[key], False)
        self.assertIs(type(report["qualification_credit"]), int)
        self.assertEqual(report["qualification_credit"], 0)
        self.assertEqual(report["g3l"], "NO_GO")
        self.assertEqual(report["custody_status"], "CUSTODY_UNQUALIFIED")
        self.assertEqual(report["anchor_freshness"], "UNKNOWN")
        self.assertEqual(report["crash_durability"], "UNKNOWN_ON_REPLAY")
        self.assertTrue(report["evidence_incomplete"])

    def refuse(self, args, code=None):
        saved = copy.deepcopy(args)
        with self.assertRaises(cp.CheckpointError) as caught:
            cp.compare_checkpoint(*args)
        if code:
            self.assertEqual(caught.exception.code, code)
        self.boundary(caught.exception.evidence)
        self.assertEqual(args, saved)
        return caught.exception.evidence

    def test_match_deterministic_and_no_mutation(self):
        args = packet([fixture("one"), fixture("two", [b"broken", b""])])
        before = copy.deepcopy(args)
        first = cp.compare_checkpoint(*args)
        self.assertEqual(first, cp.compare_checkpoint(*args))
        self.assertEqual(before, args)
        self.boundary(first)
        self.assertEqual(first["status"], "SUPPLIED_CHECKPOINT_MATCH_UNQUALIFIED")
        self.assertEqual(len(first["session_diagnostics"][1]["failures"]), 2)

    def test_missing_unknown_stale_and_wrong_scope_pins(self):
        args = packet()
        self.refuse([args[0], None, *args[2:]], "ANCHOR_REQUIRED")
        for key, value, code in (
                ("checkpoint_sha256", "0" * 64, "ANCHOR_MISMATCH"),
                ("checkpoint_byte_length", 1, "ANCHOR_MISMATCH"),
                ("generation", 1, "ANCHOR_SCOPE_OR_GENERATION"),
                ("scope_id", "other", "ANCHOR_SCOPE_OR_GENERATION"),
                ("custodian_id", "other", "ANCHOR_SCOPE_OR_GENERATION")):
            with self.subTest(key=key):
                anchor = json.loads(args[1])
                anchor[key] = value
                self.refuse([args[0], encode(anchor), *args[2:]], code)

    def test_old_valid_checkpoint_against_new_pin_refuses(self):
        old, new = packet(generation=1), packet(generation=2)
        self.refuse([old[0], new[1], *old[2:]], "ANCHOR_MISMATCH")
        # Rolling back ALL supplied bytes cannot be detected without external state.
        self.boundary(cp.compare_checkpoint(*old))

    def test_rollback_to_valid_shorter_session(self):
        current = packet([fixture(raws=[b"first", b"second"])])
        old_bundle, _ = fixture(raws=[b"first"])
        self.refuse([*current[:3], (old_bundle,)], "CHECKPOINT_SESSION_MISMATCH")

    def test_whole_consistent_history_substitution(self):
        current = packet()
        changed, _ = fixture(raws=[b"entire rewritten chain"])
        self.refuse([*current[:3], (changed,)], "CHECKPOINT_SESSION_MISMATCH")
        self.boundary(cp.compare_checkpoint(*packet([fixture(raws=[b"rewrite"])])))

    def test_all_checkpoint_and_pin_truncation_prefixes(self):
        args = packet()
        for index in (0, 1):
            for end in range(len(args[index])):
                damaged = list(args)
                damaged[index] = args[index][:end]
                self.refuse(damaged)

    def test_partial_truncated_and_substituted_session_bytes(self):
        args = packet()
        header, objects, terminal = args[3][0]
        for bundle in ((header[:-1], objects, terminal), (header, (), terminal),
                       (header, objects, b""), (header, objects, terminal[:-1]),
                       (header, (objects[0][:-1],), terminal),
                       (header, (b"\xff\xff\xff\xff",), terminal),
                       (header, (b"abc",), terminal),
                       (header, (objects[0] + b"x",), terminal)):
            self.refuse([*args[:3], (bundle,)])

    def test_duplicate_reordered_missing_and_extra_sessions(self):
        args = packet([fixture("one"), fixture("two")])
        one, two = args[3]
        self.refuse([*args[:3], (two, one)], "CHECKPOINT_SESSION_MISMATCH")
        self.refuse([*args[:3], (one, one)], "DUPLICATE_SESSION")
        self.refuse([*args[:3], (one,)], "SESSION_SET_MISMATCH")
        self.refuse([*args[:3], (one, two, one)], "SESSION_SET_MISMATCH")
        self.refuse(packet([fixture("same"), fixture("same")]), "DUPLICATE_SESSION")

    def test_duplicate_reordered_and_gapped_objects(self):
        args = packet([fixture(raws=[b"one", b"two", b"three"])])
        header, objects, terminal = args[3][0]
        for changed in ((objects[1], objects[0], objects[2]),
                        (objects[0], objects[0], objects[2]),
                        (objects[0], objects[2])):
            self.refuse([*args[:3], ((header, changed, terminal),)], "FRAME_MISMATCH")

    def test_manifest_order_and_closed_nested_schema(self):
        args = packet([fixture("one"), fixture("two")])
        value = json.loads(args[0])
        value["sessions"].reverse()
        self.refuse(repin(args, value), "CHECKPOINT_SESSION_MISMATCH")
        for key, val in (("extra", "x"), ("count", True), ("head", "0" * 64),
                         ("object_sha256", []), ("terminal_sha256", None)):
            value = json.loads(args[0])
            value["sessions"][0][key] = val
            self.refuse(repin(args, value), "CHECKPOINT_SESSION_MISMATCH")

    def test_context_exact_binding(self):
        args = packet()
        for context in (b"", b"substitute", args[2] + b"x"):
            self.refuse([*args[:2], context, args[3]])

    def test_fixed_authority_and_no_self_asserted_trust(self):
        args = packet()
        for key, value in (("execution_authority", True), ("qualification_credit", False),
                           ("g3l", "READY"), ("custody_status", "QUALIFIED"),
                           ("trusted", True), ("signature", "PASS")):
            checkpoint = json.loads(args[0])
            checkpoint[key] = value
            self.refuse(repin(args, checkpoint))

    def test_json_bounds_duplicate_keys_and_noncanonical(self):
        args = packet()
        for raw in (b'{"schema":1,"schema":2}', b"[" * 9 + b"]" * 9,
                    b"\xff", b"NaN", b"Infinity", b"1.0", b"9" * 21,
                    b'"' + b"x" * 4097 + b'"', b"[0," * 10,
                    b"[" + b"0," * 4096 + b"0]", args[1] + b"\n"):
            self.refuse([args[0], raw, *args[2:]])
        checkpoint = json.loads(args[0])
        for key, value in (("generation", True), ("generation", 0),
                           ("receipt_context_byte_length", True), ("sessions", {})):
            changed = dict(checkpoint, **{key: value})
            self.refuse(repin(args, changed))

    def test_byte_and_cardinality_caps_before_replay(self):
        args = packet()
        for index, cap in ((0, cp.MAX_CHECKPOINT), (1, cp.MAX_ANCHOR), (2, cp.MAX_CONTEXT)):
            changed = list(args)
            changed[index] = b"x" * (cap + 1)
            self.refuse(changed, "INPUT_BOUNDS")
        header, objects, terminal = args[3][0]
        for bundles in ((), args[3] * 9, ((header, objects * 65, terminal),),
                        ((header, (b"x" * (cp.MAX_OBJECT + 1),), terminal),),
                        ((header, (b"x" * cp.MAX_OBJECT,) * 17, terminal),),
                        ((b"x" * (cp.TERMINAL_RESERVE + 1), objects, terminal),),
                        ((header, objects, b"x" * (cp.TERMINAL_RESERVE + 1)),)):
            with patch.object(cp, "_replay", side_effect=AssertionError("must refuse before replay")):
                self.refuse([*args[:3], bundles], "INPUT_BOUNDS")

    def test_exact_container_types_without_user_hooks(self):
        class Hostile:
            def __len__(self):
                raise AssertionError("length hook")
            def __iter__(self):
                raise AssertionError("iterator hook")
        class Bytes(bytes):
            def __len__(self):
                raise AssertionError("bytes hook")
        args = packet()
        for index in range(4):
            for value in (Hostile(), [], {}, bytearray(b"x"), Bytes(b"x")):
                changed = list(args)
                changed[index] = value
                with self.assertRaises(cp.CheckpointError):
                    cp.compare_checkpoint(*changed)

    def test_failed_raw_records_and_capacity_terminal_survive_replay(self):
        raw = encode(dict(schema="ALPHA_V11_GATE3_CLOCK_PROBE_RECORD_V1", status="REFUSED",
                          code="CLOCK_SOURCE_UNAVAILABLE", detail_errno=38, partial={}))
        args = packet([fixture(raws=[raw, b"\x00\xfftruncated"], reason="CAPACITY_EXHAUSTED")])
        report = cp.compare_checkpoint(*args)
        diagnostic = report["session_diagnostics"][0]
        self.assertEqual(diagnostic["terminal_reason"], "CAPACITY_EXHAUSTED")
        self.assertEqual([item["status"] for item in diagnostic["failures"]],
                         ["REFUSED", "PARSE_REFUSED"])
        # No failure disappears when the complete bytes are supplied again.
        self.assertEqual(report, cp.compare_checkpoint(*args))
        self.boundary(report)

    def test_raw_hash_is_not_projection_hash(self):
        raw = _raw(_record())
        compact = json.dumps(_record(), separators=(",", ":")).encode()
        args = packet([fixture(raws=[raw])])
        other, _ = fixture(raws=[compact])
        self.refuse([*args[:3], (other,)], "CHECKPOINT_SESSION_MISMATCH")

    def test_empty_closed_session_and_max_sessions_samples(self):
        self.boundary(cp.compare_checkpoint(*packet([fixture(raws=[])])))
        args = packet([fixture(str(i), [b"x"] * 64) for i in range(8)])
        report = cp.compare_checkpoint(*args)
        self.assertEqual(len(report["sessions"]), 8)
        self.assertTrue(all(item["count"] == 64 for item in report["sessions"]))

    def test_writer_state_helpers_are_not_replay_dependencies(self):
        args = packet()
        with patch.object(writer, "_object", side_effect=AssertionError("writer dependency")), \
                patch.object(writer, "_terminal", side_effect=AssertionError("writer dependency")), \
                patch.object(writer, "_header", side_effect=AssertionError("writer dependency")):
            self.boundary(cp.compare_checkpoint(*args))

    def test_wire_fault_recomputed_pin_cannot_hide_invalid_frame(self):
        args = packet()
        header, objects, terminal = args[3][0]
        size = int.from_bytes(objects[0][:4], "big")
        envelope = json.loads(objects[0][4:4 + size])
        for key, value in (("sequence", True), ("prior_head", "0" * 64),
                           ("parsed_projection_sha256", "0" * 64),
                           ("execution_authority", True), ("extra", 1)):
            edited = encode(dict(envelope, **{key: value}))
            frame = len(edited).to_bytes(4, "big") + edited + objects[0][4 + size:]
            self.refuse([*args[:3], ((header, (frame,), terminal),)], "FRAME_MISMATCH")

    def test_supplied_byte_capability_boundary(self):
        source = (ROOT / "tools/v11_gate3_clock_checkpoint.py").read_text()
        imports = []
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                imports.extend(item.name for item in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.append(node.module)
        self.assertEqual(set(imports), {"__future__", "hashlib", "json", "re", "tools"})
        args = packet()
        from contextlib import ExitStack
        with ExitStack() as stack:
            for module, name in ((socket, "socket"), (subprocess, "Popen"), (os, "open"),
                                 (os, "urandom"), (time, "time"), (time, "monotonic"),
                                 (time, "clock_gettime")):
                stack.enter_context(patch.object(module, name, side_effect=AssertionError(name)))
            stack.enter_context(patch("builtins.open", side_effect=AssertionError("open")))
            self.boundary(cp.compare_checkpoint(*args))
            self.refuse([args[0], None, *args[2:]], "ANCHOR_REQUIRED")

    def test_filesystem_writer_interoperability_without_native_observation(self):
        args = packet([fixture(raws=[_raw(_record()), b"broken"])])
        metadata = json.loads(args[3][0][0])["metadata"]
        with tempfile.TemporaryDirectory(prefix=".checkpoint-test-", dir=ROOT) as temp:
            fd = os.open(temp, os.O_RDONLY | os.O_DIRECTORY)
            try:
                with writer.FixtureStore(fd, "fixture", metadata, create=True) as store:
                    store.append(_raw(_record()))
                    store.append(b"broken")
                    store.finish()
                    local = store.replay()
                path = Path(temp) / "fixture"
                bundle = ((path / "session.json").read_bytes(),
                          tuple((path / f"{i:06d}.obj").read_bytes() for i in (1, 2)),
                          (path / "terminal.json").read_bytes())
                self.assertEqual(bundle, args[3][0])
                report = cp.compare_checkpoint(*[*args[:3], (bundle,)])
                self.assertEqual(report["sessions"][0]["head"], local["head"])
                self.assertEqual(report["sessions"][0]["terminal_sha256"], local["terminal_sha256"])
            finally:
                os.close(fd)


if __name__ == "__main__":
    unittest.main()
