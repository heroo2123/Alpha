# V5 feasibility baseline replay handoff — 2026-10-03

Scope: offline reproduction of the already reviewed feasibility arithmetic only.
The companion report and original dossier retain their exact reviewed bytes.
The dossier's older `--write` description is superseded: this checker now has
read-only `--check` and `--replay-baseline` modes.

`--replay-baseline` reads the fixed commit
`e73fb9b66c0cf2591c0b8017925c764aac6cff1e`. Its raw commit object names
tree `8afe7a24c6e437b0b6345a1fadc08e136797a2c1`, which matches the report.
The checker verifies the raw Git SHA-1 digest of that commit, each traversed
tree and each source blob, then recomputes the existing 22 SHA-256 source pins,
32 arithmetic checks and the complete report bytes. It strips Git environment
overrides and disables replacement refs. Git lazy fetching is disabled and every
transport protocol is denied; object reads have a five-second deadline and an
8 MiB bound on each output stream. Timed-out or oversized reads kill and reap
the child process group. Wrong commit/tree, missing or substituted objects,
non-regular source entries and any report byte change refuse. The report is
read as bounded raw bytes, with a 1 MiB limit. There is no CLI
option to select another baseline or rewrite the report.

`--check` reads each current source as a bounded regular file and checks its
exact baseline-pinned length and SHA-256 before parsing or evaluating arithmetic.
It then verifies the historical report's exact bytes.
It refuses on this merged branch because the three status documents advanced.
The historical replay passes; a current-source refusal must not be interpreted
as arithmetic drift or as permission to update the reviewed pins. Baseline replay
does not certify current sources or provide operational authority.

Reproduce with:

```
python3 -B tools/v11_gate3_v5_feasibility_arithmetic.py --replay-baseline
python3 -O -B tools/v11_gate3_v5_feasibility_arithmetic.py --replay-baseline
python3 -B -m unittest tests.test_v11_gate3_v5_feasibility_replay
python3 -O -B -m unittest tests.test_v11_gate3_v5_feasibility_replay
git diff --check
```

The focused tests cover status-only advancement, non-status source and report
tampering in current-source verification, CRLF and CR-only report tampering,
wrong baseline identity, missing/promisor objects, forged and oversized object
bytes, FIFO stalling, child process cleanup, and hostile Git environment
overrides. The local Git executable and repository remain inputs; the replay
therefore refuses missing or corrupt objects rather than repairing them.
This remains a SAFE NONFINANCIAL, V5 `PROPOSED_BLOCKED` finding with G3-L
`NO-GO` and zero qualification credit. No provider facts, execution, host
qualification, V5 implementation or SHADOW authority follows. A different-model
exact review of the clean candidate commit/tree is required before integration;
the author must
not self-approve or merge.
