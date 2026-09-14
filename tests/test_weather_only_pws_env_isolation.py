from pathlib import Path


SETUP = Path("deploy/setup-weather-paper-service.sh")


def test_weather_paper_env_allows_only_telegram_and_optional_pws_key():
    text = SETUP.read_text(encoding="utf-8")
    allowlist = "TELEGRAM_BOT_TOKEN|TELEGRAM_CHAT_ID|WEATHER_PWS_API_KEY"
    assert allowlist in text
    assert "WEATHER_PWS_API_KEY duplicated in bot.env" in text
    for forbidden in (
        "POLYMARKET_PRIVATE_KEY",
        "PRIVATE_KEY",
        "WALLET",
        "BINANCE_API_KEY",
        "BINANCE_SECRET",
        "GOOGLE_APPLICATION_CREDENTIALS",
    ):
        assert forbidden not in text


def test_pws_key_is_optional_but_telegram_credentials_remain_required():
    text = SETUP.read_text(encoding="utf-8")
    assert "TELEGRAM_BOT_TOKEN missing/duplicated in bot.env" in text
    assert "TELEGRAM_CHAT_ID missing/duplicated in bot.env" in text
    assert "WEATHER_PWS_API_KEY missing" not in text
