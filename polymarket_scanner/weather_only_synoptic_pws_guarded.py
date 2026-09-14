from __future__ import annotations

"""Deployment guard for Synoptic/CWOP summary-level authentication failures.

Synoptic documents legacy responses where RESPONSE_CODE=2 can represent either true
zero results or an invalid token, distinguished by HTTP_STATUS_CODE/message.  The base
adapter intentionally treats ordinary code 2 as zero results; this deployment wrapper
prevents an invalid/limited credential from being mistaken for a healthy empty PWS
query during preflight or live collection.
"""

from .weather_only_pws import (
    PWS_STATUS_AUTH_ERROR,
    PWS_STATUS_PROVIDER_ERROR,
)
from .weather_only_synoptic_pws import SynopticCWOPPWSClient, _strict_response_code


def _optional_status_code(value: object) -> int | None:
    if value in (None, ""):
        return None
    try:
        return _strict_response_code(value)
    except Exception:
        return -999999


class GuardedSynopticCWOPPWSClient(SynopticCWOPPWSClient):
    """Synoptic client with fail-closed summary authentication classification."""

    async def _json(self, params: dict[str, object]) -> tuple[str, object | None]:
        status, body = await super()._json(params)
        if status != "OK" or not isinstance(body, dict):
            return status, body
        summary = body.get("SUMMARY")
        if not isinstance(summary, dict):
            return status, body

        try:
            response_code = _strict_response_code(summary.get("RESPONSE_CODE"))
        except Exception:
            return status, body
        http_status = _optional_status_code(summary.get("HTTP_STATUS_CODE"))
        message = str(summary.get("RESPONSE_MESSAGE") or "").strip().lower()

        # Official Synoptic error documentation includes invalid-token responses with
        # RESPONSE_CODE=2 plus HTTP_STATUS_CODE=401.  Some service generations also
        # use explicit 200/403 response codes for authentication/authorization errors.
        if http_status in {401, 403} or response_code in {200, 403}:
            return PWS_STATUS_AUTH_ERROR, None
        if response_code == 2 and any(
            marker in message
            for marker in (
                "invalid token",
                "authentication",
                "authorization error",
                "not enabled for this contract",
            )
        ):
            return PWS_STATUS_AUTH_ERROR, None

        # A RESPONSE_CODE=2 with a non-auth HTTP error (for example 429) is not a
        # healthy zero-result response and must not satisfy the deployment preflight.
        if http_status is not None and (http_status == -999999 or http_status >= 400):
            return PWS_STATUS_PROVIDER_ERROR, None
        return status, body
