"""Explicit dedicated Deposit Wallet Owner adapter; not restricted Session custody.

Uses the existing type-3 order/signature implementation without a Session
signer envelope. It never deploys a wallet, creates credentials, invokes a
relayer, or initiates funding/approval/redemption. Operator provisioning is
separate. Full Owner key custody can expose the entire dedicated wallet.
"""
from __future__ import annotations

import math
import time

from .chain import ExchangeError, number, uint
from .config import OWNER_CUSTODY_ACK, SESSION_OPENING_SAFETY_SECONDS
from .exchange import (ExchangeEOA, _deposit_wallet_owner_forms,
                       deposit_wallet_typed_order, wrap_deposit_wallet_signature)
from .wallet_attestation import attest_wallet_identity, MAX_ATTESTATION_SECONDS


class ExchangeDepositOwner(ExchangeEOA):
    wallet_type = "DEPOSIT_WALLET"
    signature_type = 3
    signer_type = "OWNER"
    order_visibility = "OWNER_SIGNER_ONLY"

    def __init__(self, *, private_key, api_key, api_secret, api_passphrase,
                 wallet, signer, deposit_owner, owner_custody_ack, wallet_exclusive_until,
                 transport=None, chain=None, rpc_url=None, clock=time.time, fee_policy=None):
        if owner_custody_ack != OWNER_CUSTODY_ACK:
            raise ExchangeError("EXPLICIT_OWNER_CUSTODY_ACKNOWLEDGEMENT_REQUIRED")
        if type(wallet_exclusive_until) not in (int, float):
            raise ExchangeError("DEPOSIT_OWNER_EXCLUSIVITY_INVALID")
        try:
            self.wallet_exclusive_until = float(wallet_exclusive_until)
        except (ValueError, OverflowError):
            raise ExchangeError("DEPOSIT_OWNER_EXCLUSIVITY_INVALID") from None
        if not math.isfinite(self.wallet_exclusive_until) or self.wallet_exclusive_until <= 0:
            raise ExchangeError("DEPOSIT_OWNER_EXCLUSIVITY_INVALID")
        self._init_authenticated(private_key=private_key, api_key=api_key,
            api_secret=api_secret, api_passphrase=api_passphrase, wallet=wallet,
            signer=signer, transport=transport, chain=chain, rpc_url=rpc_url,
            clock=clock, fee_policy=fee_policy)
        from .chain import address
        self.deposit_owner = address(deposit_owner)
        if self.signer != self.deposit_owner or self.wallet == self.signer:
            raise ExchangeError("DEPOSIT_OWNER_SIGNER_IDENTITY_INVALID")
        forms = _deposit_wallet_owner_forms(self.deposit_owner)
        matches = [kind for kind, target in forms.items() if target == self.wallet]
        if len(matches) != 1:
            raise ExchangeError("DEPOSIT_WALLET_OWNER_BINDING_MISMATCH")
        self.expected_proxy_type = matches[0]

    def _owner_attestation(self):
        wall, mono = self.clock(), time.monotonic()
        block = self.chain.block("latest")
        evidence = attest_wallet_identity(self.chain, self.wallet, self.deposit_owner,
            expected_proxy_type=self.expected_proxy_type, block=block)
        self.chain.confirm_block(block)
        if not -30 <= self.clock() - block["timestamp"] <= 180:
            raise ExchangeError("STALE_CHAIN_BLOCK")
        if (not 0 <= self.clock() - wall <= MAX_ATTESTATION_SECONDS
                or not 0 <= time.monotonic() - mono <= MAX_ATTESTATION_SECONDS):
            raise ExchangeError("DEPOSIT_WALLET_ATTESTATION_TOO_SLOW")
        return evidence

    def owner_opening_restriction(self):
        try:
            self._owner_attestation()
        except ExchangeError as exc:
            return exc.code
        if self.clock() >= self.wallet_exclusive_until - SESSION_OPENING_SAFETY_SECONDS:
            return "DEDICATED_OWNER_EXCLUSIVITY_EXPIRED_OR_NEAR_EXPIRY"
        return None

    def _headers(self, method: str, path: str, body: bytes | None = None) -> dict:
        """Authenticate L2 requests as the Deposit Wallet, not its Owner EOA.

        Poly1271 order payloads identify the Deposit Wallet as maker+signer. A CLOB
        API key usable for this adapter must therefore be bound to that same wallet.
        The Owner EOA remains the private signing key inside the EIP-1271 wrapper;
        it must never be substituted as the L2 account identity. Eligibility's
        authenticated /auth/api-keys read proves the supplied credentials are
        accepted under this wallet identity before openings can gain authority.
        """
        headers = super()._headers(method, path, body)
        headers["POLY_ADDRESS"] = self.wallet
        return headers

    def _submission_opening_restriction(self):
        return self.owner_opening_restriction()

    def eligibility(self, *, require_opening=True):
        result = super().eligibility(require_opening=False)
        restrictions = list(result["opening_restrictions"])
        evidence = None
        try:
            evidence = self._owner_attestation()
        except ExchangeError as exc:
            restrictions.append(exc.code)
        if self.clock() >= self.wallet_exclusive_until - SESSION_OPENING_SAFETY_SECONDS:
            restrictions.append("DEDICATED_OWNER_EXCLUSIVITY_EXPIRED_OR_NEAR_EXPIRY")
        if restrictions and require_opening:
            raise ExchangeError(restrictions[0])
        return dict(result, openings_allowed=not restrictions,
            opening_restrictions=list(dict.fromkeys(restrictions)),
            wallet_attestation=evidence, signer_type="OWNER",
            wallet_exclusive_until=self.wallet_exclusive_until,
            custody="FULL_OWNER_KEY_DEDICATED_WALLET_NOT_WITHDRAWAL_RESTRICTED")

    def prepare_buy(self, *, token, condition, quantity, price, order_type, fee_cap,
                    expiration=0, valid_until=None, post_only=None):
        cutoff = self.wallet_exclusive_until - SESSION_OPENING_SAFETY_SECONDS
        if self.clock() >= cutoff:
            raise ExchangeError("DEDICATED_OWNER_EXCLUSIVITY_EXPIRED_OR_NEAR_EXPIRY")
        if order_type == "GTD" and uint(expiration) > int(cutoff):
            raise ExchangeError("ORDER_OUTLIVES_OWNER_OPENING_WINDOW")
        deadline = cutoff if valid_until is None else min(float(number(valid_until)), cutoff)
        return super().prepare_buy(token=token, condition=condition, quantity=quantity,
            price=price, order_type=order_type, fee_cap=fee_cap, expiration=expiration,
            valid_until=deadline, post_only=post_only)

    def _sign_order(self, order, exchange):
        try:
            from eth_account.messages import encode_typed_data
            typed = deposit_wallet_typed_order(order, exchange)
            inner = self._account.sign_message(encode_typed_data(full_message=typed))
            return "0x" + wrap_deposit_wallet_signature(typed, bytes(inner.signature)).hex()
        except Exception:
            raise ExchangeError("ORDER_SIGNING_FAILED") from None

    def redemption_receipt(self, transaction_hash, condition):
        from .deposit_redemption import read_deposit_redemption
        return read_deposit_redemption(self.chain, transaction_hash,
            wallet=self.wallet, owner=self.deposit_owner, condition=condition)
