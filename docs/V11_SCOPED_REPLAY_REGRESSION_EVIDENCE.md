# Scoped historical replay regression evidence

All checks are off-host synthetic/local implementation evidence. They do not
establish actual source truth, empirical PWS advantage, calibration, historical
executable identity, independent acceptance, host isolation or funding readiness.
V10 remains unchanged, maintenance deferred, NOT_READY_TO_FUND.

## PWS integration baseline

Published `f0335ede51526670ab65c3f41a7c5155e5c2ead8`, tree
`be4f98bb3fbf160011b89bc393132c304f541b1d`: **380 affected checks passed in 57.74 s,
exit 0**, session 65960. All **851 tracked inputs unchanged**. The fixed input
map is reproducible from this immutable saved tree; its digest is below. Local
JSON also retains the complete map. No PWS-only full run was started: source-
release integration followed, so one combined full run will cover both changes.

Focused PWS replay: **15 passed / 5.15 s**, session 99572. Initial combined
fixture run: **45 passed / 31 setup errors / 9.15 s**, session 76194. Raw-source
references added to the synthetic QC fixture also required its existing metadata
contract. The fixture was completed; production lineage checks stayed intact.

```json
{
  "commit": "f0335ede51526670ab65c3f41a7c5155e5c2ead8",
  "elapsed_seconds": 58.628,
  "exit_code": 0,
  "file_count": 851,
  "finished_at_epoch": 1790334691.9026463,
  "input_digest": "11adf26e7800e69c10baafed10b2f6df2adb98d8bbfcc4bdeb2759121cd39087",
  "inputs_unchanged": true,
  "log_path": "/workspace/scratch/38af7099c566/v11-test-evidence/pws-replay-affected-20260925-01.log",
  "peak_rss_kib": 76444,
  "started_at_epoch": 1790334633.2750616,
  "status": "PASS",
  "system_seconds": 16.666575,
  "tree": "be4f98bb3fbf160011b89bc393132c304f541b1d",
  "user_seconds": 39.471126
}
```

```text
........................................................................ [ 18%]
........................................................................ [ 37%]
........................................................................ [ 56%]
........................................................................ [ 75%]
........................................................................ [ 94%]
....................                                                     [100%]
380 passed in 57.74s

```

## Combined source-release/PWS focused checks

**79 passed in 17.50 s, exit 0**, session 68969 (16 new source-release cases plus
15 new PWS cases and existing replay/release tests). Initial run stopped at
**1 failed / 14 passed in 4.04 s**, session 83290: the new candidate test passed
an unsupported schedule_id constructor argument; it now uses the existing lane
factory, which resolves the original schedule. No production interface was widened.
Combined affected/full evidence remains due on the saved implementation tree.

```text
..............F
=================================== FAILURES ===================================
_ test_assembled_reaction_lane_reaches_candidate_audit_with_original_receipt_pair[SOURCE_SHOCK] _
tests/test_v11_release_replay.py:87: in test_assembled_reaction_lane_reaches_candidate_audit_with_original_receipt_pair
    lane=app.SourceReleaseLane('release',inputs(r,kw),(target(r),),'fixture',r['request'].valuation_policy,10.,schedule_id='schedule')
E   TypeError: SourceReleaseLane.__init__() got an unexpected keyword argument 'schedule_id'
=========================== short test summary info ============================
FAILED tests/test_v11_release_replay.py::test_assembled_reaction_lane_reaches_candidate_audit_with_original_receipt_pair[SOURCE_SHOCK]
!!!!!!!!!!!!!!!!!!!!!!!!!! stopping after 1 failures !!!!!!!!!!!!!!!!!!!!!!!!!!!
1 failed, 14 passed in 4.04s

```

```text
........................................................................ [ 91%]
.......                                                                  [100%]
79 passed in 17.50s

```
