# FC1 step-2 IA1 repair — author verification

All commands ran in the isolated worktree. The final FC1 runs followed the last
implementation edit. The IA1 document checker and inherited regressions ran
before a final pure FC1 stream-debit edit; their document and inherited source
files did not change. These are offline author checks, not independent review
or qualification.

| Command | Result |
| --- | --- |
| `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p 'test_v11_gate3_v5_fc1.py'` | 21 passed; 255.170 s |
| `PYTHONDONTWRITEBYTECODE=1 python3 -O -m unittest discover -s tests -p 'test_v11_gate3_v5_fc1.py'` | 21 passed; 255.411 s |
| `PYTHONDONTWRITEBYTECODE=1 python3 tests/verify_v11_fc1_ia1_documents.py` | 10 passed; 0.729 s |
| `PYTHONDONTWRITEBYTECODE=1 python3 -O tests/verify_v11_fc1_ia1_documents.py` | 10 passed; 0.744 s |

The inherited regression command was run in both modes with
`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` and the existing development venv:

```sh
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 /home/alphaadmin/AlphaV11_Dev/venv/bin/python -m pytest -q -p no:cacheprovider tests/test_v11_r09_gate3_store_v1.py tests/test_v11_r09_gate3_launch_v4.py tests/test_v11_gate3_v5_feasibility_replay.py tests/test_v11_r09_gate3_runtime.py tests/test_v11_r09_gate3_ledgers.py tests/test_v11_r09_gate3_h1h6.py tests/test_v11_r09_gate3_launch.py tests/test_v11_r09_gate3_restart_composition.py
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 /home/alphaadmin/AlphaV11_Dev/venv/bin/python -O -m pytest -q -p no:cacheprovider tests/test_v11_r09_gate3_store_v1.py tests/test_v11_r09_gate3_launch_v4.py tests/test_v11_gate3_v5_feasibility_replay.py tests/test_v11_r09_gate3_runtime.py tests/test_v11_r09_gate3_ledgers.py tests/test_v11_r09_gate3_h1h6.py tests/test_v11_r09_gate3_launch.py tests/test_v11_r09_gate3_restart_composition.py
```

Normal: 447 passed in 102.01 s; two inherited `os.fork()` deprecation warnings.
Optimized: 447 passed in 105.87 s; the same two warnings plus pytest's
expected warning about non-rewritten assertions under `-O`.

The IA1 checker used system `python3`, because the development venv lacks
`jsonschema`. The corrected stream debit was covered by a final direct focused
run of two tests (2 passed), followed by the final complete 21-test runs above.
Earlier author runs exposed a timing test stage mismatch and a mutation-helper
rewrite of reviewed pretty-printed JSON; both were corrected before the final
runs. `git diff --check` passed before commit.

Recomputed synthetic witness: 2,713 requests; 711,196,672 reserved body bytes;
711,262,208 including the single abort allowance; 9,586,000 ms conditional
IA1 elapsed bound and 1,214,000 ms arithmetic slack. `A=135,650 ms` is
charged twice within 590,000 ms jitter. The same parameters under inherited
close-plus-spacing give 15,010,000 ms and refuse. The fixture now has 2,761
final and 2,762 peak physical objects, including seven exact public contract/
IA1 document pins. Journal bounds are budget 10,856 events / 11,116,544 B,
session 32,560 / 66,682,880 B, denial 10,856 / 44,466,176 B, and store
8,287 / 64,844,800 B. All are structural synthetic arithmetic, with zero
qualification credit.

Residual limitations: no enrolled store/fsync durability, original clock or
actual transport-start producer, counted streaming adapter or framing proof,
successful native decode cost, real resource measurement, external custody, or
accepted export. Supplied trace fields and synthetic acknowledgement sets do
not authenticate themselves. The inherited V1/V4/V5 refusals and all safety
gates remain in force. No provider request or account action was made.
