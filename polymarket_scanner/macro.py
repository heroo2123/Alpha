from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

from .config import settings

BLS_API = "https://api.bls.gov/publicAPI/v2/timeseries/data/"
SERIES = {
    "cpi": "CUUR0000SA0",
    "core_cpi": "CUUR0000SA0L1E",
    "unemployment": "LNS14000000",
    "payroll_level_k": "CES0000000001",
}


@dataclass(slots=True)
class MacroValue:
    metric: str
    year: int
    month: int
    value: float
    source: str = "U.S. Bureau of Labor Statistics"


class MacroClient:
    """Optional official BLS data feed; disabled when BLS_API_KEY is absent."""
    def __init__(self) -> None:
        self.http = httpx.AsyncClient(timeout=settings.request_timeout, headers={"User-Agent": "polymarket-edge-scanner/0.2"})
        self.values: dict[tuple[str, int, int], MacroValue] = {}
        self.last_refresh: datetime | None = None
        self.last_error: str | None = None

    @property
    def enabled(self) -> bool:
        return bool(settings.bls_api_key)

    async def close(self) -> None:
        await self.http.aclose()

    async def refresh(self) -> None:
        if not self.enabled:
            return
        now = datetime.now(timezone.utc)
        payload = {"seriesid": list(SERIES.values()), "startyear": str(now.year - 1), "endyear": str(now.year), "registrationkey": settings.bls_api_key}
        try:
            r = await self.http.post(BLS_API, json=payload); r.raise_for_status(); data = r.json()
            if data.get("status") != "REQUEST_SUCCEEDED":
                raise RuntimeError("; ".join(data.get("message") or ["BLS request failed"]))
            raw: dict[str, dict[tuple[int, int], float]] = {}
            reverse = {v: k for k, v in SERIES.items()}
            for series in data.get("Results", {}).get("series", []):
                name = reverse.get(series.get("seriesID"))
                if not name:
                    continue
                rows: dict[tuple[int, int], float] = {}
                for item in series.get("data", []):
                    period = str(item.get("period") or "")
                    if not period.startswith("M") or period == "M13":
                        continue
                    try:
                        rows[(int(item["year"]), int(period[1:]))] = float(item["value"])
                    except Exception:
                        continue
                raw[name] = rows
            self.values = {}
            for (year, month), val in raw.get("unemployment", {}).items():
                self.values[("unemployment", year, month)] = MacroValue("unemployment", year, month, val)
            for metric in ("cpi", "core_cpi"):
                for (year, month), val in raw.get(metric, {}).items():
                    prev = raw.get(metric, {}).get((year - 1, month))
                    if prev:
                        yoy = (val / prev - 1.0) * 100.0
                        self.values[(f"{metric}_yoy", year, month)] = MacroValue(f"{metric}_yoy", year, month, yoy)
            levels = raw.get("payroll_level_k", {})
            for (year, month), val in levels.items():
                py, pm = (year - 1, 12) if month == 1 else (year, month - 1)
                prev = levels.get((py, pm))
                if prev is not None:
                    self.values[("payroll_change_k", year, month)] = MacroValue("payroll_change_k", year, month, val - prev)
            self.last_refresh = now; self.last_error = None
        except Exception as exc:
            self.last_error = repr(exc)

    def get(self, metric: str, year: int, month: int) -> MacroValue | None:
        return self.values.get((metric, year, month))

    def status(self) -> dict:
        return {"enabled": self.enabled, "last_refresh": self.last_refresh.isoformat() if self.last_refresh else None, "last_error": self.last_error, "values": len(self.values)}
