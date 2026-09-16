from pathlib import Path

from polymarket_scanner.weather_only_live_paper_three_layer_validation import (
    THREE_LAYER_CAPTURE_JSON_BYTES_CAP,
)


def test_weather_paper_backup_retention_is_consistently_bounded_for_three_layer_db():
    pre_release = Path("deploy/pre-release-weather-paper-backup.sh").read_text(encoding="utf-8")
    setup = Path("deploy/setup-weather-paper-backup-service.sh").read_text(encoding="utf-8")

    assert 'WEATHER_PAPER_BACKUP_RETENTION_DAYS:-3' in pre_release
    assert 'Environment=WEATHER_PAPER_BACKUP_RETENTION_DAYS=3' in setup
    assert 'WEATHER_PAPER_BACKUP_RETENTION_DAYS=14' not in setup


def test_three_layer_capture_byte_cap_remains_one_gib_or_less():
    # Backup retention is sized around a bounded research evidence store. If this cap
    # is ever raised, disk/backup capacity must be re-reviewed rather than silently
    # growing the daily backup footprint on the e2-micro.
    assert THREE_LAYER_CAPTURE_JSON_BYTES_CAP <= 1024 * 1024 * 1024
