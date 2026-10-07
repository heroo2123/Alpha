"""Adversarial checks for the proposed, unreviewed current-executable pins."""

import ast
import copy
import hashlib
import json
import os
import stat
import struct
import subprocess
import sys
import zlib
from pathlib import Path

import pytest

from tools import v11_gate3_current_executable_binding as binding

REPO = Path(__file__).resolve().parents[1]
# F4: an ambient GIT_NO_REPLACE_OBJECTS breaks the attack-setup precondition
# of tests that deliberately create a `git replace` and then expect plain
# (non-hardened) git invocations to honour it. Strip it for those callers,
# mirroring tests/test_v11_gate3_current_executable_binding_survivor_coverage.py.
ENV_HONOURING_REPLACE = {k: v for k, v in os.environ.items() if k != "GIT_NO_REPLACE_OBJECTS"}
SOURCE = "d806c11082fe81defd74993152906ee7454bce1d"
PREVIOUS_SOURCE = "58e63fc8409f49d60b2c4a06efa377a6b30ee195"
OLDER_SOURCE = "d1c5602aa77e0d835e416d281b4a78754a3a79df"
OLDER_BASELINES = {
    "polymarket_scanner/v11/evidence.py",
    "polymarket_scanner/v11/pws_admission.py",
}
REPIN_CHANGES = {
    "polymarket_scanner/v11/evidence.py": (
        "3ae3852474ad87977ccd50aac010d3d1ed813c96",),
    "polymarket_scanner/v11/pws_admission.py": (
        "3ae3852474ad87977ccd50aac010d3d1ed813c96",
        "0d55adbd70c74375c1dbdd9d0dcac0344f4202ce",
        "5c8ae333c8eb2d2c73fbfeb25ce6c707eb3b19a7",
        "b2bbb0df76ccbcf4300903c04eb2748a3ef61de6"),
    "tests/test_v11_gate3_attempt_runtime_wiring.py": (
        "ef4f534eaae442dd0aac149c9850f0ef3c76d78f",),
    "tests/test_v11_gate3_evidence_intake_launch_wiring.py": (
        "ef4f534eaae442dd0aac149c9850f0ef3c76d78f",),
    "tests/test_v11_r09_gate3_runtime.py": (
        "7adbd18b0fcd0ae1e970264334e1b477c980e5aa",
        "d806c11082fe81defd74993152906ee7454bce1d"),
    "tools/v11_gate3_evidence_preflight_real_intake.py": (
        "a582a005d08c0bcb62a9061946aa0bf096657360",),
    "tools/v11_r09_gate3_runtime.py": (
        "7adbd18b0fcd0ae1e970264334e1b477c980e5aa",
        "d806c11082fe81defd74993152906ee7454bce1d"),
}
MODULE_ROOTS = {
    "tools/v11_r09_gate3_collector.py",
    "tools/v11_r09_gate3_runtime.py",
    "tools/v11_r09_gate3_ledgers.py",
    "tools/v11_r09_gate3_message_sizes.py",
    "tools/v11_r09_gate3_launch.py",
    "tools/v11_gate3_preflight_attempt_model.py",
    "tools/v11_gate3_evidence_intake_guard.py",
    "tools/v11_gate3_raw_decoder_binding.py",
    "tools/v11_r09_gate3_store_v1.py",
}


def _source_git(*args):
    return subprocess.run(["git", "--no-replace-objects", *args], cwd=REPO,
                          env={**os.environ, "GIT_NO_REPLACE_OBJECTS": "1",
                               "GIT_GRAFT_FILE": os.devnull}, check=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout


def _source_local_import_closure():
    """Independently walk source-commit AST imports and package initializers."""
    files = set(_source_git("ls-tree", "-r", "--name-only", SOURCE).decode().splitlines())
    pending = list(MODULE_ROOTS)
    visited = set()
    while pending:
        path = pending.pop()
        if path in visited:
            continue
        visited.add(path)
        tree = ast.parse(_source_git("show", f"{SOURCE}:{path}"), filename=path)
        package = path.split("/")[:-1]
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                call = ast.unparse(node.func)
                if call in {"__import__", "importlib.import_module",
                            "importlib.util.spec_from_file_location",
                            "importlib.machinery.SourceFileLoader"}:
                    pytest.fail(f"dynamic local import needs review: {path}: {call}")
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    prefix = package[:len(package) - node.level + 1]
                    base = ".".join(prefix + ([node.module] if node.module else []))
                else:
                    base = node.module or ""
                names = [base] + [base + "." + alias.name for alias in node.names]
            else:
                continue
            for name in names:
                parts = name.split(".")
                for length in range(1, len(parts) + 1):
                    stem = "/".join(parts[:length])
                    for candidate in (stem + ".py", stem + "/__init__.py"):
                        if candidate in files and candidate not in visited:
                            pending.append(candidate)
    return visited


def test_source_local_import_closure_is_pinned():
    closure = _source_local_import_closure()
    missing = closure - binding.PATHS
    if missing:
        pytest.fail(f"source local imports lack pins: {sorted(missing)}")
    if len(closure) != 81:
        pytest.fail(f"source local import closure changed: {len(closure)}")
    for path in ("tools/v11_multimodel_panel.py",
                 "tools/v11_r09_gate3_launch_v4.py",
                 "tools/v11_r09_gate3_offline_io.py",
                 "polymarket_scanner/v11/ecmwf_grib.py"):
        if path not in closure:
            pytest.fail(f"closure scan missed direct or transitive import: {path}")


@pytest.mark.parametrize("target", ["tools/v11_multimodel_panel.py", *REPIN_CHANGES])
def test_local_import_drift_refuses_live_and_committed(tmp_path, target):
    clone = tmp_path / "checkout"
    subprocess.run(["git", "clone", "--shared", "--quiet", str(REPO), str(clone)],
                   check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    (clone / binding.MANIFEST).write_bytes((REPO / binding.MANIFEST).read_bytes())
    source = (clone / target).read_bytes()
    (clone / target).write_bytes(source + b"\n# adverse local import drift\n")
    for committed in (False, True):
        if committed:
            subprocess.run(["git", "add", "--", target], cwd=clone, check=True,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            subprocess.run(["git", "-c", "user.name=Binding Test",
                            "-c", "user.email=binding@example.invalid", "commit", "-qm",
                            "adverse local import drift"], cwd=clone, check=True,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        with pytest.raises(ValueError) as exc:
            binding.verify(clone)
        if str(exc.value) != f"current executable/dependency byte drift: {target}":
            pytest.fail(f"unexpected refusal for local import drift: {exc.value}")


def test_missing_pinned_live_file_has_stable_refusal(tmp_path):
    clone = tmp_path / "checkout"
    subprocess.run(["git", "clone", "--shared", "--quiet", str(REPO), str(clone)],
                   check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    (clone / binding.MANIFEST).write_bytes((REPO / binding.MANIFEST).read_bytes())
    target = "tools/v11_multimodel_panel.py"
    (clone / target).unlink()
    with pytest.raises(ValueError) as exc:
        binding.verify(clone)
    if str(exc.value) != f"non-regular binding path: {target}":
        pytest.fail(f"unexpected missing-file refusal: {exc.value}")


@pytest.mark.parametrize("kind", ("fifo", "socket", "symlink", "missing"))
def test_real_verifier_promptly_refuses_nonregular_pinned_path(tmp_path, kind):
    clone = tmp_path / "checkout"
    subprocess.run(["git", "clone", "--shared", "--quiet", str(REPO), str(clone)],
                   check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    (clone / binding.MANIFEST).write_bytes((REPO / binding.MANIFEST).read_bytes())
    target = "config/v11/r09_gate3_observed_message_sizes_20260930.json"
    path = clone / target
    path.unlink()
    if kind == "fifo":
        os.mkfifo(path)  # No writer: a blocking open stalls before fstat.
    elif kind == "socket":
        os.mknod(path, stat.S_IFSOCK | 0o600)  # Filesystem node; no socket syscall.
    elif kind == "symlink":
        path.symlink_to(tmp_path / "same-bytes")
        (tmp_path / "same-bytes").write_bytes((REPO / target).read_bytes())

    script = """
import sys
from pathlib import Path
from tools import v11_gate3_current_executable_binding as binding
try:
    binding.verify(Path(sys.argv[1]))
except ValueError as exc:
    if str(exc) == f"non-regular binding path: {sys.argv[2]}":
        sys.exit(0)
    raise
raise RuntimeError("nonregular pinned path was accepted")
"""
    command = [sys.executable, *(["-O"] if sys.flags.optimize else []),
               "-c", script, str(clone), target]
    try:
        result = subprocess.run(command, cwd=REPO, capture_output=True, text=True,
                                timeout=15, check=False)
    except subprocess.TimeoutExpired:
        pytest.fail(f"real verifier blocked on pinned {kind} for over 15 seconds")
    if result.returncode:
        pytest.fail(f"real verifier did not refuse pinned {kind}: {result.stderr}")


def test_live_refuses_character_device():
    # Use a real character device; creating a new one requires privileges.
    device = Path("/dev/null")
    if not stat.S_ISCHR(device.stat().st_mode):
        pytest.fail("expected /dev/null to be a character device")
    with pytest.raises(ValueError) as exc:
        binding._live(device.parent, device.name)
    if str(exc.value) != "non-regular binding path: null":
        pytest.fail(f"unexpected device refusal: {exc.value}")


def test_live_refuses_symlinked_directory_component(tmp_path):
    (tmp_path / "real").mkdir()
    (tmp_path / "real" / "file").write_bytes(b"pinned bytes")
    (tmp_path / "alias").symlink_to(tmp_path / "real", target_is_directory=True)
    with pytest.raises(ValueError) as exc:
        binding._live(tmp_path, "alias/file")
    if str(exc.value) != "non-directory binding path: alias/file":
        pytest.fail(f"unexpected directory symlink refusal: {exc.value}")


def _manifest():
    return json.loads((REPO / binding.MANIFEST).read_bytes())


def _altered(tmp_path, change):
    data = copy.deepcopy(_manifest())
    change(data)
    path = tmp_path / "binding.json"
    path.write_text(json.dumps(data, sort_keys=True))
    return path


def test_exact_candidate_pins_thirteen_histories_and_dependent_protocol_tests():
    result = binding.verify(REPO)
    expected = {"source_commit": binding.SOURCE_COMMIT,
                "source_tree": binding.SOURCE_TREE,
                "verified_files": len(binding.PATHS),
                "launchable": False, "qualification_credit": 0}
    if result != expected or len(binding.HISTORICAL) != 13 or len(binding.PATHS) != 92:
        pytest.fail(f"candidate pins or authority changed: {result}")
    if {p for p in binding.PATHS if p.startswith("tests/")} != {
        "tests/test_v11_r09_gate3_collector.py",
        "tests/test_v11_r09_gate3_message_sizes.py",
        "tests/test_v11_r09_gate3_runtime.py",
        "tests/test_v11_r09_gate3_ledgers.py",
        "tests/test_v11_gate3_attempt_runtime_wiring.py",
        "tests/test_v11_gate3_evidence_intake_launch_wiring.py",
        "tests/test_v11_gate3_raw_decoder_binding.py",
    }:
        pytest.fail("dependent protocol-test coverage changed")


def test_partial_pinning_and_claimed_credit_refuse(tmp_path):
    target = "tools/v11_r09_gate3_ledgers.py"
    for change in (
        lambda m: m["files"].pop(target),
        lambda m: m["historical_baselines"].pop(target),
        lambda m: m["drift_commits"].pop(target),
        lambda m: m.update(qualification_credit=1),
        lambda m: m.update(launchable=True),
    ):
        with pytest.raises(ValueError, match="coverage or authority"):
            binding.verify(REPO, _altered(tmp_path, change))


def test_manifest_blob_digest_and_stale_review_reuse_refuse(tmp_path):
    target = "tools/v11_r09_gate3_runtime.py"
    for change, reason in (
        (lambda m: m["files"][target].update(sha256="f" * 64), "source blob or digest"),
        (lambda m: m["files"][target].update(git_blob="f" * 40), "source blob or digest"),
        (lambda m: m["historical_baselines"][target].update(commit=binding.SOURCE_COMMIT),
         "historical review baseline"),
        (lambda m: m["drift_commits"][target].clear(), "partial drift trace"),
    ):
        with pytest.raises(ValueError, match=reason):
            binding.verify(REPO, _altered(tmp_path, change))
    with pytest.raises(ValueError, match="partial drift trace"):
        binding.verify(REPO, _altered(tmp_path, lambda m:
            m["drift_commits"][target].pop(3)))


def test_live_byte_drift_refuses_even_when_source_git_blob_is_intact(monkeypatch):
    original = binding._live
    target = "tools/v11_r09_gate3_collector.py"
    monkeypatch.setattr(binding, "_live", lambda repo, path:
                        original(repo, path) + b"\n" if path == target else original(repo, path))
    with pytest.raises(ValueError, match="current executable/dependency byte drift"):
        binding.verify(REPO)


def test_git_replace_cannot_rebind_pinned_blob(tmp_path):
    clone = tmp_path / "objects"
    subprocess.run(["git", "clone", "--shared", "--no-checkout", "--quiet", str(REPO), str(clone)],
                   check=True, env=ENV_HONOURING_REPLACE,
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    path = "tools/v11_r09_gate3_runtime.py"
    old_oid, original = binding._blob(clone, binding.SOURCE_COMMIT, path)
    changed = original + b"\n# replacement attack\n"
    new_oid = subprocess.run(["git", "hash-object", "-w", "--stdin"], cwd=clone,
                             input=changed, check=True, env=ENV_HONOURING_REPLACE,
                             stdout=subprocess.PIPE).stdout.decode().strip()
    subprocess.run(["git", "replace", old_oid, new_oid], cwd=clone, check=True,
                   env=ENV_HONOURING_REPLACE, stdout=subprocess.PIPE)
    assert subprocess.run(["git", "cat-file", "blob", old_oid], cwd=clone,
                          check=True, env=ENV_HONOURING_REPLACE,
                          stdout=subprocess.PIPE).stdout == changed
    assert binding._blob(clone, binding.SOURCE_COMMIT, path) == (old_oid, original)


def test_local_graft_cannot_forge_source_ancestry(tmp_path):
    clone = tmp_path / "objects"
    subprocess.run(["git", "clone", "--shared", "--no-checkout", "--quiet", str(REPO), str(clone)],
                   check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    unrelated = subprocess.run(["git", "commit-tree", binding.SOURCE_TREE], cwd=clone,
                               input=b"unrelated\n", check=True,
                               env={**os.environ, "GIT_AUTHOR_NAME": "Binding Test",
                                    "GIT_AUTHOR_EMAIL": "binding@example.invalid",
                                    "GIT_COMMITTER_NAME": "Binding Test",
                                    "GIT_COMMITTER_EMAIL": "binding@example.invalid"},
                               stdout=subprocess.PIPE).stdout.decode().strip()
    (clone / ".git/info/grafts").write_text(f"{unrelated} {binding.SOURCE_COMMIT}\n")
    assert not binding._raw_ancestor(clone, binding.SOURCE_COMMIT, unrelated)


def _forge_commit_graph_parent_edge(repo, real_parentless_commit, fake_parent_oid):
    """Rewrite a freshly-written commit-graph so `real_parentless_commit`
    (which has zero real parents) falsely claims `fake_parent_oid` as its
    parent. Mirrors a local attacker with ordinary filesystem write access to
    `.git/objects/info/commit-graph` (same threat model as the existing
    git-replace/git-graft forgery tests above): the trailing checksum is a
    plain hash of the file's own bytes, not tied to any secret, so it can be
    freely recomputed after tampering.
    """
    path = repo / ".git/objects/info/commit-graph"
    data = bytearray(path.read_bytes())
    if data[:4] != b"CGPH" or data[5] != 1:
        raise AssertionError(f"unsupported commit-graph format for forging probe: {data[:8]!r}")
    num_chunks = data[6]
    chunk_offset = {}
    pos = 8
    for _ in range(num_chunks + 1):
        chunk_offset[bytes(data[pos:pos + 4])] = int.from_bytes(data[pos + 4:pos + 12], "big")
        pos += 12
    oidl_off, cdat_off = chunk_offset[b"OIDL"], chunk_offset[b"CDAT"]
    oid_count = (cdat_off - oidl_off) // 20
    oids = [bytes(data[oidl_off + 20 * i:oidl_off + 20 * i + 20]).hex() for i in range(oid_count)]
    parent_field = cdat_off + 36 * oids.index(real_parentless_commit) + 20
    if int.from_bytes(data[parent_field:parent_field + 4], "big") != 0x70000000:
        raise AssertionError("expected commit-graph sentinel for a real parentless commit")
    data[parent_field:parent_field + 4] = oids.index(fake_parent_oid).to_bytes(4, "big")
    data[-20:] = hashlib.sha1(bytes(data[:-20])).digest()
    path.chmod(0o644)
    path.write_bytes(bytes(data))


def test_forged_commit_graph_cannot_fake_ancestor_edge(tmp_path, monkeypatch):
    """F2: mutant M5 (shortcutting ancestry verification to Git's own
    `merge-base --is-ancestor`, which trusts the commit-graph cache rather
    than independently walking real parent hashes) was mis-recorded as an
    equivalent mutant. A forged `.git/objects/info/commit-graph` disproves
    that: construct an honest HEAD whose real parent has no parent, forge
    that parent's graph edge to falsely claim `source` as a parent, and show
    the shortcut is fooled while the real `_raw_ancestor` walk is not.
    """
    repo = tmp_path / "forge"
    env = {**os.environ, "GIT_AUTHOR_NAME": "Binding Test", "GIT_AUTHOR_EMAIL": "binding@example.invalid",
           "GIT_COMMITTER_NAME": "Binding Test", "GIT_COMMITTER_EMAIL": "binding@example.invalid"}

    def git(*args, inp=None):
        return subprocess.run(["git", *args], cwd=repo, env=env, input=inp, check=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout.decode().strip()

    repo.mkdir()
    subprocess.run(["git", "init", "--quiet", str(repo)], check=True,
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    empty_tree = git("hash-object", "-t", "tree", os.devnull)
    root = git("commit-tree", empty_tree, inp=b"root\n")  # honest: zero real parents
    head = git("commit-tree", empty_tree, "-p", root, inp=b"head\n")  # honest HEAD, parent=root
    source = git("commit-tree", empty_tree, inp=b"unrelated source\n")  # unrelated commit
    git("update-ref", "refs/heads/head", head)
    git("update-ref", "refs/heads/source", source)
    git("commit-graph", "write", "--no-progress", "--reachable")
    _forge_commit_graph_parent_edge(repo, root, source)

    # (a) the exact forged-commit-graph construction: confirm the forged cache
    # really does fool a plain `merge-base --is-ancestor` shortcut -- i.e. this
    # is a real exploitable gap, not a hypothetical one.
    shortcut = subprocess.run(["git", "--no-replace-objects", "merge-base", "--is-ancestor",
                               source, head], cwd=repo,
                              env={**os.environ, "GIT_NO_REPLACE_OBJECTS": "1",
                                   "GIT_GRAFT_FILE": os.devnull})
    if shortcut.returncode != 0:
        pytest.fail("forged-commit-graph probe setup is invalid: shortcut was not fooled")

    # (c) demonstrate the pre-fix shape of the gap directly: a reference
    # implementation of the M5 shortcut, installed in place of the real
    # `_raw_ancestor`, wrongly admits the forged claim.
    def vulnerable_shortcut_ancestor(repo, ancestor, descendant):
        probe = subprocess.run(["git", "--no-replace-objects", "merge-base", "--is-ancestor",
                                ancestor, descendant], cwd=repo,
                               env={**os.environ, "GIT_NO_REPLACE_OBJECTS": "1",
                                    "GIT_GRAFT_FILE": os.devnull})
        return probe.returncode == 0

    monkeypatch.setattr(binding, "_raw_ancestor", vulnerable_shortcut_ancestor)
    if not binding._raw_ancestor(repo, source, head):
        pytest.fail("mutant-M5 reference shortcut unexpectedly refused; probe invalid")
    monkeypatch.undo()

    # (b) the fixed/real code: an independent parent-hash walk must refuse the
    # forged claim even though the commit-graph cache admits it.
    if binding._raw_ancestor(repo, source, head):
        pytest.fail("forged commit-graph cache fooled the real ancestry walk")

    # Defense in depth: the hardened `_git` helper (core.commitGraph=false)
    # also refuses the shortcut outright for any future caller.
    # pytest.raises(match=...) alone loses its regex assertion under -O.
    try:
        binding._git(repo, "merge-base", "--is-ancestor", source, head)
    except ValueError as exc:
        if "unavailable Git object" not in str(exc):
            pytest.fail(f"unexpected _git refusal reason: {exc}")
    else:
        pytest.fail("hardened _git() helper did not refuse the merge-base shortcut")


def test_manifest_duplicate_key_refuses(tmp_path):
    raw = (REPO / binding.MANIFEST).read_text()
    path = tmp_path / "duplicate.json"
    path.write_text(raw.replace('"launchable": false,',
                                '"launchable": false, "launchable": false,', 1))
    with pytest.raises(ValueError, match="duplicate manifest key"):
        binding.verify(REPO, path)


def test_repin_preserves_coverage_and_accounts_for_every_changed_blob():
    previous = json.loads(_source_git("show", f"{SOURCE}:{binding.MANIFEST}"))
    older = json.loads(_source_git("show", f"{PREVIOUS_SOURCE}:{binding.MANIFEST}"))
    current = _manifest()
    if (previous["source_commit"] != PREVIOUS_SOURCE
        or older["source_commit"] != OLDER_SOURCE
        or previous["source_tree"] != _source_git("rev-parse", f"{PREVIOUS_SOURCE}^{{tree}}").decode().strip()
        or older["source_tree"] != _source_git("rev-parse", f"{OLDER_SOURCE}^{{tree}}").decode().strip()
        or current["source_commit"] != SOURCE):
        pytest.fail("stored manifest source commit or tree differs from actual ancestry")
    if set(current["files"]) != set(previous["files"]):
        pytest.fail("repin changed the 92-path coverage")
    changed = {p for p in current["files"] if current["files"][p] != previous["files"][p]}
    if changed != set(REPIN_CHANGES):
        pytest.fail(f"unexpected repinned paths: {sorted(changed)}")
    for path, commits in REPIN_CHANGES.items():
        previous_blob = _source_git("rev-parse", f"{PREVIOUS_SOURCE}:{path}").decode().strip()
        if previous["files"][path]["git_blob"] != previous_blob:
            pytest.fail(f"prior binding did not describe its frozen source: {path}")
        history = _source_git("log", "--format=%H", "--no-merges",
                              f"{PREVIOUS_SOURCE}..{SOURCE}", "--", path).decode().splitlines()
        if history != list(reversed(commits)):
            pytest.fail(f"real path-change history differs: {path}: {history}")
        if path in previous["historical_baselines"]:
            if current["historical_baselines"][path] != previous["historical_baselines"][path]:
                pytest.fail(f"earlier baseline changed: {path}")
            prior_trace = previous["drift_commits"][path]
            if current["drift_commits"][path][:len(prior_trace)] != prior_trace:
                pytest.fail(f"earlier drift trace changed: {path}")
        else:
            baseline = older if path in OLDER_BASELINES else previous
            if path in OLDER_BASELINES and older["files"][path] != previous["files"][path]:
                pytest.fail(f"older source snapshot differs from prior binding: {path}")
            expected = {"commit": baseline["source_commit"], "tree": baseline["source_tree"],
                        **baseline["files"][path]}
            if current["historical_baselines"][path] != expected:
                pytest.fail(f"previous frozen observation not retained: {path}")
            prior_trace = []
        new_trace = current["drift_commits"][path][len(prior_trace):]
        if tuple(t["commit"] for t in new_trace) != commits:
            pytest.fail(f"new drift trace incomplete: {path}")
        if binding.DRIFT_COMMITS[path] != tuple(t["commit"] for t in current["drift_commits"][path]):
            pytest.fail(f"verifier trace differs: {path}")
    for path, value in previous["historical_baselines"].items():
        if current["historical_baselines"][path] != value:
            pytest.fail(f"prior historical baseline changed: {path}")
    for path, value in previous["drift_commits"].items():
        if current["drift_commits"][path][:len(value)] != value:
            pytest.fail(f"prior historical trace changed: {path}")


@pytest.mark.parametrize("target", REPIN_CHANGES)
def test_repinned_dependency_provenance_tampering_refuses(tmp_path, target):
    for change, reason in (
        (lambda m: m["historical_baselines"].pop(target), "coverage or authority"),
        (lambda m: m["historical_baselines"][target].update(commit=SOURCE),
         "historical review baseline changed"),
        (lambda m: m["drift_commits"][target].clear(), "partial drift trace"),
        (lambda m: m["drift_commits"][target][-1].update(commit=PREVIOUS_SOURCE),
         "partial drift trace"),
        (lambda m: m["drift_commits"][target][-1].update(classification=""),
         "unclassified drift commit"),
    ):
        with pytest.raises(ValueError) as exc:
            binding.verify(REPO, _altered(tmp_path, change))
        # pytest.raises(match=...) alone loses its regex assertion under -O.
        if reason not in str(exc.value):
            pytest.fail(f"unexpected provenance refusal: {exc.value}")
