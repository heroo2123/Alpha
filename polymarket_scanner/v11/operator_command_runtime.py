"""Bounded candidate-runner job that authenticates and routes Telegram operator
safety commands into the same protected account as the finite PAPER candidate.

The poller, adapter and router were each independently tested but reachable
from nothing shaped like the actual PAPER candidate runner: no caller checked
that a supplied ``OperatorSafetyPolicy`` actually protects the same account the
candidate itself runs. This binds that check once, here, rather than trusting
a separately configured account id, so a routed command can only ever reduce
risk on the runner's own protected account.

The candidate owns one bounded polling coroutine alongside ordinary work so
collection waits and opening-clock gates cannot suppress safety input. Polls
retain the existing command freshness checks and durable cursor; shutdown drains
that coroutine. The poller's bot lock retains a consumer binding across polls
and restarts; a different store, worker or identity/policy in the same directory
is refused before polling. This is local consistency, not protected configuration
custody or exclusion of older/uncooperative controllers. ``handoff_bot_owner``
exposes the poller's reviewed ownership transfer here so a deliberate binding
change never needs raw lock-file deletion. Real delivery/deployment, credential
provisioning, cross-directory/cross-host ownership and independent operational
acceptance remain unclaimed.
"""
from __future__ import annotations

from .evidence import EvidenceError, digest
from .operator_command_adapter import TelegramOperatorCommandAdapter
from .operator_command_poller import TelegramOperatorCommandPoller
from .operator_safety_router import OperatorSafetyPolicy, OperatorSafetyRouter


class CandidateOperatorCommands:
    """Binds one authenticated Telegram command poller to one protected account."""

    def __init__(self, store, account_id, worker_key, *, telegram, identity, policy):
        if not isinstance(policy, OperatorSafetyPolicy) or policy.account_id != account_id:
            raise EvidenceError('OPERATOR_COMMANDS_ACCOUNT_MISMATCH')
        router = OperatorSafetyRouter(store, policy)
        self.adapter = TelegramOperatorCommandAdapter(telegram, identity, router)
        self.poller = TelegramOperatorCommandPoller(store, worker_key, self.adapter)
        self.account_id = account_id
        self.config = digest(dict(
            account=account_id, worker_key=worker_key,
            identity=dict(bot_id=identity.bot_id, chat_id=identity.chat_id, operators=list(identity.operators)),
            policy=dict(account_id=policy.account_id, operators=list(policy.operators),
                        allowed_scopes=list(policy.allowed_scopes), allowed_actions=list(policy.allowed_actions),
                        max_command_age_seconds=policy.max_command_age_seconds)))

    async def step(self):
        outcomes = await self.poller.step()
        return dict(outcome='OPERATOR_COMMANDS_POLLED' if outcomes else 'NO_OPERATOR_COMMANDS',
                    financial_authority=False, outcomes=outcomes)

    def handoff_bot_owner(self, *, previous_identity, previous_policy, reason: str) -> dict:
        """Reviewed same-cursor rotation; caller authorization remains external."""
        return self.poller.handoff_bot_owner(
            previous_identity=previous_identity, previous_policy=previous_policy, reason=reason)
