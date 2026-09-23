# Alpha V11 work checkpoint

Updated 2026-09-23, 19:42 UTC. Resume here. Verdict: **NOT_READY_TO_FUND**.
This is an implementation checkpoint, not release or financial approval.

## Exact identities and scope

- Branch: `weather-v11-profitability-upgrade-2026-09-23`
- Last verified implementation HEAD: `cc268af500949570e25fab0b8699f58e2c9032c2`
- Last verified implementation tree: `d018c39289e0e2331595b83aa774a9eb54bb0baa`
- Worktree at implementation verification: clean; local and published trees match.
- This following checkpoint commit changes documentation only. Resolve its own
  exact commit with `git log -1 --format=%H -- docs/V11_WORK_CHECKPOINT.md`, and
  its tree with `git rev-parse <that-commit>^{tree}`. This avoids self-referential
  commit hashes and distinguishes tested implementation from checkpoint metadata.
- Frozen public base: `f5f0661307a8d426a20dbf8308d18a0ee403e9b3`, tree
  `d5d2b806e273f11e2f832940f483a5f656462584`; no moving-main substitution.
- Local V11 workspace: `/workspace/scratch/38af7099c566/Alpha`.
- Authoritative input SHA-256 matched exactly:
  `a0e16d9bd7344c943a54a16a53c6757662363d93642f6e5cb7953cd047659b4a`.
  All specification text and all four complete reference PDFs were read, including
  relevant tables/figures. See `V11_INPUT_MANIFEST.json` for exact input identities.

## Delivered implementation and tests

Phase 0 remains incomplete because protected control evidence is inaccessible.
Independent Phase 1 foundation implementation is delivered and tested. Under
`V11_REQUIREMENTS_MATRIX.md`, eight packages are PARTIAL and zero of 50 packages
are fully accepted end to end (**0% complete under that conservative denominator**).
Code delivery, technical acceptance, strategy eligibility and empirical validation
remain separate; missing integrations and blocked evidence earn no completion credit.

Delivered additive code:

1. Private per-namespace append-only causal evidence with receipt/observation/issue
   timestamps, revisions, bounded capacity, and foreign/control/live DB refusal.
2. Decision explanations and lane/source outcomes bound to exact evidence hashes,
   release/config/model/rule identities, plus deterministic-evaluator replay.
3. Bounded anonymous public GET collection, source-by-source durability, limited
   retries, total deadlines, response bounds, and no immediate retry of HTTP 429.
4. Fee/depth-aware counterfactual markouts and descriptive probability scores;
   unavailable exits stay unknown, synthetic evidence stays synthetic, no fake fills.
5. Consistent read-only SQLite backup including committed WAL, copy-only integrity
   verification, resource bounds and private bracketing runtime-context captures.
6. Snapshot-only V10 forensic reader with protocol/quarantine separation, identity
   and cash reconciliation, impossible-settlement rejection, descriptive slices,
   explicit unknown funnels and no automatic threshold or calibration certification.
7. An owner-invoked capture helper, staged separately on the host and verified with
   its read-only preflight. The owner-only capture itself has NOT run.

Validation:

| Check | Exact scope | Result |
|---|---|---|
| Untouched baseline | pinned V10-equivalent source | 2,336 passed; 4 warnings; 63.45 s |
| Foundation regression | tree `2363b44483cdca298fd17f21c01bec1fda17dac4` | 2,384 passed; 4 warnings; 67.94 s |
| Final implementation regression | tree `d018c39289e0e2331595b83aa774a9eb54bb0baa` | **2,389 passed; 4 warnings; 60.62 s** |
| V11 targeted tests | evidence, collector, snapshot and forensics | **53 passed** |
| Static/local checks | Python compilation, shell syntax, staged diff whitespace | passed |
| Public Git checkpoint review | explicitly selected source/docs/tests only | no private PDFs, full prompt, database, credentials or raw runtime evidence |
| Foundation GitHub Actions | run 35910573402 at `8101f9944e8249ed8ca79d183524efe4195ba063` | completed successfully |
| Final-code GitHub Actions | run 35911031597 at `cc268af500949570e25fab0b8699f58e2c9032c2` | in progress at 19:42 UTC; not claimed passed |

The four warnings are the same existing FastAPI lifecycle deprecation warnings.
Tests ran off-host in an isolated environment installed from the hash-locked
`requirements-dev.txt`. Synthetic fixtures establish code behavior, not live
market evidence or independent review. No independent reviewer has reviewed V11.

## Frozen control and deployment state

The remote control checkout remains clean at
`5bbac24759349714d4521faf9e087a14c5c0ae05`, tree
`d5d2b806e273f11e2f832940f483a5f656462584`. The staged preflight again verified
all 221 deployed application/lock files and the launcher/unit hashes. Readable
installed distribution metadata matched all 23 pinned runtime packages, with
pip 24.0 additionally present; installed wheel bytes are not fully attested.

V10 service: active/running since 2026-09-22 06:13:31 UTC, NRestarts=0. Memory:
454,397,952 bytes of 524,288,000 maximum. This is process-level health only;
current successful source/settlement cycles are not verified without status/logs.
Executor: MASKED and INACTIVE. Production controller: INACTIVE. Paper-unit
filesystem isolation excludes execution credentials and financial state. Clock
synchronization reports healthy. No service, source, permission or executor-mask
change was made to V10.

The readable launcher expects release marker
`2a1fe2b0d199ca87fabc1f119fae6ee4902f73da`, distinct from the verified source tree.
The protected marker/status/database must resolve that distinction; do not edit
control metadata merely to make identifiers agree. See `V11_V10_FORENSIC_BASELINE.md`.

One CPU. Latest capture preflight: 819,974,144 bytes available RAM and
5,816,803,328 bytes free disk; prior inspection showed active swap use. No host
training, regression suite, V11 paper/shadow runtime or financial process started.
The prepared one-shot snapshot has separate CPU/RAM/time limits and must recheck
headroom when the owner actually runs it.

## Models, namespaces and evidence watermark

- V11 deployment: none. Only separately staged read-only capture tooling exists.
- Actual preserved V10 snapshot: NONE; protected-source access blocked.
- Actual V10 forensic findings/counts: UNKNOWN; no dated figures silently reused.
- V11 live/paper/control/challenger ledgers: no shared or migrated database;
  unit-test archives only. No V11 trading ledger or operational balance exists.
- Champion/challenger artifact IDs: none. No training, promotion, demotion or rollback.
- Latest learner dataset/watermark: none. No calibration or selection run.
- Actual PWS lead, executable-exit, maker-fill and settlement evidence: unverified.
- Account entitlement, credentials-presence metadata and balances: unverified.
- Financial authority: unchanged. No funding, transfer, wallet/account creation,
  credential disclosure, real order or live activation.
- Canary eligibility: NONE. Empirical validation: NONE. NOT_READY_TO_FUND.

## Blocker, prepared owner action and exact continuation

The connected `alphaadmin` identity cannot read `/var/lib/alpha-paper-demo`, the
runtime status or journal. `sudo -n -l` requires a password. No access control has
been weakened or bypassed. The implementation is ready for one owner-only action:

```sh
bash /home/alphaadmin/alpha-v11-phase0-20260923/deploy/v11-capture-control-owner.sh
```

This staged helper and its two supporting files are exact published bytes from
commit `8101f9944e8249ed8ca79d183524efe4195ba063`; the later accounting patch does
not alter them. `--check-only` passed on `alpha-dev`. The actual isolated capture
has NOT run. See `V11_CONTROL_CAPTURE.md` for safeguards, output and scope.
Do not request a secret in chat. The owner uses the host's own sudo prompt.

After that succeeds:

1. Verify and privately preserve the completed snapshot and context hashes;
   reconcile source/config/release marker and successful recent runtime cycles.
   Journal access and complete financial-containment attestation remain separate.
2. Analyze the immutable copy off-host; produce full V10 baseline slices, accounting
   and zero-lane diagnoses. Missing fields remain unknown, not reconstructed claims.
3. Freeze development hypotheses and predeclare later/event-disjoint validation
   before finalizing calibration, economic thresholds or promotion rules.
4. Continue the dependency order: source adapters/registry/rule fingerprints and
   actual free-PWS availability, coherent valuation/calibration, controlled learning,
   event state/EV/coordinator/risk, strategy migration, exits, maker, guardian and
   technical/unfunded acceptance. Reuse verified existing components.
5. Keep public checkpoints nonsecret. Current official auth documentation makes
   Session access entitlement-dependent and EOA trading allowlist-dependent;
   neither account-specific path is attested. No funding or activation follows
   from a test pass or from the snapshot action.
