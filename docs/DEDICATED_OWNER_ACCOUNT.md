# Dedicated bot account without Builder / Session approval

Decision recorded 2026-09-18: the user requested a supported non-Builder route,
including a bot-only funded account. The user also reports direct Open-Meteo
permission for this use. Provider permission is USER-CONFIRMED, not an independently
inspected agreement; retain that confirmation privately. Existing request limits,
source freshness checks, attribution and outage handling still apply.

## Supported alternative and custody trade-off

Connect an ordinary Polymarket account's Deposit Wallet using that dedicated
wallet's Owner signer and its CLOB credentials. This does not create another
Session grant and does not need Builder credentials for order authentication.
Relayer credentials for owner-side wallet operations remain off the executor.
This is not a way around account, geography, or EOA allowlisting restrictions.
Direct EOA trading is documented only for allowlisted EOAs and is not the default
new-account solution. Source checked on 2026-09-18:
https://docs.polymarket.com/trading/wallets-auth
https://docs.polymarket.com/getting-started/api
https://docs.polymarket.com/trading/session-keys

An Owner private key has FULL wallet authority. A compromised executor can risk
all assets in this wallet, including by withdrawal. Application BUY limits,
CLOB API keys, file permissions and this acknowledgement do not cryptographically
prevent misuse of a stolen owner key. Use a fresh bot-only recovery seed/account;
never provision a main savings key, reuse its seed, or automatically refill the
bot. Keep recovery independent of the bot and Telegram. No security guarantee or
profit guarantee is implied. An offline hardware wallet cannot provide unattended
signatures without a separately implemented signing workflow.

## Explicit configuration and acceptance

Use `live-execution-deposit-owner.example.json` only after filling independently
selected public identities, risk settings and custody decisions. The example
has no credentials, allowance, budget, activation, or pre-approved acknowledgement.
Required mode: `wallet_type=DEPOSIT_WALLET`, `signer_type=OWNER`, signature type 3,
`signer=deposit_owner`, with a distinct correctly derived wallet. The exact
`owner_custody_ack` value is `DEDICATED_BOT_OWNER_KEY_HAS_FULL_WALLET_AUTHORITY`.
`wallet_exclusive_until` is a local exclusive-use approval bound, NOT a venue
expiry for an Owner key. No Session fields may be supplied in this mode.
Omitting signer_type retains the existing Session-only interpretation and must
not switch to an Owner key. A failed Owner path never falls back to EOA/Session.

The private file remains exactly private_key/api_key/api_secret/api_passphrase.
Only this dedicated wallet's individual Owner key and its EOA-bound CLOB L2
credentials may be privately provisioned after approval. For Owner/Poly1271,
`POLY_ADDRESS` remains the Owner EOA for authenticated CLOB requests while the V2
order itself uses the Deposit Wallet as maker and signer. This split matches the
official Rust V2 client at commit
`222143d321eba97d5711a848265eb9aab3bc7ff4`. Alpha's nested Poly1271 typed digest
and 317-byte wrapper reproduce that client's published deterministic test vector
byte-for-byte. No seed phrase is needed by this process.
Do not reuse a main account or paste any secret into chat. The user creates the
ordinary account and retains recovery on their trusted device; the application
does not create an account, deploy a wallet, acquire Builder access or authorize
on-chain approvals. Owner-side Relayer credentials remain on the user's device.

Unfunded acceptance requires the real account/wallet identity, current wallet
code/owner, CLOB authentication, no unmanaged orders/activity/positions, protected
configuration, correct filesystem custody and no initial financial activation.
Funding and initial activation are separate user actions. Never fund fixture
addresses. No generic EOA funding address may be substituted for the user's
actual Polymarket deposit route. Preflight must check actual results, not assume
that code support means this particular account has been accepted.

## Receipt freshness and operational limits

The supported read-only Owner-signed standard/negative-risk redemption importer
is reused. It initiates no redemption and cannot handle arbitrary multi-call
batches. After import, original financial records and burns stay append-only.
The separate receipt-validation state is refreshed by reconciliation, at most
two receipts per cycle, oldest/unseen first. A new engine epoch must recheck all
prior imports before opening. A receipt freshly verified on import enters the
current epoch and remains part of the regular rotating re-audit.

Verification expires after 600 seconds. Missing/read-failed evidence is excluded
from currently verified proceeds and blocks openings; exact historical amounts
remain visible under recorded proceeds. Changed evidence additionally creates
a sticky reconciliation fault. Neither type of failure deletes or rewinds
history. Temporary source recovery can restore verification but never silently
clears a changed-proof fault. Large or slow histories may keep openings blocked
until the bounded audit completes within its freshness budget; this is not an
unlimited-history performance certification.

## Release boundary

This branch is development work from 1244a105f3d7888efc63b483d64603e32cb7a334.
No real Owner key, funds, account grant, relayer key or production activation was
provisioned. Host policy currently approves scanner/controller only and must be
separately extended/accepted for the exact Owner-custody executor. Tests and
source compatibility are not independent review or account acceptance. Production
state, existing financial journals and installed services remain untouched.
