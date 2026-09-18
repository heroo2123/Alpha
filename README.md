# Alpha weather production application

The canonical weather application is `python -I -m polymarket_scanner.production`.
It provides a private Telegram operator panel, live manual signals and a separately configured execution worker.

- **LIVE_SIGNALS** continuously checks public markets/weather, sends precise Telegram signals, invalidates stale alerts, and observes outcomes without assuming you traded.
- **LIVE_EXECUTION** adds authenticated order submission, cancellation, fill/fee reconciliation and actual holdings. It requires your account identity, credentials, explicit risk settings and fee policy, configuration-bound activation and operational readiness. Selecting the mode alone grants no financial authority.
- Historical simulations remain separate tools and databases. Their P&L is excluded from actual account reports.

Read the [Telegram panel guide](docs/TELEGRAM_OPERATOR_PANEL.md), [account/security compatibility](docs/ACCOUNT_SECURITY_ONBOARDING.md), [unfunded UpCloud commissioning package](docs/UPCLOUD_UNFUNDED_COMMISSIONING.md), [production operations](docs/PRODUCTION_OPERATIONS.md), [strategy/weather scope](docs/WEATHER_PRODUCTION_REVIEW.md), and [execution API/dependency policy](docs/PRODUCTION_EXECUTION_API_POLICY.md). Configuration templates are in [config/production](config/production). Financial values are deliberately unset for the operator to choose.

After independently approved host provisioning, use the sealed release's isolated interpreter:

```sh
/path/to/scanner/venv/bin/python -I -m polymarket_scanner.production scanner --config /etc/alpha-weather/config.json
/path/to/controller/venv/bin/python -I -m polymarket_scanner.production controller --config /etc/alpha-weather/config.json
/path/to/execution/venv/bin/python -I -m polymarket_scanner.production preflight --config /etc/alpha-weather/config.json
/path/to/execution/venv/bin/python -I -m polymarket_scanner.production execution --config /etc/alpha-weather/config.json
```

The panel requires the protected `operator_control` configuration. The retained `signals` command is the combined text interface for configurations without that section. Scanner and controller need no trading credentials. Execution retains the dedicated eligible Polygon EOA route and this development branch adds a Deposit Wallet **CLOB-only Session Key** route; both use reviewed V2 long BUY orders, FAK takers and post-only GTD makers. EOA keys are not withdrawal-restricted. The Deposit route keeps Owner/Builder keys off-host, uses signature type 3 and a wallet-wide public activity witness, but still requires a dedicated single-session wallet because another session's resting orders are not account-wide visible. It is not a funded/released capability until independent review, host executor approval and unfunded account preflight pass. Structural legs reserve all limit-price costs and fee allowances, with explicit legging risk. V2 fees have no signed per-order ceiling; the operator explicitly chooses a current onchain-bound or published-schedule policy. Result-lag opportunities are rejected when publication finality cannot be established. Same-day/source-shock observation support is currently Fahrenheit-only. Raw model frequencies are uncalibrated.

Signals, hypothetical simulations, confirmed fills, outstanding/unknown orders, settlement claim value and verified redemption cash are reported separately. `/start` opens screens and settings. Choose signals, confirm-one-attempt or automatic within an independently authorized ceiling. `/stop` pauses openings; `/cancel_open` also requests cancellation. Neither erases fills or proves cancellation. Telegram cannot issue the initial financial grant; confirmed resume still requires existing config-bound authority and fresh reconciliation. No autonomous selling or redemption is added.

Use distinct OS identities, state directories and freshly locked virtual environments. Application candidates cannot install or replace the independent host authority that approves them. Never rewind a financial journal after submission; follow the operator guide's controlled recovery protocol.

## Verification

```sh
python -m pip install --require-hashes -r requirements-dev.txt
python -m pytest -q
python -m compileall -q polymarket_scanner deploy host_trust tests tools
python tests/validate_shadow_units.py
python tests/validate_shadow_scale.py --output /tmp/alpha-shadow-scale.json
```

CI covers Python 3.11 and 3.12. Financial writes and Telegram delivery in tests are mocked. Public source/census workflows are read-only. Repository verification does not certify the real host, account funding, geographic eligibility or credentials; deployment and real-money activation remain separate operator-controlled steps.

The [continuation checkpoint](docs/OPERATOR_CONTINUATION_CHECKPOINT.md) records this operator pass; the [engine checkpoint](docs/PRODUCTION_CHECKPOINT.md) retains the previous production baseline. A working-tree implementation is not release approval.

The unrelated legacy scanner and its research tools are preserved; their historical setup is in [the legacy shadow guide](docs/LEGACY_SHADOW_README.md). They are not the canonical weather production startup path.
