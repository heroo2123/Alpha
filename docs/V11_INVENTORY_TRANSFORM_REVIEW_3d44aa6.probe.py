"""Independent bounded offline assertions for exact inventory repair 3d44aa6."""
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

attempts = []
def deny_socket(event, args):
    if event.startswith('socket.'):
        attempts.append(event)
        raise RuntimeError('NETWORK_DENIED:' + event)
sys.addaudithook(deny_socket)

from polymarket_scanner.v11 import structural_evidence as ev
from polymarket_scanner.v11.neg_risk_contract import NegRiskTopologyProof

base = (("start", "0"), ("end", "10"))
def source(parameters=base, page_number=0):
    return ev.Source("independent", "v2", "/v2/activity", parameters,
                     None, "UNKNOWN", 0, 10, "a" * 64, page_number)
def normalized(parameters=base, pagination=None, page_number=0, positions=False):
    payload = {"data": [], "pagination": pagination if pagination is not None else
               {"has_more": False, "next_cursor": None}}
    fn = ev.normalize_positions if positions else ev.normalize_activity
    return fn(payload, source(parameters, page_number))

for positions in (False, True):
    good = normalized(positions=positions)
    assert good.coverage is ev.Coverage.COMPLETE, good
    empty = normalized(base + (("cursor", ""),), positions=positions)
    assert empty.coverage is ev.Coverage.UNKNOWN, empty
    assert "WINDOW_OR_PRIOR_PAGES_UNVERIFIED" in empty.discrepancies, empty
    for params in (base + (("cursor", "opaque"),),
                   base + (("cursor", "opaque"), ("cursor", "")),
                   base + (("offset", "1000"), ("offset", "0")),
                   base + (("offset", "1"),)):
        result = normalized(params, positions=positions)
        assert result.coverage is not ev.Coverage.COMPLETE, result
    for cursor in (0, False, ""):
        result = normalized(pagination={"has_more": False, "next_cursor": cursor}, positions=positions)
        assert result.coverage is ev.Coverage.UNKNOWN and "PAGINATION_UNKNOWN" in result.discrepancies, result
    explicit_null = normalized(pagination={"has_more": False, "offset": None}, positions=positions)
    assert explicit_null.coverage is not ev.Coverage.COMPLETE, explicit_null
    later = normalized(page_number=1, positions=positions)
    assert later.coverage is not ev.Coverage.COMPLETE, later
    continuing = normalized(pagination={"has_more": True, "next_cursor": "more"}, positions=positions)
    assert continuing.coverage is ev.Coverage.INCOMPLETE, continuing
print("PAGINATION_ACTIVITY_AND_POSITIONS_REFUSALS_AND_CONTROLS_OK")

with tempfile.TemporaryDirectory(prefix="alpha-v11-it-3d44aa6-probe-", dir="/tmp") as tmp:
    root = Path(tmp)
    fifo = root / "wait.json"
    os.mkfifo(fifo)
    child = '''import sys,time\nsys.addaudithook(lambda event,args: (_ for _ in ()).throw(RuntimeError("NETWORK_DENIED:"+event)) if event.startswith("socket.") else None)\nfrom pathlib import Path\nfrom polymarket_scanner.v11.structural_evidence import load_offline_json,Limits,Coverage\nt=time.monotonic(); r=load_offline_json(Path(sys.argv[1]),Limits(max_seconds=0.1)); dt=time.monotonic()-t\nassert r.payload is None and r.coverage is Coverage.UNKNOWN and r.discrepancy=="NOT_REGULAR_FILE",r\nassert dt < 0.5,dt\nprint("FIFO_REJECTED",round(dt,5))'''
    start = time.monotonic()
    completed = subprocess.run([sys.executable, "-c", child, str(fifo)],
                               capture_output=True, text=True, timeout=1.5, check=True)
    assert time.monotonic() - start < 1.5
    print(completed.stdout.strip())

    normal = root / "normal.json"
    raw = b'{"data":[],"ok":true}'
    normal.write_bytes(raw)
    loaded = ev.load_offline_json(normal)
    assert loaded.payload == {"data": [], "ok": True}, loaded
    assert loaded.raw_sha256 == hashlib.sha256(raw).hexdigest(), loaded
    assert ev.load_offline_json(normal, ev.Limits(max_bytes=5)).discrepancy == "BYTE_LIMIT"
    print("REGULAR_READ_HASH_AND_BYTE_BOUND_OK")

    linked = root / "linked.json"
    linked.symlink_to(normal)
    assert ev.load_offline_json(linked).discrepancy == "NOT_REGULAR_FILE"
    directory = root / "directory"
    directory.mkdir()
    (directory / "page.json").write_bytes(raw)
    parent_link = root / "parent_link"
    parent_link.symlink_to(directory, target_is_directory=True)
    assert ev.load_offline_json(parent_link / "page.json").discrepancy == "NOT_REGULAR_FILE"
    assert ev.load_offline_json(parent_link / ".." / "normal.json").discrepancy == "NOT_REGULAR_FILE"

    checked = root / "checked.json"
    checked.write_text('{"probe":"checked"}')
    other = root / "other.json"
    other.write_text('{"probe":"other"}')
    real_open = ev.os.open
    triggered = False
    def swap_final(path, flags, *args, **kwargs):
        global triggered
        if not triggered and path == "checked.json":
            triggered = True
            checked.unlink()
            checked.symlink_to(other)
        return real_open(path, flags, *args, **kwargs)
    ev.os.open = swap_final
    try:
        result = ev.load_offline_json(checked)
    finally:
        ev.os.open = real_open
    assert triggered and result.discrepancy == "NOT_REGULAR_FILE" and result.payload is None, result

    inner = root / "inner"
    inner.mkdir()
    (inner / "page.json").write_bytes(raw)
    triggered = False
    def swap_parent(path, flags, *args, **kwargs):
        global triggered
        if not triggered and path == "inner":
            triggered = True
            inner.rename(root / "old_inner")
            inner.symlink_to(directory, target_is_directory=True)
        return real_open(path, flags, *args, **kwargs)
    ev.os.open = swap_parent
    try:
        result = ev.load_offline_json(inner / "page.json")
    finally:
        ev.os.open = real_open
    assert triggered and result.discrepancy == "NOT_REGULAR_FILE" and result.payload is None, result
    print("ALL_COMPONENT_NOFOLLOW_AND_OPEN_TIME_SYMLINK_SWAPS_OK")

    regular = root / "regular.json"
    replacement = root / "replacement.json"
    regular.write_text('{"probe":"old"}')
    replacement_bytes = b'{"probe":"new"}'
    replacement.write_bytes(replacement_bytes)
    triggered = False
    def swap_regular(path, flags, *args, **kwargs):
        global triggered
        if not triggered and path == "regular.json":
            triggered = True
            replacement.replace(regular)
        return real_open(path, flags, *args, **kwargs)
    ev.os.open = swap_regular
    try:
        result = ev.load_offline_json(regular)
    finally:
        ev.os.open = real_open
    assert triggered and result.payload == {"probe": "new"}, result
    assert result.raw_sha256 == hashlib.sha256(replacement_bytes).hexdigest(), result
    print("OPENED_REGULAR_INODE_BYTES_AND_HASH_OK_PREOPEN_IDENTITY_UNPROVEN")

    deep = root / "deep.json"
    deep.write_text("[" * 10000 + "0" + "]" * 10000)
    result = ev.load_offline_json(deep)
    assert result.payload is None and result.discrepancy == "INVALID_JSON" and result.raw_sha256, result
    print("DEEP_JSON_REFUSAL_OK")

    real_monotonic = ev.time.monotonic
    ticks = iter((0.0, 1.0))
    ev.time.monotonic = lambda: next(ticks)
    try:
        timed = ev.load_offline_json(normal, ev.Limits(max_seconds=0.1))
    finally:
        ev.time.monotonic = real_monotonic
    assert timed.payload is None and timed.discrepancy == "TIME_LIMIT", timed
    record_limited = ev.normalize_activity({"data": [{}, {}], "pagination": {"has_more": False}},
                                          source(), ev.Limits(max_records=1))
    assert record_limited.coverage is ev.Coverage.INCOMPLETE and "RECORD_LIMIT" in record_limited.discrepancies
    print("DEADLINE_AND_RECORD_BOUND_OK")

for yes, no in ((("x", "y"), ("x", "z")), (("a", "b"), ("c", "b"))):
    try:
        NegRiskTopologyProof(("c0", "c1"), yes, no, "usd", "rule")
    except ValueError:
        pass
    else:
        raise AssertionError("token alias accepted")
print("TOKEN_ALIAS_REFUSAL_OK")
print("NETWORK_ATTEMPTS=" + str(len(attempts)))
