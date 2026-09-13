from __future__ import annotations

import inspect

from polymarket_scanner import weather_only_live_paper_final as final_runtime
from polymarket_scanner.weather_only_paper_recovery import CrashSafeWeatherPaperStore
from polymarket_scanner.weather_only_paper_recovery_final import (
    FinalCrashSafeWeatherPaperStore,
)


def test_final_recovery_guard_is_stricter_than_base_recovery_store():
    assert issubclass(FinalCrashSafeWeatherPaperStore, CrashSafeWeatherPaperStore)
    assert FinalCrashSafeWeatherPaperStore is not CrashSafeWeatherPaperStore


def test_deployable_runtime_constructs_final_recovery_guard_not_base_store():
    source = inspect.getsource(final_runtime.FinalWeatherLivePaperService.__init__)
    assert "FinalCrashSafeWeatherPaperStore(self.db_path)" in source
    assert "CrashSafeWeatherPaperStore(self.db_path)" not in source.replace(
        "FinalCrashSafeWeatherPaperStore(self.db_path)", ""
    )
    assert final_runtime.FINAL_PAPER_RUNTIME_VERSION == (
        "weather_live_paper_final_v3_exact_recovery_boundary"
    )
