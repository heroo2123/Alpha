"""Verify rendered units in a disposable directory; never install/start services."""
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile


def main():
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("render_shadow_units", root / "deploy/render-shadow-units.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with tempfile.TemporaryDirectory(prefix="alpha-unit-verification-") as temporary:
        directory = Path(temporary)
        app, config = directory / "app", directory / "config"
        (app / ".venv/bin").mkdir(parents=True)
        (app / ".venv/bin/python").symlink_to(sys.executable)
        (config / "universe").mkdir(parents=True)
        rendered = module.render(app, config, "alpha")
        assert set(rendered) == {
            "polymarket-edge-scanner.service",
            "polymarket-edge-command.service",
            "polymarket-universe-builder.service",
            "polymarket-weather-shadow.service",
            "polymarket-weather-calibration.service",
            "polymarket-shadow.slice",
        }

        weather = rendered["polymarket-weather-shadow.service"]
        assert "-m polymarket_scanner.weather_only_runtime --loop --interval-seconds 300" in weather
        assert "--output /var/lib/polymarket-weather-shadow/status.json" in weather
        assert "verify-runtime-release.sh" in weather
        assert "MemoryHigh=320M" in weather and "MemoryMax=350M" in weather
        assert "MemorySwapMax=0" in weather
        assert "StateDirectory=polymarket-weather-shadow" in weather
        assert "StateDirectoryMode=0700" in weather
        assert "ReadWritePaths=/var/lib/polymarket-weather-shadow" in weather
        assert "EnvironmentFile=" not in weather
        assert "bot.env" not in weather
        assert "TELEGRAM" not in weather.upper()
        assert "command_worker" not in weather
        assert "app_trade_only" not in weather
        assert "NoNewPrivileges=true" in weather
        assert "ProtectSystem=strict" in weather
        assert "ProtectHome=read-only" in weather
        assert "ProtectHome=true" not in weather
        assert "PrivateDevices=true" in weather
        assert "CapabilityBoundingSet=\n" in weather

        calibration = rendered["polymarket-weather-calibration.service"]
        assert "-m polymarket_scanner.weather_only_calibration_worker_runtime --loop --interval-seconds 30" in calibration
        assert "-m polymarket_scanner.weather_only_calibration_worker --loop" not in calibration
        assert "-m polymarket_scanner.weather_calibration_service_preflight" in calibration
        assert "--release-file" in calibration and "--app-dir" in calibration and "--db" in calibration
        assert "verify-runtime-release.sh" in calibration
        assert "MemoryMax=128M" in calibration
        assert "MemorySwapMax=0" in calibration
        assert "StateDirectory=polymarket-weather-calibration" in calibration
        assert "ReadWritePaths=/var/lib/polymarket-weather-calibration" in calibration
        assert "EnvironmentFile=" not in calibration
        assert "bot.env" not in calibration
        assert "TELEGRAM" not in calibration.upper()
        assert "command_worker" not in calibration
        assert "app_trade_only" not in calibration
        assert "StateDirectoryMode=0700" in calibration
        assert "NoNewPrivileges=true" in calibration
        assert "ProtectSystem=strict" in calibration
        assert "ProtectHome=read-only" in calibration
        assert "ProtectHome=true" not in calibration

        paths = []
        for name, body in rendered.items():
            path = directory / name
            path.write_text(body)
            paths.append(str(path))
        subprocess.run(["systemd-analyze", "verify", *paths], check=True, timeout=30)
    print("Six rendered systemd units verified; weather scanner/calibration use strict public-data runtimes with readable immutable home checkout; nothing installed or started.")


if __name__ == "__main__":
    main()
