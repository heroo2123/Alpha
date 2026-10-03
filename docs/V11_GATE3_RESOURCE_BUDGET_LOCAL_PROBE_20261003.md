# Local probe of held offline resource-budget candidate — 2026-10-03

Candidate `0df1a950993641c9e09961805d4bfe2a5894e6fd` remains unmerged and pending candidate-specific authorized different-model exact review. This is a local coordinator probe, not an independent review or verdict.

The calculator verifies the supplied request triples against the manifest schedule, but it does not bind `frozen_plan['events']` to a manifest field or an independently validated frozen plan. On the candidate's own two-request synthetic fixture, replacing its one field event with an empty event list still returns an `OFFLINE_PROPOSAL` report. `aggregate_nodes` changes from 1 to 0 and `runtime_capacity.disk_bytes` falls by 8,585,216. Both outputs retain `resource_qualification: false` and `g3l: NO_GO`.

Reproduce locally in the held author worktree with Python 3 by importing `fixture` from `tests/test_v11_gate3_offline_resource_budget.py`, calculating once with its original plan, then setting `plan['events'] = []` and calculating again. The original plan has one event linked to `field`; the mutated plan has none. No network, provider request, host reservation, or external transfer occurred.

Before integration, the candidate should either require a separately validated exact frozen-plan binding for the event list, or present its capacity estimate explicitly as conditional on unverified caller-supplied events with no implication that it covers the actual frozen schedule. Add a focused regression for this omission. Preserve the exact held commit for its authorized reviewer; repair and re-review a new exact commit if the independent verdict confirms the finding.
