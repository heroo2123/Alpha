import logging

from polymarket_scanner.safe_logging import redact_secret_text


def test_redact_secret_text_removes_bot_api_path_and_env_token(monkeypatch):
    token = "123456789:ABC_DEF-secret"
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", token)
    raw = f"POST https://api.telegram.org/bot{token}/sendMessage token={token}"
    safe = redact_secret_text(raw)
    assert token not in safe
    assert "/bot<redacted>/sendMessage" in safe
    assert "<redacted-bot-token>" in safe


def test_process_logging_factory_never_emits_telegram_token(monkeypatch, caplog):
    token = "987654321:XYZ-secret"
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", token)
    logger = logging.getLogger("polybot.test.secret")
    with caplog.at_level(logging.WARNING):
        logger.warning("request failed at https://api.telegram.org/bot%s/sendMessage", token)
    rendered = "\n".join(record.getMessage() for record in caplog.records)
    assert token not in rendered
    assert "/bot<redacted>/sendMessage" in rendered
