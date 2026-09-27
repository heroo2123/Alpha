"""Policy checks for operator safety reductions in a nonfinancial namespace.

This helper checks a caller-supplied numeric actor against an allowlist; it does
not authenticate that actor or provide a protected command endpoint. A trusted
upstream adapter must authenticate the sender, retain original command timestamps,
and bind a protected policy/account to this store before calling ``route``. Actor
identity, timestamps and policy must not be taken from untrusted command fields.

The helper checks scope/action ceilings, ACCOUNT target equality and freshness,
then invokes the existing durable, replay-safe, monotonic-only
``event_risk.SafetyReductions.apply``. It does not retain a transport-authentication
receipt. The command window is checked on every call, including retries; the
underlying reduction remains durable after that window expires.

No transport or candidate entry point currently calls this helper. Authenticated
transport integration, real delivery and independent operational acceptance remain
open. The helper cannot restore posture or grant financial authority.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import time

from .evidence import EvidenceError, EvidenceStore, identity
from .event_risk import ACTIONS, SafetyReductions

SCOPES = frozenset({'ACCOUNT', 'CITY', 'STATION', 'EVENT'})


class OperatorSafetyError(EvidenceError):
    pass


@dataclass(frozen=True)
class OperatorSafetyPolicy:
    """A preauthorized ceiling; it can never be raised by an accepted command."""

    account_id: str
    operators: tuple[int, ...]
    allowed_scopes: tuple[str, ...]
    allowed_actions: tuple[str, ...]
    max_command_age_seconds: float = 120.0

    def __post_init__(self):
        identity(self.account_id)
        if (not isinstance(self.operators, tuple) or not 1 <= len(self.operators) <= 10
                or any(type(x) is not int or x <= 0 for x in self.operators)
                or len(set(self.operators)) != len(self.operators)):
            raise OperatorSafetyError('OPERATOR_ALLOWLIST_INVALID')
        if (not isinstance(self.allowed_scopes, tuple) or not self.allowed_scopes
                or any(x not in SCOPES for x in self.allowed_scopes)
                or len(set(self.allowed_scopes)) != len(self.allowed_scopes)):
            raise OperatorSafetyError('ALLOWED_SCOPES_INVALID')
        if (not isinstance(self.allowed_actions, tuple) or not self.allowed_actions
                or any(x not in ACTIONS for x in self.allowed_actions)
                or len(set(self.allowed_actions)) != len(self.allowed_actions)):
            raise OperatorSafetyError('ALLOWED_ACTIONS_INVALID')
        if type(self.max_command_age_seconds) not in (int, float) or not 0 < self.max_command_age_seconds <= 120:
            raise OperatorSafetyError('MAX_COMMAND_AGE_INVALID')

    def authorize(self, *, actor, scope, action):
        if type(actor) is not int or actor not in self.operators:
            raise OperatorSafetyError('OPERATOR_NOT_AUTHORIZED')
        if scope not in self.allowed_scopes:
            raise OperatorSafetyError('SCOPE_NOT_PREAUTHORIZED')
        if action not in self.allowed_actions:
            raise OperatorSafetyError('ACTION_NOT_PREAUTHORIZED')


class OperatorSafetyRouter:
    """Authorize reductions for an operator authenticated by a trusted caller."""

    def __init__(self, store: EvidenceStore, policy: OperatorSafetyPolicy, *, clock=time.time):
        self.store, self.policy, self.clock = store, policy, clock

    def route(self, command_id: str, *, actor: int, scope: str, scope_id: str,
              action: str, reason: str, created: float, expires: float) -> dict:
        self.policy.authorize(actor=actor, scope=scope, action=action)
        if scope == 'ACCOUNT' and scope_id != self.policy.account_id:
            raise OperatorSafetyError('ACCOUNT_SCOPE_MISMATCH')
        for value in (created, expires):
            if type(value) not in (int, float) or not math.isfinite(value):
                raise OperatorSafetyError('COMMAND_TIME_INVALID')
        now = self.clock()
        if not created <= now < expires <= created + self.policy.max_command_age_seconds:
            raise OperatorSafetyError('COMMAND_EXPIRED_OR_BACKDATED')
        return SafetyReductions(self.store).apply(
            command_id, scope=scope, scope_id=identity(scope_id), action=action,
            actor=str(actor), reason=identity(reason))
