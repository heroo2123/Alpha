# Gate 3 offline resource budget candidate

`tools/v11_gate3_offline_resource_budget.py` accepts an in-memory, previously
validated V4 manifest payload and an ordered frozen projection of its requests
and event field IDs. It checks their agreement, recounts V4 purpose budgets and
quota arithmetic, and reproduces the runtime `CapacityPlan` reserve formula.
The event list is caller supplied and is not bound to a validated schedule.
An omitted event lowers the estimate. Every report therefore explicitly marks
`event_binding=UNVERIFIED_CALLER_SUPPLIED` and
`capacity_covers_frozen_schedule=false`; capacity and minimum host-resource
figures are conditional on that list and cannot qualify the actual schedule.
It reports unused request, byte, and elapsed ceilings separately from unknown
delivered bytes. Fresh journal ceilings are diagnostics; existing occupancy and
live disk and memory remain unknown.

The calculator does not validate canonical manifest evidence, review status,
provider mappings, current host measurements, or acquired journal state. The
caller must run the existing V4 validator independently. No allocation,
dispatch, account mutation, receipt, or launch path is added. Every successful
result carries `execution_authority=false`, `provider_authority=false`,
`resource_qualification=false`, `g3l=NO_GO`, and `qualification_credit=0`.
Consequently the arithmetic grants no Gate 3 qualification.

Focused synthetic checks: `python3 -m unittest discover -s tests -p
'test_v11_gate3_offline_resource_budget.py' -v` and the same command with
`python3 -O`; `git diff --check`. The tests compare the runtime formula,
exercise ceiling reporting and malformed input refusal, and guard against
socket, dispatch, and budget reservation calls. The environment has no
`pytest` installation, so the focused suite uses standard-library unittest.
