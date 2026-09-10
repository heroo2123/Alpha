"""Read-only bootstrap gate. Never starts services, builds universes or sends alerts."""
import argparse
import json
import time

from .shadow_preflight import attest
from .universe_reader import UniverseReader
from .universe_snapshot import BUILD_DEADLINE_SECONDS, snapshot_directory


def ready_snapshot(reader: UniverseReader) -> dict | None:
    prepared = reader.poll()
    if prepared is not None:
        reader.accept(prepared)
    universe, screening = reader.universe_status(), reader.screening_status()
    if not universe["safe_for_detection"] or screening["usable_coverage_ratio"] < .70:
        return None
    return {"ready": True, "generation_id": universe["generation_id"], "producer_sha": universe["producer_sha"],
            "age_seconds": universe["age_seconds"], "screening_coverage": screening["usable_coverage_ratio"],
            "discovered_market_count": universe["discovered_market_count"],
            "materialized_market_count": universe["materialized_market_count"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=float, default=BUILD_DEADLINE_SECONDS)
    args = parser.parse_args()
    if not 0 < args.timeout <= BUILD_DEADLINE_SECONDS:
        parser.error("timeout must be within the fixed 900-second bootstrap budget")
    manifest = attest()
    reader = UniverseReader(snapshot_directory(), producer_sha=manifest["git_head_sha"],
                            priority=lambda market: market.id, weather_universe=lambda markets: ([], []))
    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        result = ready_snapshot(reader)
        if result is not None:
            print(json.dumps(result))
            return
        time.sleep(min(5., max(0., deadline - time.monotonic())))
    print(json.dumps({"ready": False, "failure_code": "BOOTSTRAP_DEADLINE", "last_reader_error": reader.last_error}))
    raise SystemExit(1)


if __name__ == "__main__":
    main()
