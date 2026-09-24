# V11 model governance preparation

The standalone helper at `host_trust/v11-model-authority/authority.py` prepares a
separate protected model-state publisher. It imports only the standard library,
never candidate application code. It has no network or financial endpoint.

The current helper supports `V11_PAPER` and `V11_SHADOW` only. It cannot create a
live mode or grant financial authority. Financial model commissioning will need
the separately reviewed live host/activation integration; this implementation
does not claim that gate has passed.

A promotion or rollback needs one root-custodied review binding exact scope,
mode, current epoch/parent, compatible bundle preimage, five component hashes,
reviewer and validity window. The candidate object bytes must already exist in
the approved store and match their canonical byte identities. Missing/changed
objects leave the old state untouched. The learner cannot install approvals.

The authority uses an exclusive lock, compares the expected state, writes and
fsyncs a complete new state, then atomically renames it and fsyncs the directory.
Pointer, epoch, overlay and append-only event history are one atomic file. Failed
publication before rename retains the old state. A failure after rename is an
uncertain acknowledgement: read and reconcile the actual protected state; never
assume rollback or reuse an old decision pin.

Automatic demotion can only reduce the size multiplier and require manual review.
It does not mutate the frozen model. Promotion/rollback preserves any existing
safety reduction. Restoring eligibility requires a separate explicit reviewed
recovery; time passing is not recovery. Review IDs cannot be reused.

The fixed paths are:

- `/etc/alpha-v11/approvals/model-bundles.json`
- `/var/lib/alpha-v11/model-authority/scopes/V11_PAPER/<scope-sha256>.json`
- `/var/lib/alpha-v11/model-authority/scopes/V11_SHADOW/<scope-sha256>.json`
- `/var/lib/alpha-v11/model-authority/objects/`

Inference selects the exact scope and nonfinancial mode. Distinct observation
and payout champions can coexist under separately reviewed scopes. A missing
slot remains gated; inference never falls back to a singleton or another scope.
The embedded scope and mode must match the selected path. Demotion or promotion
of one slot cannot refresh or replace another slot's epoch or decision pin.

The standalone publisher uses the same `--scope-key` and `--mode` selectors.
Each slot and its protected parent directories must already be provisioned under
reviewed host custody; this helper does not initialize, migrate or relabel them.
The mode-directory lock serializes publishers and each complete slot changes by
atomic rename. The legacy `/var/lib/alpha-v11/model-authority/state.json` remains
available only through explicit unscoped inspection/maintenance calls. No active
inference path uses it, and no legacy state is automatically migrated.

No installer, initial state, approval or service has been applied to alpha-dev.
The helper itself must be independently reviewed and installed under protected
host custody; repository code cannot approve its own installation. The learner
must have no write access to these paths and no financial credentials. The actual
host boundary is unverified, with deployment additionally blocked by V10 memory
pressure and stale cycle status.

Unit tests cover the state machine, protected-path rejection and synthetic local
atomic-write interruption. They are self-tests, not an independent security review
or proof of a commissioned host boundary.
