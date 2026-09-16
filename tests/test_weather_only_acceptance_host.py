from __future__ import annotations

import pytest

from polymarket_scanner.weather_only_acceptance_host import (
    WeatherW7HostMetricsError,
    parse_linux_proc_metrics,
    read_linux_proc_metrics,
)


MEMINFO = """MemTotal:         999999 kB
MemAvailable:     200000 kB
SwapTotal:          2048 kB
SwapFree:           2048 kB
"""
STATUS = """Name:\tpython
VmRSS:\t 100000 kB
VmHWM:\t 120000 kB
"""


def test_proc_metrics_use_rss_high_water_memavailable_and_actual_swap_usage():
    row = parse_linux_proc_metrics(meminfo_text=MEMINFO, status_text=STATUS)
    assert row.process_rss_bytes == 120000 * 1024
    assert row.host_mem_available_bytes == 200000 * 1024
    assert row.swap_used_bytes == 0
    assert row.financial_authority is False
    assert row.financial_delivery is False
    assert row.automatic_order_placement is False


def test_current_rss_wins_if_kernel_hwm_is_unexpectedly_lower():
    row = parse_linux_proc_metrics(
        meminfo_text=MEMINFO,
        status_text="VmRSS: 130000 kB\nVmHWM: 120000 kB\n",
    )
    assert row.process_rss_bytes == 130000 * 1024


def test_swap_usage_is_total_minus_free_and_cannot_go_negative():
    row = parse_linux_proc_metrics(
        meminfo_text="MemAvailable: 200000 kB\nSwapTotal: 4096 kB\nSwapFree: 1024 kB\n",
        status_text=STATUS,
    )
    assert row.swap_used_bytes == 3072 * 1024

    with pytest.raises(WeatherW7HostMetricsError) as raised:
        parse_linux_proc_metrics(
            meminfo_text="MemAvailable: 1 kB\nSwapTotal: 1 kB\nSwapFree: 2 kB\n",
            status_text=STATUS,
        )
    assert raised.value.code == "W7_SWAP_FREE_EXCEEDS_TOTAL"


def test_missing_duplicate_bad_unit_and_bad_number_fail_closed():
    cases = [
        ("MemAvailable: 1 kB\nSwapTotal: 0 kB\n", STATUS, "W7_MEMINFO_FIELD_MISSING"),
        (
            "MemAvailable: 1 kB\nMemAvailable: 1 kB\nSwapTotal: 0 kB\nSwapFree: 0 kB\n",
            STATUS,
            "W7_MEMINFO_DUPLICATE_FIELD",
        ),
        (
            "MemAvailable: 1 MB\nSwapTotal: 0 kB\nSwapFree: 0 kB\n",
            STATUS,
            "W7_MEMINFO_UNIT_INVALID",
        ),
        (
            "MemAvailable: nope kB\nSwapTotal: 0 kB\nSwapFree: 0 kB\n",
            STATUS,
            "W7_MEMINFO_NUMBER_INVALID",
        ),
        (MEMINFO, "VmRSS: 1 kB\n", "W7_STATUS_FIELD_MISSING"),
    ]
    for meminfo, status, code in cases:
        with pytest.raises(WeatherW7HostMetricsError) as raised:
            parse_linux_proc_metrics(meminfo_text=meminfo, status_text=status)
        assert raised.value.code == code


def test_read_linux_proc_metrics_is_read_only_and_testable_with_fake_proc(tmp_path):
    root = tmp_path / "proc"
    (root / "self").mkdir(parents=True)
    (root / "meminfo").write_text(MEMINFO, encoding="utf-8")
    (root / "self" / "status").write_text(STATUS, encoding="utf-8")
    before_mem = (root / "meminfo").read_bytes()
    before_status = (root / "self" / "status").read_bytes()

    row = read_linux_proc_metrics(root)
    assert row.process_rss_bytes == 120000 * 1024
    assert (root / "meminfo").read_bytes() == before_mem
    assert (root / "self" / "status").read_bytes() == before_status


def test_out_of_process_recorder_can_sample_exact_scanner_pid_not_itself(tmp_path):
    root = tmp_path / "proc"
    (root / "self").mkdir(parents=True)
    (root / "4242").mkdir(parents=True)
    (root / "meminfo").write_text(MEMINFO, encoding="utf-8")
    (root / "self" / "status").write_text(
        "VmRSS: 1000 kB\nVmHWM: 2000 kB\n", encoding="utf-8"
    )
    (root / "4242" / "status").write_text(
        "VmRSS: 180000 kB\nVmHWM: 190000 kB\n", encoding="utf-8"
    )

    self_row = read_linux_proc_metrics(root)
    scanner_row = read_linux_proc_metrics(root, process_id=4242)
    assert self_row.process_rss_bytes == 2000 * 1024
    assert scanner_row.process_rss_bytes == 190000 * 1024


@pytest.mark.parametrize("bad_pid", [True, False, 0, -1, 1.5, "4242"])
def test_target_process_id_is_strict_positive_integer(bad_pid, tmp_path):
    with pytest.raises(WeatherW7HostMetricsError) as raised:
        read_linux_proc_metrics(tmp_path, process_id=bad_pid)
    assert raised.value.code == "W7_PROCESS_ID_INVALID"


def test_proc_read_failure_has_fixed_secret_safe_error(tmp_path):
    with pytest.raises(WeatherW7HostMetricsError) as raised:
        read_linux_proc_metrics(tmp_path / "missing")
    assert raised.value.code == "W7_PROC_READ_FAILED"
