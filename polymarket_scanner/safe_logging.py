from __future__ import annotations

import logging
import os
import re
import threading

_BOT_PATH = re.compile(r"/bot[^/\s]+/", re.I)
_QUERY_SECRET = re.compile(
    r"(?i)(?P<prefix>(?:[?&]|\b)(?:token|apikey|api_key)=)(?P<value>[^&#\s]+)"
)
_INSTALLED = False
_LOCK = threading.Lock()
_ORIGINAL_FACTORY = logging.getLogRecordFactory()


def redact_secret_text(value: object) -> str:
    """Return log-safe text with URL/query credentials removed.

    Telegram embeds the bot token in the URL path.  The WRH browser bridge embeds its
    ephemeral Synoptic credential in a ``token=`` query parameter.  Keep both classes
    fail-safe even if an HTTP logger is accidentally re-enabled later.
    """
    text = str(value)
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    if token:
        text = text.replace(token, "<redacted-bot-token>")
    text = _BOT_PATH.sub("/bot<redacted>/", text)
    text = _QUERY_SECRET.sub(
        lambda match: f"{match.group('prefix')}<redacted-query-secret>",
        text,
    )
    return text


def install_secret_safe_logging() -> None:
    """Install defense-in-depth logging redaction once per process.

    httpx INFO access logs can include complete request URLs. Production suppresses
    those loggers already, but the record factory also sanitizes accidental legacy or
    custom records before they reach journald or another handler.
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
