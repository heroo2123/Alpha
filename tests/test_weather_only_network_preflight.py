from __future__ import annotations

from pathlib import Path

from polymarket_scanner.weather_only_network_preflight import (
    NETWORK_PREFLIGHT_VERSION,
    NetworkProbe,
    evaluate_network_probes,
)


def _probe(name: str, *, ok: bool, required: bool = True) -> NetworkProbe:
    return NetworkProbe(
        name=name,
        host=f"{name}.example",
        required=required,
        ok=ok,
        detail="fixture",
        ipv4_dns=True,
        ipv6_dns=False,
        elapsed_seconds=0.1,
    )


def test_required_provider_failure_blocks_deployment_network_gate():
    report = evaluate_network_probes(
        (
            _probe("polymarket_gamma", ok=True),
            _probe("open_meteo_gefs", ok=False),
            _probe("telegram_transport", ok=True),
        ),
        checked_at=100.0,
    )
    assert report.version == NETWORK_PREFLIGHT_VERSION
    assert report.required_passed is False
    assert report.financial_authority is False
    assert report.automatic_order_placement is False
    assert report.telegram_message_sent is False


def test_optional_failure_does_not_override_required_success():
    report = evaluate_network_probes(
        (
            _probe("required", ok=True),
            _probe("diagnostic", ok=False, required=False),
        ),
        checked_at=100.0,
    )
    assert report.required_passed is True


def test_preflight_runs_before_backup_or_service_install():
    text = Path("deploy/preflight-weather-paper-deployment.sh").read_text(encoding="utf-8")
    network = text.index("check-weather-paper-network.py")
    backup = text.index("pre-release-weather-paper-backup.sh")
    install = text.index("setup-weather-paper-service.sh")
    assert network < backup < install
    assert "weather-paper-network-preflight.json" in text


def test_network_preflight_uses_exact_guarded_three_layer_transports_and_never_sends_telegram():
    source = Path("polymarket_scanner/weather_only_network_preflight.py").read_text(encoding="utf-8")
    assert "OPEN_METEO_ENSEMBLE" in source
    assert "GuardedNWSNearTermGridClient" in source
    assert "GuardedSameDayStationMetadataClient" in source
    assert "GuardedNWSWRHLiveClient" in source
    assert "GuardedOpenMeteoGEFSHourlyClient" in source
    assert "wrh.close()" in source
    assert "station_meta.close()" in source
    assert "nws_station_metadata" in source
    assert "station_metadata_action" in source
    assert "polymarket_gamma" in source
    assert "polymarket_clob" in source
    assert "open_meteo_gefs" in source
    assert "telegram_transport" in source
    # Do not silently regress this deployment gate to the unguarded transport classes.
    assert "near = NWSNearTermGridClient()" not in source
    assert "wrh = NWSWRHLiveClient(" not in source
    assert "gefs = OpenMeteoGEFSHourlyClient()" not in source
    for forbidden in (
        "sendMessage",
        "TELEGRAM_BOT_TOKEN",
        "PRIVATE_KEY",
        "create_order(",
        "post_order(",
    ):
        assert forbidden not in source
