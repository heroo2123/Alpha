from pathlib import Path
from tempfile import TemporaryDirectory
from decimal import Decimal, localcontext
import json

from polymarket_scanner.v11 import structural_evidence as ev
from polymarket_scanner.v11.neg_risk_contract import NegRiskTopologyProof


def source(parameters=(("start", "0"), ("end", "10"))):
    return ev.Source("probe", "v2", "/v2/activity", parameters, None,
                     "UNKNOWN", 0, 10, "a" * 64)


for yes, no in ((("same", "y1"), ("same", "n1")),
                (("y0", "n1"), ("n0", "n1"))):
    try:
        NegRiskTopologyProof(("c0", "c1"), yes, no, "usd", "rules")
    except ValueError as error:
        print("TOKEN_ALIAS_REJECTED", str(error))
    else:
        raise AssertionError("token alias accepted")
try:
    NegRiskTopologyProof(("c0", "c1"), ("y0", "y1"), ["n0", "n1"], "usd", "rules")
except ValueError as error:
    print("MUTABLE_TOPOLOGY_REJECTED", str(error))
else:
    raise AssertionError("mutable topology accepted")

row = {"timestamp": 5, "type": "TRADE", "size": "1", "usdc_size": "1e100",
       "price": "1", "condition_id": "c", "transaction_hash": "h",
       "event_slug": "e", "side": "BUY"}
bounded = ev.normalize_activity({"data": [row], "pagination": {"has_more": False}}, source())
assert bounded.coverage is not ev.Coverage.COMPLETE and not bounded.rows
print("EXTREME_DECIMAL_REJECTED", bounded.coverage.value, bounded.discrepancies)


for pagination in (
    {"has_more": False, "next_cursor": 0},
    {"has_more": False, "next_cursor": False},
    {"has_more": False, "offset": None, "next_cursor": None},
):
    result = ev.normalize_activity({"data": [], "pagination": pagination}, source())
    print("PAGINATION", repr(pagination), result.coverage.value, result.discrepancies)

for parameters in (
    (("start", "0"), ("end", "10"), ("cursor", "later"), ("cursor", "")),
    (("start", "0"), ("end", "10"), ("offset", "1000"), ("offset", "0")),
):
    result = ev.normalize_activity({"data": [], "pagination": {"has_more": False}}, source(parameters))
    print("DUPLICATE_PARAMETERS", repr(parameters), result.coverage.value, result.discrepancies)

with TemporaryDirectory(prefix="alpha-v11-it-review-probe-") as temp:
    root = Path(temp)
    real = root / "real"
    sub = real / "sub"
    sub.mkdir(parents=True)
    (real / "page.json").write_text('{"probe": "followed parent symlink"}')
    (root / "linked").symlink_to(sub, target_is_directory=True)
    path = root / "linked" / ".." / "page.json"
    result = ev.load_offline_json(path)
    print("SYMLINK_DOTDOT", repr(str(path)), result.payload, result.discrepancy)

    checked = root / "checked.json"
    other = root / "other.json"
    checked.write_text('{"probe": "checked bytes"}')
    other.write_text('{"probe": "swapped bytes"}')
    original_open = Path.open
    def swap_then_open(self, *args, **kwargs):
        if self == checked:
            checked.unlink()
            checked.symlink_to(other)
        return original_open(self, *args, **kwargs)
    Path.open = swap_then_open
    try:
        raced = ev.load_offline_json(checked)
    finally:
        Path.open = original_open
    print("OPEN_SWAP", raced.payload, raced.discrepancy)

    stable = root / "stable.json"
    replacement = root / "replacement.json"
    stable.write_text('{"probe": "original inode"}')
    replacement.write_text('{"probe": "replacement inode"}')
    original_open = Path.open
    def replace_then_open(self, *args, **kwargs):
        if self == stable:
            replacement.replace(stable)
        return original_open(self, *args, **kwargs)
    Path.open = replace_then_open
    try:
        replaced = ev.load_offline_json(stable)
    finally:
        Path.open = original_open
    print("INODE_SWAP", replaced.payload, replaced.discrepancy)

    numeric = root / "numeric.json"
    numeric.write_text('{"v": 0.' + '1' * 100000 + '}')
    result = ev.load_offline_json(numeric)
    print("LONG_NUMERIC", type(result.payload["v"]).__name__ if result.payload else None,
          len(result.payload["v"].as_tuple().digits) if result.payload else None,
          result.discrepancy)
    precise = root / "precise.json"
    precise.write_text('{"v":0.1234567890123456789}')
    result = ev.load_offline_json(precise)
    assert result.payload["v"] == Decimal("0.1234567890123456789")
    print("JSON_LEXEME_PRESERVED", str(result.payload["v"]))
    deep = root / "deep.json"
    deep.write_text('[' * 10000 + '0' + ']' * 10000)
    result = ev.load_offline_json(deep)
    print("DEEP_JSON", result.coverage.value, result.discrepancy, bool(result.raw_sha256))
