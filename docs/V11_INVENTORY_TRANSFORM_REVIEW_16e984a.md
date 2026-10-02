# Independent exact review: inventory IT-R1/IT-R4 repair

**Verdict: CHANGES_REQUIRED.** This is an offline research candidate, with no
provider, account, receipt, financial, G3-L or deployment authority.

The sole Sonnet/high author completed clean commit
`16e984a63156f12252e2811f84b6106b1f84fac7`, tree
`18bce4af93da8b7e2996d9f794982182ef875056`, parent `f6c7c90`.
The outer terminal reports exit 0, matching head/tree and clean status;
the changed source/test SHA-256 values match the author record. The exact
diff modifies only `polymarket_scanner/v11/structural_evidence.py` and
`tests/test_v11_structural_evidence.py` (198 insertions, 24 deletions).
`git diff --check f6c7c90 16e984a` passes. The reviewer is a separate
Sol/high coordinator session; no author file was edited.

The repair closes the prior review's specific malformed response cursor,
duplicate request parameter, explicit null offset, and open-time symlink
cases in the tested scope. It leaves two material gaps:

1. **P2, IT-R1: an explicit empty request cursor still proves a first page.**
   `_finalize_coverage` uses `not request_cursor`, so a source with one
   `("cursor", "")` pair, valid window parameters, `page_number=0`, and a
   terminal response becomes `COMPLETE` with no discrepancy. `Source` permits
   that empty value. It is an ambiguous request page identity, so the loader
   cannot claim whole-window completeness. The retained probe reproduces it.
   Require absence of the cursor key, or establish an explicit reviewed
   provider contract for an empty cursor before treating it as first page.

2. **P2, IT-R4: the new open can block beyond the loader deadline.**
   `_open_regular_nofollow` opens the final component with `O_RDONLY` before
   `fstat` establishes that it is regular. A named pipe therefore blocks in
   `os.open` waiting for a writer; `Limits(max_seconds=0.1)` cannot take
   effect. The retained probe creates only a temporary FIFO and kills the
   child after one second. Open-time no-follow is useful, but the final open
   also needs a nonblocking fail-closed path for nonregular files. Preserve
   the existing symlink and byte-limit checks.

Independent verification in the clean candidate worktree used the retained
`/tmp/alpha-v11-inventory-transform-review-f6c7c90-test-runner.py` with an
audit hook denying all socket events, an empty inherited environment,
disabled plugin autoload/cacheprovider/bytecode, and fresh `/tmp` basetemps:

```text
tests/test_v11_structural_evidence.py tests/test_v11_neg_risk_contract.py
  28 passed in 1.54s; NETWORK_ATTEMPTS=0
tests/test_v11_scenario_risk.py tests/test_v11_evidence_foundation.py
  43 passed in 3.53s; NETWORK_ATTEMPTS=0
```

Run `docs/V11_INVENTORY_TRANSFORM_REVIEW_16e984a.probe.py` from the exact
candidate checkout with `PYTHONPATH` pointing there. Both adverse cases
reproduce. No broad release suite or provider request was performed.
The candidate remains unmerged pending repair, another exact different-model
review, and newer-main reconciliation. G3-L remains NO-GO; no C/J/E/A
boundary crosses.
