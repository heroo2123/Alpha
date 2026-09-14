from __future__ import annotations

from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


new_test = Path("tests/test_weather_only_three_layer_layer2_eligibility.py")
text = new_test.read_text(encoding="utf-8")
text = replace_once(
    text,
    '    async def metadata(compiled): return _meta(compiled.station_hint)\n'
    '    async def supported(*, latitude, longitude): return latitude < 40.05\n'
    '    service = _service(metadata, supported)\n'
    '    rows = ({"id":"a","station":"KAAA"},{"id":"b","station":"KZZZ"})\n',
    '    async def metadata(compiled):\n'
    '        if compiled.station_hint == "KAAA":\n'
    '            return NS(timezone="UTC", latitude=40.0, longitude=-73.0)\n'
    '        return NS(timezone="UTC", latitude=51.47, longitude=-0.46)\n'
    '    async def supported(*, latitude, longitude): return latitude < 50.0\n'
    '    service = _service(metadata, supported)\n'
    '    rows = ({"id":"a","station":"KAAA"},{"id":"b","station":"KZZZ"})\n',
    "new unsupported fixture",
)
new_test.write_text(text, encoding="utf-8")


existing = Path("tests/test_weather_only_three_layer_validation_guarded.py")
text = existing.read_text(encoding="utf-8")
function_start = text.index("def test_more_than_selection_cap_fails_closed_without_partial_sampling")
function_end = text.find("\ndef ", function_start + 5)
if function_end < 0:
    function_end = len(text)
chunk = text[function_start:function_end]
chunk = replace_once(
    chunk,
    "    service._station_metadata_for_compiled = metadata\n"
    "    events = tuple({\"id\": f\"event-{index:02d}\"} for index in range(13))\n",
    "    service._station_metadata_for_compiled = metadata\n"
    "\n"
    "    class NWS:\n"
    "        async def point_supported(self, **_kwargs):\n"
    "            return True\n"
    "\n"
    "    service._same_day_nws = NWS()\n"
    "    service._three_layer_nws_support_cache = {}\n"
    "    events = tuple({\"id\": f\"event-{index:02d}\"} for index in range(13))\n",
    "existing selection-cap fixture",
)
text = text[:function_start] + chunk + text[function_end:]
existing.write_text(text, encoding="utf-8")
print("ROUND2_TEST_FIXTURES_APPLIED")
