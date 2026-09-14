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


def test_redact_secret_text_removes_synoptic_env_and_query_tokens(monkeypatch):
    token = "synoptic-public-token-sentinel"
    monkeypatch.setenv("SYNOPTIC_PWS_TOKEN", token)
    raw = (
        f"GET https://api.synopticdata.com/v2/stations/latest?token={token}&network=65 "
        "fallback=https://example.test/x?apiKey=explicit-other-secret"
    )
    safe = redact_secret_text(raw)
    assert token not in safe
    assert "explicit-other-secret" not in safe
    assert "token=<redacted-api-key>" in safe
    assert "apiKey=<redacted-api-key>" in safe


def test_redact_secret_text_still_protects_superseded_weather_company_key(monkeypatch):
    key = "old-weather-provider-key"
    monkeypatch.setenv("WEATHER_PWS_API_KEY", key)
    safe = redact_secret_text(f"https://api.weather.com/x?apiKey={key}")
    assert key not in safe


def test_process_logging_factory_never_emits_telegram_token(monkeypatch, caplog):
    token = "987654321:XYZ-secret"
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", token)
    logger = logging.getLogger("polybot.test.secret")
    with caplog.at_level(logging.WARNING):
        logger.warning("request failed at https://api.telegram.org/bot%s/sendMessage", token)
    rendered = "\n".join(record.getMessage() for record in caplog.records)
    assert token not in rendered
    assert "/bot<redacted>/sendMessage" in rendered
