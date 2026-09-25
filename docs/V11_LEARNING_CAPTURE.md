# Forecast capture and causal dataset integration

`v11/learning_capture.py` joins the protected FUTURE_FORECAST prediction in
`TemperatureStrategies.evaluate` to the existing feature, decision and dataset
interfaces. It captures every YES bucket of that evaluated event before the
requested token's entry economics. Complementary NO claims do not double the
training population. This is event-vector coverage, not proof that every
supported event was discovered, admitted or evaluated.

The capture binds the original inference cutoff, source hashes, model members,
issue/receipt/availability times, exact rule and protected bundle. Numbered model
members and explicit quantization cuts form a stable feature schema, bounded to
128 fields. The cut convention is a model hypothesis, not settlement-source
rounding proof. Features retain their actual computation time. Interrupted child
records remain immutable; retries neither backdate them nor renew original
expiry. Completed requests replay without writes. Capture failures retain the
forecast and report a separate dataset gate.

Capture v2 now requires a `PinnedBundle` whose declared FEATURES artifact exactly
matches the shared `ForecastFeatureContract` and whose probability parameters
match the archived prediction. Model IDs/member widths, unit, daily family and
supported quantization cannot change silently. Existing completed v1 captures
remain readable/replayable under their original version; they are not relabeled
as v2 compatibility evidence. A legacy generic parent can still be read, but a
new mismatched forecast capture is gated. Interrupted incompatible legacy children
are retained and require explicit reconciliation, never overwrite or redating.

`build_initial_forecast_bundle` builds new INITIAL_NO_FIT research objects with
this contract and explicit probability/provenance/quality parameters. It refuses
a parent identity, unsupported dimensions and fitted claims; it never rewrites
an existing bundle or installs an active pointer. Calibration stays vacuous and
execution-cost evidence stays UNKNOWN.

`v11/forecast_learning.py` provides the explicit offline join to the existing
bounded learner. A typed complete-label cohort and registered frozen plan produce
a separate CHALLENGER-journal recipe with original capture/example hashes, exact
dataset identity and counts. Training is never invoked by capture or the trading
runtime. Completed jobs replay the saved result; interrupted attempts are gated
for review, not silently refitted. Historical predictions keep their original
bundle identity even when compared under a compatible current research parent.
The cohort is bounded to 128 captures/2048 examples/ten seconds of assembly;
the unchanged learner has its own finite numerical/time limits. OS separation
remains unverified.

The job now uses `learning_sources.learning_source_view`: a bounded SQLite
`mode=ro`, query-only transaction including committed WAL. Source sequence and
original rows are pinned; later appends and caller dictionary mutations cannot
change the view. There are no source write/checkpoint/schema pragmas. Reads cap
at 8192 records/8 MiB/ten seconds; dataset serialization caps at 16 MiB.

`build_example` now retains a separate source-derivation manifest for normalized
raw references and full GEFS paths. Original times, hashes and edges survive
later revisions; unknown availability, cross-event/provider/kind, bad hash,
missing references and noncausal sequence/time fail closed. The offline graph
cap is 1024 records/2048 edges/512 KiB metadata/two seconds. Runtime decisions
remain at 64 inputs and the existing feature DAG remains capped at 256. This is
receipt provenance, not independent source truth or full provider acceptance.
Fifteen new tests include the complete 621-record synthetic GEFS derivation and
read-only/WAL/byte/time gates; the related suite passed 168 tests in 31.28 seconds.

The subsequent declared-contract/learner integration passed 145 related tests
in 29.35 seconds, including 29 new cases. Synthetic 31-member C/F and high/low
event vectors traverse capture, complete labels, causal temporal splits, fitting
and candidate inference with numerical parity, parent preservation and no
promotion. This is not actual-label, calibration, source-access or live evidence.

All capture decisions are GATED for executable economics. They create no label,
fill, account mutation, training run, active-model pointer or financial authority.
`labeled_examples` requires a complete, consistent set of explicitly supplied
exact bucket labels and delegates causal/identity checks to `build_example`.
The default cohort is DEVELOPMENT. Label attestation remains false; fabricated
test labels are SYNTHETIC. A successful join does not certify source finality.

The 17 new tests cover the rejected-trade integration, all-event-bucket capture,
dataset construction, original timing, interrupted replay, scope/source defects,
label inconsistency, bounded failure and target separation. The related
dataset/learner/artifact/strategy/candidate run passed 114 tests in 22.55 seconds.
No real or forward learning evidence is claimed.

Still required: actual exact-label production and independent attestation;
universe coverage and selection analysis; remaining execution and maker
target-specific features; accepted calibration; isolated
training and champion/learning acceptance. A next official observation remains
distinct from final payout and an executable exit.

## Conditioned and paired observation captures

`target_learning.py` now joins same-day payout and paired PWS observation
predictions to the candidate and read-only dataset path before economic filtering.
All payout buckets retain the exact observed revision, complete unresolved interval
coverage, original inference cutoff and reproduced frozen prediction. Physical
features/member values match the parent contract. Observation pairs retain the
same first-Alpha-receipt window, official anchor, unit/source/population and
otherwise-identical ablation inputs. Receipt-window targets are never silently
relabeled as next publication, final payout or executable proceeds.

New learning snapshots use LEARNING_FEATURES, which cannot satisfy operational
FEATURES leases and do not advance their source heads. The existing source/CAS,
clock, model and cancellation safeguards remain unchanged. Interrupted children
remain immutable and completed captures replay without timestamp renewal. The
bounded source derivation also follows explicit mixed-provider physical/remaining
model dependencies and PWS QC captures down to original AWC/MADIS/GEFS receipts.
Original normalized raw-reference edges still require the same provider/kind.
Malformed/ambiguous references, future inputs, labels in feature graphs and
synthetic-to-public evidence substitution gate. Limits are unchanged.

`labeled_target_examples` accepts only explicitly supplied labels. Observation
labels bind an existing first-received-report score and its exact official source,
with receipt/observation/revision identity retained in the example. It does not
create or independently attest a label. Two ablations remain one paired target;
statistical independence is unknown, and dataset counts group them by event and
city-day. Original prediction time determines temporal partitioning; archival time
is retained separately, and a known label cannot become new through later capture.

Conditioned Gaussian payout examples have a separately declared fitting policy;
ordinary forecast policies still reject them. Physical coefficient and observation
fitting remain gated until their own verified contracts/evidence exist. Other
required execution/markout/maker target coverage and empirical acceptance are open.
