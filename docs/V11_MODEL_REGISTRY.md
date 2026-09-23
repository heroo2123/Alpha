# V11 model bundles and decision epochs

Implemented: `v11/model_artifacts.py` and `v11/model_registry.py`. These are data
validation and inference interfaces, with no account/order API.

A compatible bundle contains exactly five hash-addressed components: feature
schema, probability model, calibration, execution cost and strategy quality.
Targets and feature schemas must match, and calibration explicitly binds its
probability artifact. Provenance carries code/tree/config/dependency/runtime,
dataset/watermark, parent, seed, model version, metrics and comparison-policy
identities. An initial model can explicitly say no fit occurred; it cannot invent
training metrics. A fitted model remains `FITTED_NOT_CALIBRATED`.

Schemas allow bounded numeric parameters and known methods only. They refuse
extra loader/code paths, risk or settlement-policy fields, ambiguous JSON keys,
nonfinite values and oversized structures. The current calibration method is an
honest uncalibrated full-width envelope. The execution component explicitly says
no empirical cost model is available. New calibrated or empirical execution
schemas require reviewed implementation and real supporting evidence.

Research objects are published as complete, canonical, private read-only files
using exclusive content-addressed names. A partial write is not exposed as a
complete object. Existing objects are rehashed and cannot be overwritten through
the API. Directory count/bytes, individual bytes and disk headroom are bounded.
The research writer has no active-pointer method.

Inference reads a separately protected state and approved object directory. A
decision pins one epoch, scope, mode, bundle and safety overlay. Final validation
requires the same state and rechecks approved object bytes. A changed epoch or
overlay requires recomputation. Cached predictions and old decisions retain their
original immutable bundle identities.

The approved reader has no artifact/state writer. A fixed protected path cannot
be redirected through an environment variable. Custody requires root ownership,
no writable parent for the development user and no symlink substitution. These
checks are not a substitute for actually provisioning a separately reviewed host.

19 artifact tests and 21 governance tests cover schema/hash bounds, incompatible
components, exact pins, persistent demotion, reviewed rollback, parent/epoch
comparison and interrupted publication. The manifests used by tests are synthetic.
No initial champion is commissioned and no model is financially active.
