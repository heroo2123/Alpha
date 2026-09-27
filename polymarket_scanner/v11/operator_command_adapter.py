"""Authenticated Telegram-command adapter for ``OperatorSafetyRouter``.

``OperatorSafetyRouter.route`` trusts its caller for actor identity, command
timestamps and target scope; it explicitly does not authenticate a sender.
This adapter is that missing authenticated caller: it reuses the existing,
already-tested private-chat identity check
(``production.telegram.Telegram.principal``) to bind a real bot/chat/operator
identity, parses one strict command grammar out of the authenticated message
text, and derives the command id and timestamps from that message's own
envelope (chat id, message id, message date) rather than any client-supplied
field. Only a scope/action/reason parsed from the authenticated text and the
actor id returned by ``principal`` ever reach the router.

Retries are not deduplicated here: Telegram redelivers the same update with an
unchanged ``message_id``/``date``, so the derived command id and timestamps
are identical on retry and ``OperatorSafetyRouter``/``SafetyReductions``
already return the original recorded result for a replayed command id with
matching parameters, and reject one with changed parameters. Callback-query
updates (inline button presses) are out of scope here: their message belongs
to the bot, not the operator, so a button-based envelope needs its own binding
and is not implemented by this text-command path.

Real Telegram network delivery, its credential and independent operational
acceptance remain separate and are not performed or assumed here.
"""
from __future__ import annotations

from dataclasses import dataclass
import re

from .operator_safety_router import OperatorSafetyError, OperatorSafetyRouter

COMMAND_PATTERN = re.compile(
    r'^/(?P<action>[A-Z][A-Z_]*)\s+(?P<scope>ACCOUNT|CITY|STATION|EVENT)\s+(?P<scope_id>\S+)\s+(?P<reason>\S.*)$',
    re.DOTALL,
)


class OperatorCommandError(OperatorSafetyError):
    pass


@dataclass(frozen=True)
class TelegramCommandIdentity:
    """The real, out-of-band-configured bot/chat/operator identity this adapter trusts."""

    bot_id: int
    chat_id: int
    operators: tuple[int, ...]


class TelegramOperatorCommandAdapter:
    """Authenticates one Telegram update, then routes at most one parsed command."""

    def __init__(self, telegram, identity: TelegramCommandIdentity, router: OperatorSafetyRouter):
        if telegram.delivery_identity != {"bot_id": str(identity.bot_id), "chat_id": str(identity.chat_id)}:
            raise OperatorCommandError("ADAPTER_TELEGRAM_IDENTITY_MISMATCH")
        if telegram.operators != {str(x) for x in identity.operators}:
            raise OperatorCommandError("ADAPTER_TELEGRAM_OPERATOR_MISMATCH")
        self.telegram, self.identity, self.router = telegram, identity, router

    def handle(self, update: dict) -> dict:
        if not isinstance(update, dict) or update.get("callback_query") is not None:
            raise OperatorCommandError("COMMAND_CALLBACK_NOT_SUPPORTED")
        actor = self.telegram.principal(update, self.identity)
        if actor is None:
            raise OperatorCommandError("UPDATE_NOT_FROM_AUTHENTICATED_OPERATOR")
        message = update.get("message")
        if not isinstance(message, dict):
            raise OperatorCommandError("COMMAND_MESSAGE_MISSING")
        text = message.get("text")
        if not isinstance(text, str):
            raise OperatorCommandError("COMMAND_TEXT_MISSING")
        match = COMMAND_PATTERN.match(text.strip())
        if not match:
            raise OperatorCommandError("COMMAND_GRAMMAR_INVALID")
        chat_id, message_id, created = message.get("chat", {}).get("id"), message.get("message_id"), message.get("date")
        if type(chat_id) is not int or type(message_id) is not int or type(created) not in (int, float):
            raise OperatorCommandError("COMMAND_ENVELOPE_INVALID")
        command_id = f"telegram:{chat_id}:{message_id}"
        expires = created + self.router.policy.max_command_age_seconds
        result = self.router.route(
            command_id, actor=actor, scope=match["scope"], scope_id=match["scope_id"],
            action=match["action"], reason=match["reason"], created=created, expires=expires)
        return {"command_id": command_id, "actor": actor, "scope": match["scope"],
                "action": match["action"], "result": result}
