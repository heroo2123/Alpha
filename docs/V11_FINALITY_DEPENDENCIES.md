# V11 exact-finality dependency review

RESULT_LAG remains GATED. This review does not activate the existing research
lane, certify a final label or create an irreversibility proof.

The existing weather_only_wrh_finality.py explicitly describes a bounded polling
bracket. Two equal endpoints cannot exclude an unobserved transient source
revision between them. Its exact_publication_state_observed,
correction_state_reconstructable, calibration_label_authority and
settlement_label_authority fields remain false. weather_only_result_lag.py
requires the relevant authority fields before selecting a winner.

The legacy result-lag adapter also requires the precise WRH_HOURLY_DATA population,
whole-degree Fahrenheit precision and its compiled cutoff/correction/fallback
semantics. A V11 WRH_ALL_TIMES rule fingerprint must not silently inherit that
population or finality. Neither the age of a quote nor the next official report
proves a contract's final payout, revision cutoff or executable early exit.

Remaining dependency: an exact-source adapter and archived publication/version
history or other independently reviewed evidence sufficient for the actual
contract's source, local date, correction window, late/special reports, fallback
and irreversible winner. Station capability admission separately requires
EXACT_FINALITY and FINAL_PAYOUT_IDENTITY; a protected capability flag alone cannot
substitute for event-specific proof. No such adapter/history/proof was delivered
by this review. R31 remains OPEN. Do not weaken the legacy gate or relabel research
polling history as source-label authority. Independent off-host exit/accounting,
maker and guardian work can continue while this evidence dependency is unresolved.
