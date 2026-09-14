from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


SETUP = Path("deploy/setup-weather-paper-service.sh")
EXTRACT = Path("deploy/extract-weather-paper-env.py")


def _run(source: Path, output: Path):
    return subprocess.run(
        [sys.executable, str(EXTRACT), "--source", str(source), "--output", str(output)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def test_setup_executes_dedicated_env_allowlist_helper():
    text = SETUP.read_text(encoding="utf-8")
    assert "deploy/extract-weather-paper-env.py" in text
    assert "grep -E" not in text


def test_extractor_copies_only_telegram_and_optional_pws_key(tmp_path):
    source = tmp_path / "bot.env"
    output = tmp_path / "weather-paper.env"
    source.write_text(
        "\n".join(
            [
                "TELEGRAM_BOT_TOKEN=tg-secret",
                "TELEGRAM_CHAT_ID=12345",
                "WEATHER_PWS_API_KEY=pws-secret",
                "POLYMARKET_PRIVATE_KEY=must-not-copy",
                "BINANCE_API_KEY=must-not-copy-either",
                "GOOGLE_APPLICATION_CREDENTIALS=/tmp/secret.json",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    result = _run(source, output)
    assert result.returncode == 0
    assert result.stdout.strip() == "PASS_WEATHER_PAPER_ENV_ALLOWLIST"
    text = output.read_text(encoding="utf-8")
    assert text.splitlines() == [
        "TELEGRAM_BOT_TOKEN=tg-secret",
        "TELEGRAM_CHAT_ID=12345",
        "WEATHER_PWS_API_KEY=pws-secret",
    ]
    assert "must-not-copy" not in text
    assert output.stat().st_mode & 0o077 == 0


def test_pws_key_is_optional(tmp_path):
    source = tmp_path / "bot.env"
    output = tmp_path / "weather-paper.env"
    source.write_text(
        "TELEGRAM_BOT_TOKEN=tg-secret\nTELEGRAM_CHAT_ID=12345\n",
        encoding="utf-8",
    )
    result = _run(source, output)
    assert result.returncode == 0
    assert "WEATHER_PWS_API_KEY" not in output.read_text(encoding="utf-8")


def test_duplicate_pws_key_is_rejected_without_writing_output(tmp_path):
    source = tmp_path / "bot.env"
    output = tmp_path / "weather-paper.env"
    source.write_text(
        "TELEGRAM_BOT_TOKEN=x\nTELEGRAM_CHAT_ID=y\n"
        "WEATHER_PWS_API_KEY=a\nWEATHER_PWS_API_KEY=b\n",
        encoding="utf-8",
    )
    result = _run(source, output)
    assert result.returncode == 2
    assert result.stdout.strip() == "WEATHER_PWS_API_KEY_DUPLICATED"
    assert not output.exists()


def test_missing_or_duplicate_telegram_credentials_are_rejected(tmp_path):
    cases = [
        "TELEGRAM_CHAT_ID=y\n",
        "TELEGRAM_BOT_TOKEN=x\n",
        "TELEGRAM_BOT_TOKEN=x\nTELEGRAM_BOT_TOKEN=z\nTELEGRAM_CHAT_ID=y\n",
        "TELEGRAM_BOT_TOKEN=x\nTELEGRAM_CHAT_ID=y\nTELEGRAM_CHAT_ID=z\n",
    ]
    for index, text in enumerate(cases):
        source = tmp_path / f"bot-{index}.env"
        output = tmp_path / f"out-{index}.env"
        source.write_text(text, encoding="utf-8")
        result = _run(source, output)
        assert result.returncode == 2
        assert not output.exists()
