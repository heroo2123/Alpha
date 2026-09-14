from __future__ import annotations

import logging
import os
import re
import threading

_BOT_PATH = re.compile(r"/bot[^/\s]+/", re.I)
_CREDENTIAL_QUERY = re.compile(
    r"([?&](?:apiKey|apikey|api_key|token)=)[^&\s]+", re.I
)
_INSTALLED = False
_LOCK = threading.Lock()
_ORIGINAL_FACTORY = logging.getLogRecordFactory()


def redact_secret_text(value: object) -> str:
    """Return log-safe text with Telegram and weather-source credentials removed."""
    text = str(value)
    telegram = os.getenv("TELEGRAM_BOT_TOKEN", "")
    if telegram:
        text = text.replace(telegram, "<redacted-bot-token>")
    # Preserve defense-in-depth for the superseded provider if its key remains in a
    # legacy operator environment, even though it is no longer copied into paper.env.
    old_pws = os.getenv("WEATHER_PWS_API_KEY", "")
    if old_pws:
        text = text.replace(old_pws, "<redacted-pws-key>")
    synoptic = os.getenv("SYNOPTIC_PWS_TOKEN", "")
    if synoptic:
        text = text.replace(synoptic, "<redacted-pws-token>")
    text = _BOT_PATH.sub("/bot<redacted>/", text)
    # Value-independent query redaction also protects explicitly supplied credentials
    # that are not present in the environment.
    text = _CREDENTIAL_QUERY.sub(r"\1<redacted-api-credential>", text)
    return text


def install_secret_safe_logging() -> None:
    """Install defense-in-depth logging redaction once per process.

    HTTP client INFO logs may include complete request URLs. Production suppresses
    those loggers already, but the record factory sanitizes accidental legacy/custom
    log records before they reach journald or another handler.
    """
    global _INSTALLED
    if _INSTALLED:
        return
    with _LOCK:
        if _INSTALLED:
            return

        def factory(*args, **kwargs):
            record = _ORIGINAL_FACTORY(*args, **kwargs)
            try:
                rendered = record.getMessage()
            except Exception:
                rendered = str(record.msg)
            safe = redact_secret_text(rendered)
            record.msg = safe
            record.args = ()
            return record

        logging.setLogRecordFactory(factory)
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)
        _INSTALLED = True
