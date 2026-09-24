# V11 model authority: prepared, not installed

`authority.py` is a standalone root-side publisher for reviewed **nonfinancial**
paper/shadow model epochs. It does not import candidate code, install approvals,
initialize itself or grant live authority. Its inputs and fixed paths are
documented in `docs/V11_LEARNING_GOVERNANCE.md`.

An independent owner/host review must provision this helper, its approval file,
initial empty state and approved immutable objects under protected custody before
use. Do not execute it against V10 or install it for development convenience.
Existing V10 host authority, service files and executor masks are unaffected.

Use exact reviewed scope and PAPER/SHADOW selectors for separately provisioned
state slots. Missing slots remain unavailable; there is no inference fallback
to the legacy singleton and no automatic initialization or migration.

This repository contains implementation and synthetic tests only. The current
alpha-dev resource/health gate blocks V11 deployment. No owner action is being
requested merely to run these off-host tests.
