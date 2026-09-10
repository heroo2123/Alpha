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
        paths = []
        for name, body in module.render(app, config, "alpha").items():
            path = directory / name
            path.write_text(body)
            paths.append(str(path))
        subprocess.run(["systemd-analyze", "verify", *paths], check=True, timeout=30)
    print("Four rendered systemd units verified; nothing installed or started.")


if __name__ == "__main__":
    main()
