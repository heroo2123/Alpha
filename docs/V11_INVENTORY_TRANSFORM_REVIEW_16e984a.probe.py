"""Independent, offline adverse probes for exact candidate 16e984a.

Run from the candidate checkout with PYTHONPATH=. . This creates only a
temporary FIFO and kills the bounded child if the loader blocks.
"""
from pathlib import Path
import os
import subprocess
import sys
import tempfile

from polymarket_scanner.v11.structural_evidence import Coverage, Source, normalize_activity


source = Source("probe", "v2", "/v2/activity",
                (("start", "0"), ("end", "10"), ("cursor", "")),
                None, "UNKNOWN", 0, 10, "a" * 64, 0)
page = {"data": [], "pagination": {"has_more": False, "next_cursor": None}}
result = normalize_activity(page, source)
assert result.coverage is Coverage.COMPLETE and not result.discrepancies
print("EMPTY_REQUEST_CURSOR_FALSE_COMPLETE_REPRODUCED")

with tempfile.TemporaryDirectory(prefix="alpha-v11-it-16e984a-") as root:
    fifo = Path(root) / "input.json"
    os.mkfifo(fifo)
    child = (
        "import sys; from pathlib import Path; "
        "from polymarket_scanner.v11.structural_evidence import load_offline_json, Limits; "
        "print(load_offline_json(Path(sys.argv[1]), Limits(max_seconds=0.1)))"
    )
    try:
        subprocess.run([sys.executable, "-c", child, str(fifo)],
                       timeout=1, check=True, capture_output=True, text=True)
    except subprocess.TimeoutExpired:
        print("FIFO_OPEN_BLOCKS_BEYOND_0_1_SECOND_LIMIT_REPRODUCED")
    else:
        raise AssertionError("FIFO block was not reproduced")
