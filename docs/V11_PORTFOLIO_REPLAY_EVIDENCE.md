# Original prepared basket/exit valuation replay evidence

2026-09-25. Original master SHA-256
`a0e16d9bd7344c943a54a16a53c6757662363d93642f6e5cb7953cd047659b4a` unchanged.
Recovered clean published b351cdb584ddbc8e17af4e77eee96b3709849f57/tree
33a74215b090094f78c5b049a0a63af2968555b9; all 764 implementation inputs matched.
No running operation was retried. V10 untouched, maintenance deferred.

`basket_valuation.basket_details` is the shared computation without journal-return
shortcuts or writes. `position_management.payout_inputs` shares original payout/
conditioning inputs while actual runtime admission/protected inference stays in
`_predict`. `portfolio_replay` reconstructs original prepared baskets and exits,
retaining every prepared candidate including those rejected by allocation. It
uses retained original model history, exact original source/valuation cutoffs,
books and original account inventory snapshot/FIFO economics. Rejected preparation
and other valuation families remain conditional/unimplemented. This is numeric
comparison, not historical strategy selection, controls, executable attestation,
independent source truth, or renewed admission.

`replay_valuations=True` joins proofs to existing account-effect replay using the
same readonly snapshot and total deadline. Typed `AuditPolicy.account_valuation_replay`
requires account replay and adds separate complete-cohort valuation coverage.
False/default omits the option from the serialized configuration to preserve old
candidate/report identities. Missing evidence and unsupported values remain
visible; no command, account mutation or financial authority is issued by replay.
Candidate-run and additional failure coverage are next at this checkpoint.

## Development verification

Interpreter: `/workspace/scratch/38af7099c566/alpha-v11-venv-20260924/bin/python`.
Working directory: `/workspace/scratch/38af7099c566/Alpha`.
Logs: `/workspace/scratch/38af7099c566/v11-test-evidence/`.

| Run | Exact pytest selection | Result / session / exit | Raw log SHA-256 |
|---|---|---|---|
| portfolio-refactor-20260925-01.log | tests/test_v11_basket_valuation.py tests/test_v11_position_management.py | 43 passed in 5.00s / 1592 / 0 | 867dcae171c1c5ae2e0915d313ca982043e66918ca4c87ee7c2b7116af91417d |
| portfolio-dev-20260925-01.log | tests/test_v11_portfolio_replay.py | 4 failed, 11 passed in 2.90s / 56745 / 1 | d63c07579bad692e7b523401ca07e6261aa7bfbc59547baa22571d1b75ca283e |
| portfolio-dev-20260925-02.log | tests/test_v11_portfolio_replay.py tests/test_v11_account_replay.py tests/test_v11_account_replay_integration.py | 51 passed in 5.80s / 67586 / 0 | f3a1fdd0fd50299dea9888f03e5b0cdfdaabec53e9ee92f9280dbb7488f302f5 |

Initial failures were fixture expectations, corrected without weakening production
gates: both basket variants have synthetic roots without raw derivation edges;
missing model evidence already gates the whole original account command; inventory
comparison binds the original account snapshot rather than an unused older fill
command. The corrected missing-inventory case removes the required snapshot.
New output explicitly records input evidence classes and derivation availability.
Sources/markets/clocks/model approval fixtures are synthetic. No real economic
edge, fill or actual provider availability is inferred.

**83/200 (~42%); formal 1/50 (2%)**, unchanged. Full original preparation/control
and executable replay, actual/calibration evidence, independent review, isolated
deployment and unfunded acceptance remain open. **NOT_READY_TO_FUND**.
