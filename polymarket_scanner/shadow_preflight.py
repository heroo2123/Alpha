"""Fail-closed local startup gate. No network calls or database mutations."""
import json
import os
from pathlib import Path

from .runtime_manifest import build_runtime_manifest
from .trade_only import TRADE_READY_VERSION, promoted_detectors


def attest() -> dict:
    config = Path(os.environ.get("ALPHA_CONFIG_DIR", "~/.polymarket-edge-scanner")).expanduser()
    promoted = promoted_detectors()
    if promoted:
        raise SystemExit("silent-shadow startup blocked: promotion registry is nonempty")
    manifest = build_runtime_manifest(promoted_detectors=promoted, trade_ready_version=TRADE_READY_VERSION,
                                     release_file=config / "release.sha", preflight_file=config / "dependency-preflight.json")
    if not manifest["production_runtime_authority_complete"]:
        raise SystemExit("silent-shadow startup blocked: release/dependency/preflight attestation failed")
    return manifest


def main():
    manifest = attest()
    print(json.dumps({"ready": True, "release_sha": manifest["git_head_sha"], "promotion_count": 0,
                      "financial_delivery": False, "runtime_mode": manifest["nonsecret_safety_policy"]["runtime_mode"]}))


if __name__ == "__main__":
    main()
