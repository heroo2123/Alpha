# Telegram operator panel

Use the canonical `polymarket_scanner.production` application with the new
`scanner`, `controller`, and optional `execution` components. The existing
`signals` component remains a compatible combined text interface when no
`operator_control` section is present. It refuses a panel configuration so two
processes cannot poll the same bot accidentally.

The scanner collects public weather/markets into a separate bounded handoff.
The controller owns the Telegram token, signal history and non-secret request
store. The executor owns financial credentials, the execution journal and the
actual effects of structured requests. Host identities and permissions must
make those distinctions real. No public HTTP listener is needed.

## Independent authorization

Add the `operator_control` object from the example fragment to the protected
configuration. Select real numeric bot/private-chat/operator IDs, a unique
independently approved authorization version, an explicit expiry (UTC Unix
seconds), allowed experiences/fee policies and UTC schedule windows. Empty
schedule means no openings. The chat ID must belong to an authorized human.
Only `SIGNALS` and an empty fee-policy list are allowed in LIVE_SIGNALS.

Financial settings in the protected configuration remain maximum ceilings.
There are no suggested bankroll, loss, allocation or leverage values. Initial
settings are **SIGNALS and paused**, even with a valid financial activation.
Selecting CONFIRM or AUTOMATIC always pauses again; resume is a separate
confirmed action. Increases within the ceiling and resume require the existing
config-bound activation, fresh account reconciliation and an unexpired grant.
Telegram cannot issue the initial grant, raise a ceiling, add an operator, clear
a fault, write a protected file, run a shell or supply a signing payload.

`/start` opens the panel. `/status`, `/recent`, `/positions`, `/stats`, `/orders`
and `/settings` open the corresponding screens. `/stop` and `/cancel_open`
remain immediate authenticated requests. Private chats only; group, forwarded,
inline and wrong-bot actions are rejected. Buttons carry an opaque server action
ID, never a price/order/signature. They bind actor, private chat, bot, config and
authorization versions, settings revision, pause epoch, expiry (at most 120s),
message ID and one-use state. Double clicks/replays never create another order.
Open `/start` again after a stale button. Typed settings must reply to their
specific bot prompt and then pass a separate preview/confirmation.

Preview text and all its buttons use one captured settings revision. A concurrent
increase cannot silently enlarge a previously displayed trade limit. Once a
fresh authenticated pause/cancel callback or command is durably accepted, its
safety reduction survives worker delay/restart and later settings revisions;
its original acceptance time must be inside the button lifetime. This exception
never authorizes opening, resume or settings increases. Direct safety commands
can retire an unused preview when the UI preview queue is full, and UI reply
pressure cannot discard later safety commands in the same Telegram poll.

## Screens and controls

- Home: mode/experience, opening authority and disabled reason, account,
  observed balance versus reservations, order/position counts, source and
  execution health, approved release/config identities and notification backlog.
- Strategies: independent enable/disable within approved scope, required
  evidence, current eligibility and units. Uncalibrated support is never called
  win probability. Result-lag is visibly gated on unavailable source finality.
- Risk/settings: allocation, per-order/station-day caps, worst-case and daily
  loss, order/position caps, price/slippage, fee allowance/policy, maker lifetime,
  basket full-cost loss budget, model/structural thresholds and UTC schedules.
  Values outside the independent ceiling or internally inconsistent limits are
  rejected with a reason. Overnight schedules must be split at UTC midnight.
- Orders/positions/performance: actual confirmed fill quantity/cost/fees;
  outstanding/UNKNOWN/partial orders; signal outcomes and excluded research
  separately. Settlement/cash shows claims separately from verified receipts.
- Pause stops **new openings**. Cancellation requests affect managed outstanding
  orders and remain pending until exchange reconciliation. Neither action sells
  or removes held positions. Reconciliation/expiry management continue.

Every settings revision invalidates in-flight opening attempts before submission
and requests cancellation of resting orders under the older settings. Confirmed
fills, original order limits and committed reservations remain. A request may
arrive after an order was posted; it cannot retroactively make that order vanish.
The dedicated safety loop checks requests independently of weather/reconciliation
waits. UI replies and financial notifications use separate bounded work queues;
a slow Telegram reply cannot defer request persistence or exchange cancellation.
UI replies lost on restart are refreshed with `/start`; financial events use the
durable mechanism below.

CONFIRM authorizes one fresh bounded attempt for the immutable signal. Review
its per-order, price, slippage, fee and basket limits. The executor consumes the
permit durably and revalidates contract/weather/book/risk before using its
existing submission path. A failure or crash consumes the attempt; it is not
retried automatically. No old Telegram quote is used as a purchase instruction.
The existing account/signal unique intent and UNKNOWN reconciliation rules still
prevent duplicate submissions.

## Local versus exchange enforcement

Allocation, station/local-day concentration, max positions/open orders, schedules,
model thresholds and worst-case loss are local admission/reservation controls.
Daily loss is UTC-day recorded settlement losses plus all unsettled acquisition
costs/fees and outstanding/new reservation; profits do not offset loss ceilings.
Already-held costs may exceed a newly reduced ceiling; new openings stay blocked
by reservation checks, and there is no automatic sell-down.

Signed BUY limit price and quantity constrain the exchange order. FAK, GTD and
post-only semantics are checked against supported interfaces. V2 expiry is an
exchange-enforced request, not a signed expiry guarantee. Slippage is a fresh
local comparison to the original signal reference, not a guarantee of liquidity.
Neither local fee policy signs an immutable venue fee cap. Published fee schedules
and administrator limits can change; actual confirmed fees always enter the
journal, including breaches. A breach faults openings and queues cancellation.

## Durable execution notifications

Execution audit entries atomically accompany the financial transition. A
sanitized batch (100 audit rows maximum) goes through the non-secret status file.
The controller commits notifications and its cursor together. The hot outbox
holds at most 500 unresolved notifications; receipt cache retains 5,000 successes.
Backpressure leaves original events in the execution audit. UNKNOWN/FAILED rows
remain visible and require operator attention; they are never silently discarded.
Monitor DB/disk budgets; financial audit records are not automatically deleted.

Events distinguish submitted/acknowledged, UNKNOWN, rejected, partial/full actual
fills, cancellation requested/confirmed, expiry, useful validation/risk rejection,
fault/breach, pause/control result, settlement claim and verified redemption.
Telegram credentials never enter the execution component. A send is marked
SENDING before the network call; a lost response or crash becomes UNKNOWN with
no automatic retry. Explicit rate-limit rejection gets at most eight attempts.
There is **no exactly-once or guaranteed-delivery claim**. This conservative
at-most-once treatment of ambiguous calls can lose a notification; status and the
immutable financial journal remain authoritative. New signal-delivery and
terminal-edit protocols preserve their existing separate guarantees.

## Local recovery and grant rotation

Existing local stop-file/activation removal and `recover --expected-fault` remain
independent of Telegram. Legacy `/stop` generations from the combined text
runtime require the existing local `resume-openings` proof. The panel cannot
clear those legacy stops or reconciliation faults.

Changing a protected config/authorization invalidates activation and startup
identity bindings. To rotate an expired grant, first pause and reconcile all open
orders to terminal states, preserve backups and financial journal, and provision
new empty scanner/control paths under independent host approval. Preserve signal
receipt custody and execution journal paths. Install the separately reviewed new
activation and grant. With services quiesced and the execution writer lease held,
use local `rotate-control-authorization --config ... --expected-control-identity
SHA256_OF_OLD_IDENTITY`. The identity is canonical JSON recorded in
`execution_state.control_identity`; hash it in the protected local review.
This operation retains old receipts, every fill and any sticky fault, revokes
unused confirmations and resets the new grant to paused SIGNALS. It cannot be
called from Telegram. The read-only account preflight must then pass before
any resume. Never reset or restore an old execution journal to rotate a grant.
