"""Start contract for the offline InventoryTransform SHADOW observer."""
import ast
import copy
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from polymarket_scanner.v11 import inventory_shadow as shadow
from polymarket_scanner.v11 import inventory_shadow_start as start
from polymarket_scanner.v11.structural_evidence import Limits


REPO = Path(__file__).resolve().parents[1]
MODULE = "polymarket_scanner.v11.inventory_shadow_start"
FIXTURE = Path(__file__).parent / "fixtures" / "v11_inventory_transforms" / "singapore_20261003_api_observed.json"
EVENT = "highest-temperature-in-singapore-on-october-3-2026"
CHILD_ENV = {"PATH": os.defpath, "PYTHONDONTWRITEBYTECODE": "1"}


def write_fixture(directory, name="a.json", mutate=None):
    value = json.loads(FIXTURE.read_text())
    if mutate is not None:
        mutate(value)
    directory.mkdir(exist_ok=True)
    path = directory / name
    path.write_text(json.dumps(value))
    return path


def unknown_coverage(value):
    value["payload"] = {"data": [], "pagination": {"has_more": False, "next_cursor": 0}}
    value["coverage"]["state"] = "UNKNOWN"


def complete_coverage(value):
    value["payload"] = {"data": [], "pagination": {"has_more": False, "next_cursor": None}}
    value["source"]["parameters"] = [["start", "0"], ["end", "10"]]
    value["source"]["window_start"] = 0
    value["source"]["window_end"] = 10
    value["coverage"]["state"] = "COMPLETE"


def output_dir(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    return out


def without_created(summary):
    value = copy.deepcopy(summary)
    for item in value["artifacts"]:
        del item["created"]
    return value


def child(*argv):
    return subprocess.run([sys.executable, *argv], cwd=REPO, env=CHILD_ENV,
                          capture_output=True, text=True, timeout=120)


def test_start_command_emits_only_shadow_diagnostics_with_ambient_access_denied(tmp_path):
    write_fixture(tmp_path / "in")
    out = output_dir(tmp_path)
    argv = ("-m", MODULE, "--input-dir", str(tmp_path / "in"), "--event", EVENT, "--output-dir", str(out))
    first = child(*argv)
    assert first.returncode == 0, first.stderr
    summary = json.loads(first.stdout)
    assert summary["version"] == start.START_VERSION
    assert summary["activation"] == "OFFLINE_SHADOW_DIAGNOSTICS_ONLY"
    assert summary["mode"] == "V11_SHADOW"
    assert summary["financial_authority"] is summary["qualification"] is summary["transaction_level_proof"] is False
    assert summary["account_effects"] == summary["order_effects"] == []
    assert summary["evidence_class_counts"] == {"CHAIN_RECEIPT": 0}
    (item,) = summary["artifacts"]
    assert item["created"] is True and item["coverage"] == "INCOMPLETE"
    assert item["artifact"] == start.artifact_name(item["source_file_sha256"], EVENT)
    assert [p.name for p in out.iterdir()] == [item["artifact"]]
    saved = (out / item["artifact"]).read_bytes()
    report = json.loads(saved)
    assert report["observation_id"] == item["observation_id"]
    assert report["financial_authority"] is report["qualification"] is report["transaction_level_proof"] is False
    assert report["account_effects"] == report["order_effects"] == []
    assert report["evidence_class_counts"] == {"API_OBSERVED": 13, "CHAIN_RECEIPT": 0, "SYNTHETIC_PROOF": 0}
    assert report["receipt_status"] == "NO_VERIFIED_RECEIPTS"
    second = child(*argv)
    assert second.returncode == 0, second.stderr
    replay = json.loads(second.stdout)
    assert replay["artifacts"][0]["created"] is False
    assert without_created(replay) == without_created(summary)
    assert [p.name for p in out.iterdir()] == [item["artifact"]]
    assert (out / item["artifact"]).read_bytes() == saved


def test_process_guard_denies_socket_dns_and_child_processes():
    code = (
        "import os, sys\n"
        "from polymarket_scanner.v11 import inventory_shadow_start as start\n"
        "start._deny_ambient_access()\n"
        "import socket\n"
        "probes = (lambda: socket.socket(), lambda: socket.getaddrinfo('localhost', 80),\n"
        "          lambda: socket.gethostbyname('localhost'), lambda: os.system('true'))\n"
        "for probe in probes:\n"
        "    try:\n"
        "        probe()\n"
        "    except RuntimeError as exc:\n"
        "        assert 'INVENTORY_SHADOW_AMBIENT_ACCESS_DENIED' in str(exc)\n"
        "    else:\n"
        "        sys.exit(3)\n"
        "print('ALL_DENIED')\n"
    )
    result = child("-c", code)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ALL_DENIED"


def test_process_guard_denies_raw_subprocess_and_ctypes_socket_bypass():
    code = (
        "import sys\n"
        "from polymarket_scanner.v11 import inventory_shadow_start as start\n"
        "start._deny_ambient_access()\n"
        "def fork_exec_probe():\n"
        "    import _posixsubprocess\n"
        "    return _posixsubprocess.fork_exec\n"
        "def ctypes_socket_probe():\n"
        "    import ctypes\n"
        "    return ctypes.CDLL(None).socket(2, 1, 0)\n"
        "for probe in (fork_exec_probe, ctypes_socket_probe):\n"
        "    try:\n"
        "        probe()\n"
        "    except RuntimeError as exc:\n"
        "        assert 'INVENTORY_SHADOW_AMBIENT_ACCESS_DENIED' in str(exc)\n"
        "    else:\n"
        "        sys.exit(3)\n"
        "print('ALL_DENIED')\n"
    )
    result = child("-c", code)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ALL_DENIED"


def test_process_guard_denies_cffi_backend_socket_bypass():
    # cffi's FFI().dlopen(None) can reach a raw libc handle, and thus a raw
    # socket fd, the same way ctypes.CDLL(None) can (finding L-B). The load
    # that must be denied is the direct backend import: `import cffi` alone
    # does not pull in the native backend (cffi.api.FFI.__init__ imports
    # `_cffi_backend` lazily, only once an FFI instance is constructed), and
    # `cffi.FFI()` on this interpreter happens to already fail earlier, at
    # pycparser's `from subprocess import check_output` -> `_posixsubprocess`
    # import, which is denied for an unrelated reason and would still fail
    # this way even if `_cffi_backend` were missing from the deny set. So the
    # probe pins `_cffi_backend` by a literal (not by looping over the set
    # under test) and checks the direct import first, with an explicit exit
    # code per failure mode instead of a child-side `assert`.
    assert {"ctypes", "_ctypes", "_posixsubprocess", "_cffi_backend"} <= start._DENIED_IMPORT_MODULES
    code = (
        "import sys\n"
        "from polymarket_scanner.v11 import inventory_shadow_start as start\n"
        "start._deny_ambient_access()\n"
        "try:\n"
        "    import _cffi_backend\n"
        "except RuntimeError as exc:\n"
        "    if str(exc) != 'INVENTORY_SHADOW_AMBIENT_ACCESS_DENIED:import':\n"
        "        sys.exit(4)\n"
        "else:\n"
        "    sys.exit(3)\n"
        "if '_cffi_backend' in sys.modules or '_posixsubprocess' in sys.modules:\n"
        "    sys.exit(5)\n"
        "import cffi\n"
        "try:\n"
        "    cffi.FFI()\n"
        "except RuntimeError:\n"
        "    pass\n"
        "else:\n"
        "    sys.exit(6)\n"
        "print('DENIED')\n"
    )
    result = child("-c", code)
    assert result.returncode == 0, (result.returncode, result.stderr)
    assert result.stdout.strip() == "DENIED"


def test_process_guard_denies_cffi_backend_dotted_alias_and_preload(tmp_path):
    # F2: an exact-name deny can miss the same extension file reimported
    # under a dotted alias (`aliaspkg._cffi_backend`), both at import time
    # and when it is already in sys.modules before the hook installs. The
    # guard must match the import audit event's and sys.modules name's last
    # dotted component, not the full dotted name. If that match ever
    # regresses, this probe proves raw libc was reached with `getpid()`
    # only; it never opens a socket.
    import importlib.util
    spec = importlib.util.find_spec("_cffi_backend")
    assert spec is not None and spec.origin
    backend_dir = os.path.dirname(spec.origin)
    path = write_fixture(tmp_path / "in")
    out = output_dir(tmp_path)

    alias_setup = (
        "import types\n"
        "pkg = types.ModuleType('aliaspkg')\n"
        f"pkg.__path__ = [{backend_dir!r}]\n"
        "sys.modules['aliaspkg'] = pkg\n"
    )

    # Import-time: the dotted alias must be denied exactly like the bare name.
    code = (
        "import sys, os\n"
        + alias_setup +
        "from polymarket_scanner.v11 import inventory_shadow_start as start\n"
        "start._deny_ambient_access()\n"
        "try:\n"
        "    import aliaspkg._cffi_backend as backend\n"
        "except RuntimeError as exc:\n"
        "    if str(exc) != 'INVENTORY_SHADOW_AMBIENT_ACCESS_DENIED:import':\n"
        "        sys.exit(4)\n"
        "else:\n"
        "    lib = backend.load_library(None)\n"
        "    bint = backend.new_primitive_type('int')\n"
        "    fn = lib.load_function(backend.new_function_type((), bint, False), 'getpid')\n"
        "    sys.exit(7 if fn() == os.getpid() else 8)\n"
        "if 'aliaspkg._cffi_backend' in sys.modules or '_cffi_backend' in sys.modules:\n"
        "    sys.exit(5)\n"
        "print('DENIED')\n"
    )
    result = child("-c", code)
    assert result.returncode == 0, (result.returncode, result.stderr)
    assert result.stdout.strip() == "DENIED"

    # Preload-time: the same alias, already imported before the hook
    # installs, must be caught by DENIED_MODULE_PRELOADED just like the
    # bare name is.
    code = (
        "import sys\n"
        + alias_setup +
        "import aliaspkg._cffi_backend\n"
        "from polymarket_scanner.v11 import inventory_shadow_start as start\n"
        "raise SystemExit(start.guarded_main(sys.argv[1:]))\n"
    )
    result = child("-c", code, "--input", str(path), "--event", EVENT, "--output-dir", str(out))
    assert result.returncode == 2, (result.returncode, result.stderr)
    assert "DENIED_MODULE_PRELOADED" in result.stderr
    assert list(out.iterdir()) == []


def test_denied_import_match_fails_closed_on_nul_suffixed_and_str_subclass_names():
    # R1: CPython resolves an extension module's init symbol from a C
    # string, which truncates at the first NUL, so '_cffi_backend\x00x' can
    # load the same extension as '_cffi_backend' while its last dotted
    # component does not literally match any denied name.
    assert start._denied_import_match("_cffi_backend\x00x") is True
    assert start._denied_import_match("harmless\x00_cffi_backend") is True
    # R2: a str subclass can override rpartition (or __eq__/__hash__) on the
    # instance the loader passes through the audit event, so anything that
    # is not exactly `str` must be denied outright rather than matched.
    class EvilRpartition(str):
        def rpartition(self, sep):
            return ("", "", "harmless")

    class EvilEqHash(str):
        def __eq__(self, other):
            return False

        def __hash__(self):
            return 0

    assert start._denied_import_match(EvilRpartition("_cffi_backend")) is True
    assert start._denied_import_match(EvilEqHash("_cffi_backend")) is True
    # Ordinary names are unaffected: no false denial of a legitimate import.
    assert start._denied_import_match("_cffi_backend") is True
    assert start._denied_import_match("aliaspkg._cffi_backend") is True
    assert start._denied_import_match("json") is False
    assert start._denied_import_match("os.path") is False


def test_process_guard_denies_cffi_backend_nul_suffixed_name_bypass(tmp_path):
    # R1 (independent review of the F1/F2 repair, 7dd1aa0): a NUL-suffixed
    # spec name resolves the identical extension file through
    # `_imp.create_dynamic`/`ExtensionFileLoader` because CPython reads the
    # init symbol from a C string, which the NUL truncates. The guard must
    # deny the name outright rather than match its (unmatchable) last dotted
    # component. If this ever regresses, the probe proves raw libc was
    # reached with `getpid()` only; it never opens a socket.
    path = write_fixture(tmp_path / "in")
    out = output_dir(tmp_path)

    code = (
        "import sys, os, _imp\n"
        "import importlib.util, importlib.machinery\n"
        "from polymarket_scanner.v11 import inventory_shadow_start as start\n"
        "start._deny_ambient_access()\n"
        "spec = importlib.util.find_spec('_cffi_backend')\n"
        "name = '_cffi_backend\\x00x'\n"
        "loader = importlib.machinery.ExtensionFileLoader(name, spec.origin)\n"
        "newspec = importlib.util.spec_from_loader(name, loader, origin=spec.origin)\n"
        "try:\n"
        "    backend = _imp.create_dynamic(newspec)\n"
        "except RuntimeError as exc:\n"
        "    if str(exc) != 'INVENTORY_SHADOW_AMBIENT_ACCESS_DENIED:import':\n"
        "        sys.exit(4)\n"
        "else:\n"
        "    lib = backend.load_library(None)\n"
        "    bint = backend.new_primitive_type('int')\n"
        "    fn = lib.load_function(backend.new_function_type((), bint, False), 'getpid')\n"
        "    sys.exit(7 if fn() == os.getpid() else 8)\n"
        "print('DENIED')\n"
    )
    result = child("-c", code)
    assert result.returncode == 0, (result.returncode, result.stderr)
    assert result.stdout.strip() == "DENIED"

    # Preload-time: the same NUL-suffixed name, already in sys.modules
    # before the hook installs, must be caught by DENIED_MODULE_PRELOADED
    # just like the bare name is.
    code = (
        "import sys, _imp\n"
        "import importlib.util, importlib.machinery\n"
        "spec = importlib.util.find_spec('_cffi_backend')\n"
        "name = '_cffi_backend\\x00x'\n"
        "loader = importlib.machinery.ExtensionFileLoader(name, spec.origin)\n"
        "newspec = importlib.util.spec_from_loader(name, loader, origin=spec.origin)\n"
        "sys.modules[name] = _imp.create_dynamic(newspec)\n"
        "from polymarket_scanner.v11 import inventory_shadow_start as start\n"
        "raise SystemExit(start.guarded_main(sys.argv[1:]))\n"
    )
    result = child("-c", code, "--input", str(path), "--event", EVENT, "--output-dir", str(out))
    assert result.returncode == 2, (result.returncode, result.stderr)
    assert "DENIED_MODULE_PRELOADED" in result.stderr
    assert list(out.iterdir()) == []


def test_process_guard_denies_cffi_backend_str_subclass_rpartition_override_bypass():
    # R2 (independent review of the F1/F2 repair, 7dd1aa0): the matcher
    # calls `name.rpartition(".")`, a method an attacker-controlled `str`
    # subclass instance can override to report a harmless split while
    # `_imp.create_dynamic` still resolves the real denied extension by its
    # true name. The guard must deny any name that is not exactly `str`
    # rather than call a method the attacker controls. If this ever
    # regresses, the probe proves raw libc was reached with `getpid()` only;
    # it never opens a socket.
    code = (
        "import sys, os, _imp\n"
        "import importlib.util, importlib.machinery\n"
        "from polymarket_scanner.v11 import inventory_shadow_start as start\n"
        "start._deny_ambient_access()\n"
        "class EvilRpartition(str):\n"
        "    def rpartition(self, sep):\n"
        "        return ('', '', 'harmless')\n"
        "spec = importlib.util.find_spec('_cffi_backend')\n"
        "name = EvilRpartition('_cffi_backend')\n"
        "loader = importlib.machinery.ExtensionFileLoader(name, spec.origin)\n"
        "newspec = importlib.util.spec_from_loader(name, loader, origin=spec.origin)\n"
        "try:\n"
        "    backend = _imp.create_dynamic(newspec)\n"
        "except RuntimeError as exc:\n"
        "    if str(exc) != 'INVENTORY_SHADOW_AMBIENT_ACCESS_DENIED:import':\n"
        "        sys.exit(4)\n"
        "else:\n"
        "    lib = backend.load_library(None)\n"
        "    bint = backend.new_primitive_type('int')\n"
        "    fn = lib.load_function(backend.new_function_type((), bint, False), 'getpid')\n"
        "    sys.exit(7 if fn() == os.getpid() else 8)\n"
        "print('DENIED')\n"
    )
    result = child("-c", code)
    assert result.returncode == 0, (result.returncode, result.stderr)
    assert result.stdout.strip() == "DENIED"


def test_process_entry_refuses_when_a_denied_module_is_already_loaded(tmp_path):
    path = write_fixture(tmp_path / "in")
    out = output_dir(tmp_path)
    for module in sorted(start._DENIED_IMPORT_MODULES):
        code = (
            "import sys\n"
            f"import {module}\n"
            "from polymarket_scanner.v11 import inventory_shadow_start as start\n"
            "raise SystemExit(start.guarded_main(sys.argv[1:]))\n"
        )
        result = child("-c", code, "--input", str(path), "--event", EVENT, "--output-dir", str(out))
        assert result.returncode == 2, (module, result.stderr)
        assert "DENIED_MODULE_PRELOADED" in result.stderr
        assert list(out.iterdir()) == []


def test_process_entry_refuses_when_any_other_project_module_is_loaded(tmp_path):
    path = write_fixture(tmp_path / "in")
    out = output_dir(tmp_path)
    code = (
        "import sys, types\n"
        "from polymarket_scanner.v11 import inventory_shadow_start as start\n"
        "sys.modules['polymarket_scanner.weather_only_runtime'] = types.ModuleType('coupled')\n"
        "raise SystemExit(start.guarded_main(sys.argv[1:]))\n"
    )
    result = child("-c", code, "--input", str(path), "--event", EVENT, "--output-dir", str(out))
    assert result.returncode == 2
    assert "MODULE_COUPLING_REFUSED" in result.stderr
    assert list(out.iterdir()) == []


def test_runner_imports_only_the_observer_and_stays_separate_from_weather_shadow(tmp_path):
    tree = ast.parse(Path(start.__file__).read_text())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.update([node.module] if node.module else [alias.name for alias in node.names])
    assert names == {"__future__", "argparse", "fcntl", "hashlib", "json", "os", "pathlib", "re",
                     "stat", "sys", "time", "inventory_shadow", "structural_evidence"}
    path = write_fixture(tmp_path / "in")
    before = {name for name in sys.modules if "weather" in name}
    start.run(EVENT, output_dir(tmp_path), inputs=(path,))
    assert {name for name in sys.modules if "weather" in name} == before


def test_directory_batch_replay_is_deterministic_and_idempotent(tmp_path):
    write_fixture(tmp_path / "in", "b.json")
    write_fixture(tmp_path / "in", "a.json", unknown_coverage)
    out = output_dir(tmp_path)
    first = start.run(EVENT, out, input_dir=tmp_path / "in")
    assert [item["coverage"] for item in first["artifacts"]] == ["UNKNOWN", "INCOMPLETE"]
    assert all(item["created"] for item in first["artifacts"])
    names = sorted(item["artifact"] for item in first["artifacts"])
    assert len(set(names)) == 2 and sorted(p.name for p in out.iterdir()) == names
    before = {name: ((out / name).read_bytes(), (out / name).stat().st_ino, (out / name).stat().st_mtime_ns)
              for name in names}
    second = start.run(EVENT, out, input_dir=tmp_path / "in")
    assert not any(item["created"] for item in second["artifacts"])
    assert without_created(second) == without_created(first)
    assert sorted(p.name for p in out.iterdir()) == names
    assert before == {name: ((out / name).read_bytes(), (out / name).stat().st_ino,
                             (out / name).stat().st_mtime_ns) for name in names}
    # The same bytes under another event occupy a different, equally stable identity.
    other = start.run("another-event", out, input_dir=tmp_path / "in")
    assert not set(item["artifact"] for item in other["artifacts"]) & set(names)


def test_uncertainty_propagates_and_completeness_claims_refuse(tmp_path):
    out = output_dir(tmp_path)
    incomplete = write_fixture(tmp_path / "in", "incomplete.json")
    unknown = write_fixture(tmp_path / "in", "unknown.json", unknown_coverage)
    complete = write_fixture(tmp_path / "in", "complete.json", complete_coverage)
    for path, state in ((incomplete, "INCOMPLETE"), (unknown, "UNKNOWN")):
        with pytest.raises(shadow.ShadowInputError, match="COMPLETENESS_REQUIRED_BUT_" + state):
            start.run(EVENT, out, inputs=(path,), require_complete=True)
    with pytest.raises(shadow.ShadowInputError, match="COMPLETENESS_REQUIRED_BUT_"):
        start.run(EVENT, out, inputs=(complete, unknown), require_complete=True)
    assert list(out.iterdir()) == []
    summary = start.run(EVENT, out, inputs=(incomplete, unknown))
    assert [item["coverage"] for item in summary["artifacts"]] == ["INCOMPLETE", "UNKNOWN"]
    for item in summary["artifacts"]:
        report = json.loads((out / item["artifact"]).read_text())
        assert report["coverage"] == report["reconciliation"]["coverage"] == item["coverage"]
        assert "EVENT_WINDOW_COVERAGE_UNPROVEN" in report["reconciliation"]["discrepancies"]
        assert report["qualification"] is False
    proven = start.run(EVENT, out, inputs=(complete,), require_complete=True)
    assert proven["qualification"] is proven["financial_authority"] is proven["transaction_level_proof"] is False
    report = json.loads((out / proven["artifacts"][0]["artifact"]).read_text())
    assert report["coverage"] == "COMPLETE" and report["qualification"] is False
    assert "OPENING_INVENTORY_UNKNOWN" in report["reconciliation"]["unresolved"]
    assert report["evidence_class_counts"]["CHAIN_RECEIPT"] == 0


def _set(key, item):
    def mutate(value):
        value[key] = item
    return mutate


def _drop(key):
    def mutate(value):
        del value[key]
    return mutate


def _source(key, item):
    def mutate(value):
        value["source"][key] = item
    return mutate


def _declare_complete(value):
    value["coverage"]["state"] = "COMPLETE"


def _drop_route(value):
    del value["source"]["route"]


@pytest.mark.parametrize("mutate, code", [
    (_set("evidence_class", "CHAIN_RECEIPT"), "API_OBSERVATION_CLASS_REQUIRED"),
    (_set("evidence_class", "SYNTHETIC_PROOF"), "API_OBSERVATION_CLASS_REQUIRED"),
    (_set("evidence_class", "OPERATOR_ASSERTED"), "API_OBSERVATION_CLASS_REQUIRED"),
    (_set("chain_receipts", []), "UNSUPPORTED_FIXTURE_FIELD"),
    (_set("chain_status", "CHAIN_VERIFIED"), "CHAIN_STATUS_UNSUPPORTED"),
    (_drop("coverage"), "COVERAGE_DECLARATION_REQUIRED"),
    (_declare_complete, "DECLARED_COVERAGE_MISMATCH"),
    (_source("raw_sha256", None), "LINEAGE_RAW_HASH_REQUIRED"),
    (_source("raw_sha256", "not-a-hash"), "SOURCE_INVALID"),
    (_drop_route, "SOURCE_SCHEMA"),
    (_drop("fixture_version"), "FIXTURE_VERSION_REQUIRED"),
    (_set("fixture_version", 7), "FIXTURE_VERSION_REQUIRED"),
    (_set("payload", []), "INPUT_SCHEMA"),
])
def test_malformed_or_unsupported_fixture_refuses_whole_batch_without_output(tmp_path, mutate, code):
    write_fixture(tmp_path / "in", "a.json")
    write_fixture(tmp_path / "in", "b.json", mutate)
    out = output_dir(tmp_path)
    with pytest.raises(shadow.ShadowInputError, match=code):
        start.run(EVENT, out, input_dir=tmp_path / "in")
    assert list(out.iterdir()) == []


def test_symlink_nonregular_and_resource_cap_inputs_refuse(tmp_path, monkeypatch):
    out = output_dir(tmp_path)
    good = write_fixture(tmp_path / "good")
    linked = tmp_path / "linked.json"
    linked.symlink_to(good)
    with pytest.raises(shadow.ShadowInputError, match="INPUT_NOT_REGULAR_FILE"):
        start.run(EVENT, out, inputs=(linked,))
    with pytest.raises(shadow.ShadowInputError, match="INPUT_NOT_REGULAR_FILE"):
        start.run(EVENT, out, inputs=(tmp_path / "missing.json",))
    with pytest.raises(shadow.ShadowInputError, match="INPUT_BYTE_LIMIT"):
        start.run(EVENT, out, inputs=(good,), limits=Limits(max_bytes=10))
    with pytest.raises(shadow.ShadowInputError, match="BATCH_INPUT_LIMIT"):
        start.run(EVENT, out, inputs=(good,) * (start.MAX_BATCH_INPUTS + 1))
    with pytest.raises(shadow.ShadowInputError, match="EMPTY_BATCH"):
        start.run(EVENT, out)
    with pytest.raises(shadow.ShadowInputError, match="INPUT_SELECTION_AMBIGUOUS"):
        start.run(EVENT, out, inputs=(good,), input_dir=tmp_path / "good")

    directory_link = tmp_path / "good-link"
    directory_link.symlink_to(tmp_path / "good", target_is_directory=True)
    with pytest.raises(shadow.ShadowInputError, match="INPUT_DIR_REFUSED"):
        start.run(EVENT, out, input_dir=directory_link)
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(shadow.ShadowInputError, match="EMPTY_BATCH"):
        start.run(EVENT, out, input_dir=empty)

    def entry_symlink(directory):
        (directory / "z.json").symlink_to(good)

    def entry_fifo(directory):
        os.mkfifo(directory / "z.json")

    def entry_subdirectory(directory):
        (directory / "z.json").mkdir()

    def entry_other_suffix(directory):
        (directory / "notes.txt").write_text("{}")

    def entry_hidden(directory):
        (directory / ".hidden.json").write_text("{}")

    for index, plant in enumerate((entry_symlink, entry_fifo, entry_subdirectory,
                                   entry_other_suffix, entry_hidden)):
        directory = tmp_path / f"batch{index}"
        write_fixture(directory)
        plant(directory)
        with pytest.raises(shadow.ShadowInputError, match="INPUT_DIR_ENTRY_REFUSED"):
            start.run(EVENT, out, input_dir=directory)

    crowded = tmp_path / "crowded"
    crowded.mkdir()
    for index in range(start.MAX_BATCH_INPUTS + 1):
        (crowded / f"f{index:02d}.json").write_text("{}")
    with pytest.raises(shadow.ShadowInputError, match="BATCH_INPUT_LIMIT"):
        start.run(EVENT, out, input_dir=crowded)

    monkeypatch.setattr(start, "MAX_RUN_SECONDS", -1.0)
    with pytest.raises(shadow.ShadowInputError, match="RUN_TIME_LIMIT"):
        start.run(EVENT, out, inputs=(good,))
    assert list(out.iterdir()) == []


def test_stale_tampered_or_foreign_output_refuses_start_and_is_never_replaced(tmp_path):
    path = write_fixture(tmp_path / "in")
    out = output_dir(tmp_path)
    name = start.run(EVENT, out, inputs=(path,))["artifacts"][0]["artifact"]
    artifact = out / name
    original = artifact.read_bytes()
    report = json.loads(original)

    def refused(code):
        with pytest.raises(shadow.ShadowInputError, match=code):
            start.run(EVENT, out, inputs=(path,))

    def encoded(value):
        return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("ascii")

    artifact.write_bytes(original + b" ")
    refused("OUTPUT_TAMPERED_OUTPUT_CONFLICT")
    assert artifact.read_bytes() == original + b" "
    artifact.write_bytes(encoded({**report, "qualification": True}))
    refused("OUTPUT_TAMPERED_ARTIFACT_POLICY_REFUSED")
    artifact.write_bytes(encoded({**report, "event_slug": "another-event"}))
    refused("OUTPUT_IDENTITY_MISMATCH")
    artifact.write_bytes(encoded({**report, "metrics": {**report["metrics"], "observed_purchases": 12}}))
    refused("OUTPUT_TAMPERED_ARTIFACT_ID_MISMATCH")
    artifact.write_bytes(encoded({**report, "version": "v11_inventory_transform_offline_shadow_v0"}))
    refused("OUTPUT_STALE_VERSION")
    assert json.loads(artifact.read_text())["version"].endswith("_v0")
    artifact.write_bytes(b"{")
    refused("OUTPUT_TAMPERED_INVALID_JSON")
    artifact.unlink()

    keep = tmp_path / "keep.json"
    keep.write_bytes(original)
    artifact.symlink_to(keep)
    refused("OUTPUT_NOT_REGULAR")
    artifact.unlink()
    renamed = out / ("inventory-shadow-" + "0" * 64 + ".json")
    renamed.write_bytes(original)
    refused("OUTPUT_IDENTITY_MISMATCH")
    renamed.unlink()
    (out / ".inventory-shadow-0123456789abcdef01234567").write_bytes(original)
    refused("OUTPUT_STALE_TEMPORARY")
    (out / ".inventory-shadow-0123456789abcdef01234567").unlink()
    (out / "weather-shadow.json").write_text("{}")
    refused("OUTPUT_DIR_FOREIGN_ENTRY")
    (out / "weather-shadow.json").unlink()
    assert list(out.iterdir()) == []

    artifact.write_bytes(original)
    replay = start.run(EVENT, out, inputs=(path,))
    assert replay["artifacts"][0]["created"] is False
    assert artifact.read_bytes() == original


def test_output_directory_identity_refusals(tmp_path, monkeypatch):
    path = write_fixture(tmp_path / "in")
    out = output_dir(tmp_path)

    def refused(directory, code):
        with pytest.raises(shadow.ShadowInputError, match=code):
            start.run(EVENT, directory, inputs=(path,))

    refused(tmp_path / "absent", "OUTPUT_DIR_REFUSED")
    refused(path, "OUTPUT_DIR_REFUSED")
    linked = tmp_path / "out-link"
    linked.symlink_to(out, target_is_directory=True)
    refused(linked, "OUTPUT_DIR_REFUSED")
    out.chmod(0o775)
    refused(out, "OUTPUT_DIR_NOT_PRIVATE")
    out.chmod(0o700)
    holder = os.open(out, os.O_RDONLY)
    try:
        fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
        refused(out, "OUTPUT_DIR_BUSY")
    finally:
        os.close(holder)
    monkeypatch.setattr(start.os, "geteuid", lambda: 0)
    refused(out, "ROOT_REFUSED")
    monkeypatch.undo()
    assert list(out.iterdir()) == []
    assert start.run(EVENT, out, inputs=(path,))["artifacts"][0]["created"] is True


def test_output_directory_rename_after_validation_cannot_redirect_the_write(tmp_path, monkeypatch):
    # Probe: a same-uid racer renames the already-validated, already-locked
    # output directory aside and substitutes a new, world-writable directory
    # at the same path between validation and write. The artifact must still
    # land in the original, locked directory (reached through its open fd),
    # never in the substitute, regardless of what the racer leaves there.
    path = write_fixture(tmp_path / "in")
    out = output_dir(tmp_path)
    real_artifact_name = start.artifact_name
    moved_aside = tmp_path / "out-moved-aside"
    substitute = tmp_path / "out"
    swapped = []

    def swap_then_name(*args, **kwargs):
        if not swapped:
            swapped.append(True)
            out.rename(moved_aside)
            substitute.mkdir(mode=0o777)
            (substitute / "attacker-marker.txt").write_text("ATTACKER_OWNED_DIRECTORY")
        return real_artifact_name(*args, **kwargs)

    monkeypatch.setattr(start, "artifact_name", swap_then_name)
    summary = start.run(EVENT, out, inputs=(path,))
    name = summary["artifacts"][0]["artifact"]
    assert summary["artifacts"][0]["created"] is True
    report = json.loads((moved_aside / name).read_text())
    assert report["observation_id"] == summary["artifacts"][0]["observation_id"]
    assert [p.name for p in moved_aside.iterdir()] == [name]
    assert [p.name for p in substitute.iterdir()] == ["attacker-marker.txt"]


def test_cli_refusal_exits_two_without_output(tmp_path, capsys):
    out = output_dir(tmp_path)
    path = write_fixture(tmp_path / "in", mutate=_set("evidence_class", "CHAIN_RECEIPT"))
    with pytest.raises(SystemExit) as exc:
        start.main(["--input", str(path), "--event", EVENT, "--output-dir", str(out)])
    assert exc.value.code == 2
    assert "inventory shadow start refused: API_OBSERVATION_CLASS_REQUIRED" in capsys.readouterr().err
    with pytest.raises(SystemExit) as exc:
        start.main(["--input", str(path), "--input-dir", str(tmp_path / "in"),
                    "--event", EVENT, "--output-dir", str(out)])
    assert exc.value.code == 2
    assert list(out.iterdir()) == []
