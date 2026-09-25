# PAPER account effects replay evidence — 2026-09-25

Scope: shared original conditional allocation/transition/recovery/fill/terminal
numerical effects and bounded scheduled candidate audits. All new runtime/source/
fill fixtures are SYNTHETIC. No current admission, account commands, external
orders or financial authority are invoked by replay. Preparation/control decisions
remain archived conditional inputs, not full engine or independent acceptance.

Recovery: clean local/remote 7a8fc1127b2d57e5c6713e0ec9f87ece6e0a33f4, tree
0b0d0aa78ea7636def0c0e62c9a5006e8cf06f79. All 760 non-document inputs matched
prior saved regression; no operation/lock was active. V10 preserved/DEFERRED.
Master source SHA-256 a0e16d9bd7344c943a54a16a53c6757662363d93642f6e5cb7953cd047659b4a.

## Development checks (unfinished working tree, not release acceptance)

| Log | Session | Result | Raw SHA-256 |
|---|---:|---|---|
| account-effects-dev-20260925-01.log | 34868 | 69 passed / 8.45 s / exit 0 | 51251d9c89572ead1d14628760142370caec20cab86523489c2b206a79b0117f |
| account-effects-dev-20260925-02.log | 83182 | 1 failed, 27 passed / 1.89 s / exit 1; test used nonexistent safety API | fc4daeedb48c36d3c7f10eb276bdbc97f3795d724b1212421eef991094d01530 |
| account-effects-dev-20260925-03.log | 51749 | 58 passed / 7.61 s / exit 0 | bcd1fffec89d5c81f8aa4f3c00d5adfe7c7069c8ce45ce8c6f0dfc1b6d91dbe4 |
| account-effects-dev-20260925-04.log | 37605 | 1 failed, 80 passed / 10.81 s / exit 1; test compared Decimal text rather than value | d6f10cc45e95cc52c8f844cd99896656d63648d7969cf82482cd724f09d937c8 |
| account-effects-dev-20260925-05.log | 42335 | 82 passed / 10.84 s / exit 0 | ad9b303220e1a6b34e7a6f46c1dcb58d66bcb8876b2ca4cd9ccf39bc7f39d83e |

Raw logs are retained in `/workspace/scratch/38af7099c566/v11-test-evidence/`.
Commands: 01 ran `test_v11_paper_coordinator.py`, `test_v11_basket_coordinator.py`,
`test_v11_position_management.py`; 02 ran new `test_v11_account_replay.py`;
03 added `test_v11_account_replay_integration.py` and `test_v11_causal_replay.py`;
04/05 added existing `test_v11_paper_coordinator.py` and the final clock/receipt
cases. Each used the existing venv Python `-m pytest -q` with output captured.

Both failures were corrected in tests; no production guard was relaxed. The
legacy full 4104-pass result does not describe this changed tree. Affected and
locked full verification on the saved implementation follow this checkpoint.
Progress remains 83/200 (~42%), formal 1/50 (2%). NOT_READY_TO_FUND.
