from __future__ import annotations

"""Release-bound one-shot runner for the frozen weather W7 acceptance window.

This command attaches to an already-running weather-only SILENT_SHADOW scanner. It
never starts/stops/restarts a service. It adds exact Git/release/cwd provenance before
and after the existing 45-minute recorder window and atomically writes the final
release-bound acceptance artifact.
"""

import argparse
import asyncio
import json
import os
import tempfile
from pathlib import Path

from .weather_only_acceptance_recorder import discover_weather_w7_probe_event
from .weather_only_acceptance_recorder_bounded import WeatherW7BoundedRecorderSession
from .weather_only_acceptance_release import (
    attest_weather_w7_release,
    build_weather_w7_release_manifest,
)
from .weather_only_acceptance_release_bundle import (
    WeatherW7ReleaseBoundBundle,
    build_weather_w7_release_bound_bundle,
    dump_weather_w7_release_bound_bundle_json,
    validate_weather_w7_release_bound_bundle,
)


WEATHER_W7_RUNNER_VERSION = "weather_w7_runner_v2_bounded_source_poll_release_bound_frozen_window"


class WeatherW7RunnerError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


def atomic_write_weather_w7_release_bundle(path: str | Path, bundle: WeatherW7ReleaseBoundBundle) -> None:
    output = Path(path)
    if output.is_symlink():
        raise WeatherW7RunnerError("W7_RUNNER_OUTPUT_PATH_INVALID")
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        raise WeatherW7RunnerError("W7_RUNNER_OUTPUT_DIRECTORY_INVALID") from None
    payload = dump_weather_w7_release_bound_bundle_json(bundle) + "\n"
    temporary_name = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=output.parent,
            prefix=output.name + ".",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_name = handle.name
            os.chmod(temporary_name, 0o600)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, output)
        temporary_name = None
    except OSError:
        raise WeatherW7RunnerError("W7_RUNNER_OUTPUT_WRITE_FAILED") from None
    finally:
        if temporary_name is not None:
            try:
                os.unlink(temporary_name)
            except OSError:
                pass


async def run_weather_w7_release_bound_acceptance(
    *,
    release_sha: str,
    app_dir: str | Path,
    release_file: str | Path,
    scanner_process_id: int,
    database_path: str | Path,
    runtime_report_path: str | Path,
    output_path: str | Path,
    event_id: str | None = None,
) -> WeatherW7ReleaseBoundBundle:
    event, _ = await discover_weather_w7_probe_event(event_id=event_id)
    before_release = attest_weather_w7_release(
        app_dir=app_dir,
        release_file=release_file,
        scanner_process_id=scanner_process_id,
        expected_release_sha=release_sha,
    )
    session = WeatherW7BoundedRecorderSession(
        release_sha=release_sha,
        scanner_process_id=scanner_process_id,
        database_path=database_path,
        runtime_report_path=runtime_report_path,
        probe_event=event,
    )
    try:
        inner = await session.run_frozen_window()
    finally:
        await session.close()
    after_release = attest_weather_w7_release(
        app_dir=app_dir,
        release_file=release_file,
        scanner_process_id=scanner_process_id,
        expected_release_sha=release_sha,
    )
    release_manifest = build_weather_w7_release_manifest(
        before=before_release,
        after=after_release,
    )
    final = build_weather_w7_release_bound_bundle(
        acceptance_bundle=inner,
        release_manifest=release_manifest,
    )
    validate_weather_w7_release_bound_bundle(final, expected_release_sha=release_sha)
    atomic_write_weather_w7_release_bundle(output_path, final)
    return final


async def _run_cli(args) -> int:
    bundle = await run_weather_w7_release_bound_acceptance(
        release_sha=args.release_sha,
        app_dir=args.app_dir,
        release_file=args.release_file,
        scanner_process_id=args.scanner_pid,
        database_path=args.database,
        runtime_report_path=args.runtime_report,
        output_path=args.output,
        event_id=args.event_id,
    )
    report = validate_weather_w7_release_bound_bundle(bundle, expected_release_sha=args.release_sha)
    print(json.dumps({
        "version": WEATHER_W7_RUNNER_VERSION,
        "release_sha": args.release_sha.lower(),
        "output": str(args.output),
        "acceptance": report.as_dict(),
        "financial_authority": False,
        "financial_delivery": False,
        "automatic_order_placement": False,
    }, sort_keys=True, indent=2))
    return 0 if report.passed else 2


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-sha", required=True)
    parser.add_argument("--app-dir", type=Path, required=True)
    parser.add_argument("--release-file", type=Path, required=True)
    parser.add_argument("--scanner-pid", type=int, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--runtime-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--event-id")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_run_cli(args)))


if __name__ == "__main__":
    main()
