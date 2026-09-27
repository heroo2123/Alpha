# R23 regional/correlated-exposure mapping: reviewed evidence

## Scope and review result — 2026-09-27

Independent GPT-6 Astra high-effort review covered commit `0378b381b2a8f87f1b58532898b328f12ba205f0`
against parent `b457aabdeffcc73aacedb5034c68a4c1f09a7003`, the authoritative
master's R23/I2 requirements, and this commit's changed ledgers only.
Corrections below supersede the original batch-15 claims of certified input,
conservative region/provider-only dependence, and closure of the entire
"actual mappings" tail. Only public NWS administrative-region lookup and
local metadata binding have been demonstrated. R23 remains PARTIAL.

## Reproduced defects and corrections

- The old adapter rounded coordinates, but membership required exact metadata
  equality. All five public stations below failed that equality. Version 2
  preserves the exact requested coordinates and uses four decimals only for
  the HTTP query/response identity. Different exact coordinates in the same
  rounded cell still fail membership binding.
- Point identity fields could conflict; foreign or unrelated URLs could pass
  suffix checks. All supplied point IDs, grid office, forecast office, office
  ID and regional parent now must agree with the official HTTPS endpoints.
- Typed records alone did not validate region evidence. The builder now checks
  normalized schema and evidence digest, including receipt time, before binding.
  Region-code/name correspondence is checked. Hashes establish consistency,
  not authenticated origin, protected approval, or station certification.
- Caller groups could replace known dependence and bypass missing providers.
  Extras now only add groups; missing observation/forecast providers fail closed.
  Source groups retain both observation and forecast provider identities; model
  groups retain forecast provider identities.
- NWS regions are administrative boundaries, not evidence of weather-system
  independence. Provider names likewise do not establish independent upstream
  sources/models. Every membership retains `SHARED_UNKNOWN_WEATHER`,
  `SHARED_UNKNOWN_SOURCE`, and `SHARED_UNKNOWN_MODEL` in the corresponding
  groups. These are conservative unresolved-risk buckets, not invented physical
  classifications. Narrower evidence-backed grouping remains future work.
- The response cap previously ran after full buffering. HTTP responses now
  stream with an incremental 512 KiB decoded-body cap, bounded retries, and
  fail-closed rejection of non-2xx responses without following redirects.

No existing production call site, financial gate, V10 asset, credential,
private input, or privileged host policy changed.

## Automated verification

Original baseline: **23 passed / 0.41 s** on the exact reviewed commit.
The original documentation's split of 14 parser / 9 membership cases was
incorrect: it was **18 parser / 5 membership**.

After correction: **81 focused passed / 0.97 s**. Tests exercise precise
coordinates, all supplied identity conflicts, evidence mutations, additive
and missing-provider groups, bounded transport/status/JSON failures and stream
closure, and actual `portfolio_risk` aggregation across different regions and
provider names. Existing schema and six-region-code tests remain.

Relevant integration selection: **156 passed / 4.27 s**, no skips or warnings:

```sh
/home/alphaadmin/AlphaV11_Dev/venv/bin/python -m pytest -q \
  tests/test_weather_only_station_region.py tests/test_v11_region_membership.py \
  tests/test_v11_scenario_risk.py tests/test_v11_certification_rules.py \
  tests/test_weather_only_station_metadata.py tests/test_weather_only_wrh_station_metadata.py \
  tests/test_v11_forecast_sources.py
```

An initial selection used nonexistent `tests/test_v11_certification.py` and
collected no tests (exit 4); correcting the filename produced the result above.
No full regression was run: corrections remain within this additive R23 slice.

## Repeatable public-source verification

The original commit reported a one-time five-station region lookup, but omitted
the exact command and never demonstrated membership with full-precision metadata.
The review repeated the lookup before correction: regions matched, but all five
exact-coordinate comparisons were false. After correction the command below
passed. It obtains coordinates/timezone from public station responses; city,
settlement and provider fields are explicitly test inputs. This is local binding
verification, not evidence of certified providers or accepted station capability.

Run from the repository root, with the development virtualenv activated:

```sh
python - <<'PYTHON'
import asyncio
from polymarket_scanner.weather_only_station_region import NWSStationRegionClient
from polymarket_scanner.weather_only_station_metadata import parse_nws_station_metadata
from polymarket_scanner.v11.certification import StationMetadata
from polymarket_scanner.v11.region_membership import build_station_membership

async def main():
    client = NWSStationRegionClient()
    try:
        for station, city in (("KATL", "Atlanta"), ("KDEN", "Denver"), ("KLAX", "Los Angeles"), ("KJFK", "New York"), ("KSEA", "Seattle")):
            payload, received = await client._get(
                "https://api.weather.gov/stations/" + station,
                timeout_code="STATION_TIMEOUT", transport_code="STATION_TRANSPORT",
                status_code="STATION_HTTP_STATUS", cap_code="STATION_CAP", json_code="STATION_JSON")
            source = parse_nws_station_metadata(payload, requested_station=station, received_at=received)
            point, office = await client.region(source.latitude, source.longitude)
            # Only coordinates/timezone are sourced here. City/provider/settlement
            # fields below are explicit test inputs, not certification evidence.
            metadata = StationMetadata(station, city, "US", source.latitude, source.longitude, None,
                                       source.timezone, "REVIEW_ONLY_UNVERIFIED", ("REVIEW_OBSERVATION",),
                                       ("REVIEW_FORECAST",), source.source_payload_sha256, received)
            member = build_station_membership(metadata=metadata, point=point, office=office)
            print(station, (source.latitude, source.longitude), point.cwa, member.region,
                  "exact_coordinate_binding=", (point.latitude, point.longitude) == (metadata.latitude, metadata.longitude))
    finally:
        await client.close()
asyncio.run(main())
PYTHON
```

Observed output on 2026-09-27:

```text
KATL (33.64028, -84.42694) FFC SOUTHERN exact_coordinate_binding= True
KDEN (39.84658, -104.65622) BOU CENTRAL exact_coordinate_binding= True
KLAX (33.93806, -118.38889) LOX WESTERN exact_coordinate_binding= True
KJFK (40.63915, -73.76393) OKX EASTERN exact_coordinate_binding= True
KSEA (47.44472, -122.31361) SEW WESTERN exact_coordinate_binding= True
```

Independent follow-up also checked official office responses across all six
regions (BOU, OKX, FFC, SEW, HFO, GUM, AFC, AFG, AJK, SJU); their regional
parent fields match the parser's checks. These are public lookups, not CI tests
or a retained operational evidence archive. The official
[NWS API documentation](https://www.weather.gov/documentation/services-web-api)
notes that point-to-office assignments can change; freshness, refresh and
archival policy remain runtime integration obligations.

## R37 correction and remaining gates

Read-only `gh run view 36300914533 --repo heroo2123/Alpha` independently confirmed
[the cited run](https://github.com/heroo2123/Alpha/actions/runs/36300914533)
completed successfully for `3dbb4c2765b002edd7bf55b9f5cf0fb624b1d6ee`, including
pytest on both Python 3.11 and 3.12. The stale CI-EPERM correction is accurate;
no R37 code or host policy changed.

`StationMetadata` validates a schema; it does not establish protected station
certification. Membership and evidence checks confer no settlement, calibration,
funding, or trading authority. Protected correlation-map review, evidence-backed
finer dependence mappings, durable evidence/freshness handling, and actual
candidate/guardian integration remain open. No C/J/E/A credit is added:
**85/200 = 42.5%; 1/50 complete. NOT_READY_TO_FUND.**
