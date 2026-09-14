from __future__ import annotations

import logging
import os
import re
import threading

_BOT_PATH = re.compile(r"/bot[^/\s]+/", re.I)
_API_KEY_QUERY = re.compile(r"([?&](?:apiKey|apikey|api_key)=)[^&\s]+", re.I)
_INSTALLED = False
_LOCK = threading.Lock()
_ORIGINAL_FACTORY = logging.getLogRecordFactory()


def redact_secret_text(value: object) -> str:
    """Return log-safe text with Telegram and PWS credentials removed."""
    text = str(value)
    telegram = os.getenv("TELEGRAM_BOT_TOKEN", "")
    if telegram:
        text = text.replace(telegram, "<redacted-bot-token>")
    pws = os.getenv("WEATHER_PWS_API_KEY", "")
    if pws:
        text = text.replace(pws, "<redacted-pws-key>")
    text = _BOT_PATH.sub("/bot<redacted>/", text)
    # This also protects explicitly supplied PWS keys that are not present in env.
    text = _API_KEY_QUERY.sub(r"\1<redacted-api-key>", text)
    return text


def install_secret_safe_logging() -> None:
    """Install defense-in-depth logging redaction once per process.

    HTTP client INFO logs may include complete request URLs. Production suppresses
    those loggers already, but the record factory sanitizes accidental legacy/custom
    log records before they reach journald or another handler. Query-parameter
    redaction is value-agnostic, so explicitly supplied PWS keys are covered too.
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
