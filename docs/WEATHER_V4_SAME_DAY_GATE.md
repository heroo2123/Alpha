# Same-day conditioned weather lane — promotion gate

The same-day lane is intentionally not emitted by v4 yet.  Promotion requires all of
the following, tested end to end with decision-time evidence:

1. Exact contract station, local target date, eligible observation population, unit,
   high/low statistic and fallback/correction semantics are frozen in the decision.
2. Official observation requests cover the entire local-day interval in UTC,
   including zones east of UTC and 23/25-hour DST days; response units and every row's
   as-of timestamp are verified.
3. Layer A computes the accepted official extreme so far from only evidence available
   by the decision time. Revisions replace the applicable observation version rather
   than adding a second vote.
4. Any elapsed interval not established by accepted observations remains unresolved;
   delayed/missing past periods are never silently treated as known.
5. Layer B (official short-horizon nowcast and/or PWS) is explicitly forecast/
   diagnostic evidence. PWS cannot become settlement authority by proximity.
6. Layer C supplies member-level trajectories/extremes over *all unresolved portions*
   of the day. A whole-day daily aggregate is not sufficient.
7. For each member: daily low = min(accepted observed low, unresolved-member low), or
   daily high = max(accepted observed high, unresolved-member high). Thus an accepted
   7°C low makes 11°C final-low impossible but does not make exactly 7°C certain if
   the remaining day can reach 6°C.
8. New observations/revisions arriving during forecast/CLOB/Telegram awaits invalidate
   the old decision and force a new evidence version.
9. The conditioned member frequency remains explicitly uncalibrated until a separate
   prospective calibration program validates it.
10. Six-zone, DST, missing-hour, late-revision, unit-drift, wrong-station and midnight
    adversarial tests all pass before Telegram emission is enabled.
