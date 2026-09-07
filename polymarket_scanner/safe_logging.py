from __future__ import annotations

import logging
import os
import re
import threading

_BOT_PATH = re.compile(r"/bot[^/\s]+/", re.I)
_INSTALLED = False
_LOCK = threading.Lock()
_ORIGINAL_FACTORY = logging.getLogRecordFactory()


def redact_secret_text(value: object) -> str:
    """Return log-safe text with Telegram Bot API credentials removed."""
    text = str(value)
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    if token:
        text = text.replace(token, "<redacted-bot-token>")
    text = _BOT_PATH.sub("/bot<redacted>/", text)
    return text


def install_secret_safe_logging() -> None:
    """Install defense-in-depth logging redaction once per process.

    httpx INFO access logs include complete request URLs; Telegram places the bot
    token in the URL path. Production normally suppresses those loggers already,
    but the record factory also sanitizes any accidental legacy/custom log record
    before it reaches journald or another handler.
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
