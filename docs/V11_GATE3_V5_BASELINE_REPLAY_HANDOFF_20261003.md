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
overrides and disables replacement refs. Wrong commit/tree, missing or substituted
objects, non-regular source entries and report changes refuse. There is no CLI
option to select another baseline or rewrite the report.

`--check` still compares all current source bytes to the same reviewed report.
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
tampering in current-source verification, report tampering in historical replay,
wrong baseline identity, missing objects, forged object bytes and hostile Git
environment overrides. This remains a SAFE NONFINANCIAL, `PROPOSED_BLOCKED`
finding. No provider facts, execution, host qualification, G3-L credit, V5
implementation or SHADOW authority follows. A different-model exact review of
the clean candidate commit/tree is required before integration; the author must
not self-approve or merge.
