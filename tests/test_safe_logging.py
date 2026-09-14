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
    assert "token=<redacted-query-secret>" in safe


def test_redact_secret_text_removes_query_credentials_without_touching_names():
    raw = (
        "GET https://api.synopticdata.com/v2/stations/timeseries?stid=KLGA&token=wrh-secret "
        "https://example.test/x?apiKey=provider-secret&api_key=second-secret"
    )
    safe = redact_secret_text(raw)
    for secret in ("wrh-secret", "provider-secret", "second-secret"):
        assert secret not in safe
    assert "token=<redacted-query-secret>" in safe
    assert "apiKey=<redacted-query-secret>" in safe
    assert "api_key=<redacted-query-secret>" in safe
    assert "stid=KLGA" in safe


def test_redact_secret_text_covers_bare_query_assignment_in_exception_text():
    safe = redact_secret_text("request failed: token=bare-secret apikey=also-secret")
    assert "bare-secret" not in safe
    assert "also-secret" not in safe
    assert "token=<redacted-query-secret>" in safe
    assert "apikey=<redacted-query-secret>" in safe


def test_process_logging_factory_never_emits_telegram_token(monkeypatch, caplog):
    token = "987654321:XYZ-secret"
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", token)
    logger = logging.getLogger("polybot.test.secret")
    with caplog.at_level(logging.WARNING):
        logger.warning("request failed at https://api.telegram.org/bot%s/sendMessage", token)
    rendered = "\n".join(record.getMessage() for record in caplog.records)
    assert token not in rendered
    assert "/bot<redacted>/sendMessage" in rendered


def test_process_logging_factory_never_emits_wrh_query_token(caplog):
    logger = logging.getLogger("polybot.test.wrh.secret")
    secret = "ephemeral-wrh-browser-token"
    with caplog.at_level(logging.WARNING):
        logger.warning(
            "request failed at https://api.synopticdata.com/v2/stations/timeseries?stid=KLGA&token=%s",
            secret,
        )
    rendered = "\n".join(record.getMessage() for record in caplog.records)
    assert secret not in rendered
    assert "token=<redacted-query-secret>" in rendered
