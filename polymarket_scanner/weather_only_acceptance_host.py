from __future__ import annotations

"""Read-only Linux resource sampling for future weather W7 acceptance.

Only /proc files are read.  This helper does not start/stop services, open sockets,
write evidence, or infer latency metrics.  It reports the process high-water RSS,
host MemAvailable and swap currently used so the eventual recorder can populate the
already-frozen W7 resource fields without shell parsing or bool/int coercion.
"""

from dataclasses import asdict, dataclass, field
from pathlib import Path


WEATHER_W7_HOST_METRICS_VERSION = "weather_w7_linux_proc_metrics_v1_hwm_memavailable_swap"


class WeatherW7HostMetricsError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


def _parse_kib_file(text: str, *, required: frozenset[str], prefix: str) -> dict[str, int]:
    if not isinstance(text, str) or not text:
        raise WeatherW7HostMetricsError(f"{prefix}_EMPTY")
    values: dict[str, int] = {}
    for raw in text.splitlines():
        if ":" not in raw:
            continue
        key, rest = raw.split(":", 1)
        key = key.strip()
        if key not in required:
            continue
        if key in values:
            raise WeatherW7HostMetricsError(f"{prefix}_DUPLICATE_FIELD")
        parts = rest.strip().split()
        if len(parts) != 2 or parts[1] != "kB":
            raise WeatherW7HostMetricsError(f"{prefix}_UNIT_INVALID")
        try:
            number = int(parts[0], 10)
        except ValueError:
            raise WeatherW7HostMetricsError(f"{prefix}_NUMBER_INVALID") from None
        if number < 0:
            raise WeatherW7HostMetricsError(f"{prefix}_NUMBER_INVALID")
        values[key] = number
    if set(values) != set(required):
        raise WeatherW7HostMetricsError(f"{prefix}_FIELD_MISSING")
    return values


@dataclass(frozen=True, slots=True)
class WeatherW7HostMetrics:
    version: str
    process_rss_bytes: int
    swap_used_bytes: int
    host_mem_available_bytes: int
    financial_authority: bool = field(init=False, default=False)
    financial_delivery: bool = field(init=False, default=False)
    automatic_order_placement: bool = field(init=False, default=False)

    def as_dict(self) -> dict:
        return asdict(self)


def parse_linux_proc_metrics(*, meminfo_text: str, status_text: str) -> WeatherW7HostMetrics:
    mem = _parse_kib_file(
        meminfo_text,
        required=frozenset({"MemAvailable", "SwapTotal", "SwapFree"}),
        prefix="W7_MEMINFO",
    )
    status = _parse_kib_file(
        status_text,
        required=frozenset({"VmRSS", "VmHWM"}),
        prefix="W7_STATUS",
    )
    if mem["SwapFree"] > mem["SwapTotal"]:
        raise WeatherW7HostMetricsError("W7_SWAP_FREE_EXCEEDS_TOTAL")
    # VmHWM is the process resident-set high-water mark. max() protects against a
    # transient/kernel-report inconsistency without ever understating observed RSS.
    process_kib = max(status["VmRSS"], status["VmHWM"])
    return WeatherW7HostMetrics(
        version=WEATHER_W7_HOST_METRICS_VERSION,
        process_rss_bytes=process_kib * 1024,
        swap_used_bytes=(mem["SwapTotal"] - mem["SwapFree"]) * 1024,
        host_mem_available_bytes=mem["MemAvailable"] * 1024,
    )


def read_linux_proc_metrics(proc_root: str | Path = "/proc") -> WeatherW7HostMetrics:
    root = Path(proc_root)
    try:
        meminfo = (root / "meminfo").read_text(encoding="utf-8")
        status = (root / "self" / "status").read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        raise WeatherW7HostMetricsError("W7_PROC_READ_FAILED") from None
    return parse_linux_proc_metrics(meminfo_text=meminfo, status_text=status)
