# Next engineering steps on this branch

1. Run repository CI and fix every regression before extending the runtime further.
2. Add provider-timestamp/YES-NO/midnight/delivery-uncertainty v4 integration probes.
3. Add a corrective WRH local-day/as-of adapter and six-zone/DST tests.
4. Add remaining-hours ensemble/official-nowcast ingestion; keep same-day emission
   disabled until all unresolved periods are covered and replayable.
5. Add offline decision-envelope replay and scale/recovery tests.
6. Add deployment-unit/process attestation and paper DB backup/restore tooling, but do
   not execute deployment without separate authorization.
7. Freeze one final SHA and submit it to a fresh adversarial review.
