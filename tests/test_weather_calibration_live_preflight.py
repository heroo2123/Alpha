from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from types import SimpleNamespace

from polymarket_scanner.weather_calibration_live_preflight import run_live_preflight
from polymarket_scanner.weather_only_contracts import DAILY_HIGH
from polymarket_scanner.weather_only_forecast import (
    GEFS_CONTROL_KEY_HIGH,
    GEFS_PERTURBED_MEMBERS,
    parse_open_meteo_gefs_daily_extreme,
)


TARGET = date(2026, 9, 12)
NOW = datetime(2026, 9, 11, 16, 0, tzinfo=timezone.utc).timestamp()


def _rules(station: str) -> str:
    return (
        'This market resolves to the range containing the highest reading in the "Temp" column from all times on this day '
        'in Hourly Data after selecting Show Hourly Data at the listed NOAA station, in whole degrees Fahrenheit, on 12 Sep \'26. '
        f'The resolution source is https://www.weather.gov/wrh/timeseries?site={station}. '
        'If NOAA data for the observation date is unavailable by 11:59 PM ET on the day following the observation date, '
        'the Weather Underground Daily Observations table will be used as the resolution source. '
        'In the event that there is no data for the observation date by 11:59 PM ET on the day following the observation date, '
        'this market will resolve to the lowest bracket. '
        'This market will resolve once the first data point for the following date has been published on the resolution source, '
        'or by 11:59 PM ET on the day following the observation date, whichever comes first. '
        "Revisions to temperatures recorded within this market's timeframe will be considered until the first data point for the "
        'following date has been published, after which any alterations will not be considered.'
    )


def _event(event_id: str, station: str) -> dict:
    rules = _rules(station)

    def market(suffix: str, question: str) -> dict:
        return {
            'id': f'{event_id}-{suffix}',
            'conditionId': f'condition-{event_id}-{suffix}',
            'question': question,
            'description': rules,
            'resolutionSource': f'https://www.weather.gov/wrh/timeseries?site={station}',
            'outcomes': ['Yes', 'No'],
            'clobTokenIds': [f'yes-{event_id}-{suffix}', f'no-{event_id}-{suffix}'],
            'active': True,
            'closed': False,
            'acceptingOrders': True,
            'enableOrderBook': True,
        }

    return {
        'id': event_id,
        'slug': event_id,
        'title': f'Highest temperature at {station} on September 12?',
        'description': rules,
        'resolutionSource': f'https://www.weather.gov/wrh/timeseries?site={station}',
        'markets': [
            market('low', 'Will the highest temperature be 79°F or lower on September 12?'),
            market('mid', 'Will the highest temperature be between 80-82°F on September 12?'),
            market('high', 'Will the highest temperature be 83°F or higher on September 12?'),
        ],
    }


def _distribution(station: str, latitude: float, longitude: float, timezone_name: str):
    variable = GEFS_CONTROL_KEY_HIGH
    values = [81.0] * 18 + [78.0] * 7 + [84.0] * 6
    daily = {'time': [TARGET.isoformat()]}
    units = {'time': 'iso8601'}
    keys = [variable] + [f'{variable}_member{i:02d}' for i in range(1, GEFS_PERTURBED_MEMBERS + 1)]
    for key, value in zip(keys, values):
        daily[key] = [value]
        units[key] = '°F'
    return parse_open_meteo_gefs_daily_extreme(
        {
            'latitude': latitude,
            'longitude': longitude,
            'timezone': timezone_name,
            'daily': daily,
            'daily_units': units,
        },
        station=station,
        target_date=TARGET,
        family=DAILY_HIGH,
        unit='F',
        timezone=timezone_name,
        requested_latitude=latitude,
        requested_longitude=longitude,
        received_at=NOW,
    )


class _Discovery:
    def __init__(self, events):
        self.events = tuple(events)

    async def discover(self, tags):
        return SimpleNamespace(
            events=self.events,
            summary=lambda: {'unique_event_count': len(self.events)},
        )


class _StationClient:
    def __init__(self):
        self.calls = []

    async def station(self, station):
        self.calls.append(station)
        coords = {
            'KLGA': (40.7794, -73.8803),
            'KATL': (33.6407, -84.4277),
        }
        lat, lon = coords[station]
        return SimpleNamespace(
            adapter='test_station_metadata',
            station=station,
            latitude=lat,
            longitude=lon,
            timezone='America/New_York',
            settlement_authority=False,
            calibration_label_authority=False,
            financial_authority=False,
        )


class _ForecastClient:
    def __init__(self):
        self.calls = []

    async def daily_extreme(self, **kwargs):
        self.calls.append(kwargs)
        return _distribution(
            kwargs['station'],
            kwargs['latitude'],
            kwargs['longitude'],
            kwargs['timezone'],
        )


def test_live_preflight_resolves_all_stations_and_samples_distinct_gefs_mappings_without_authority():
    async def scenario():
        station_client = _StationClient()
        forecast_client = _ForecastClient()
        report = await run_live_preflight(
            discovery=_Discovery([
                _event('event-klga', 'KLGA'),
                _event('event-katl', 'KATL'),
            ]),
            station_client=station_client,
            forecast_client=forecast_client,
            now=NOW,
            max_forecast_probes=2,
        )
        assert report['preflight_ok'] is True
        assert report['eligible_fahrenheit_event_count'] == 2
        assert report['eligible_station_count'] == 2
        assert report['metadata_resolved_station_count'] == 2
        assert report['window_status_counts'] == {'BEFORE': 2}
        assert report['still_prospective_event_count'] == 2
        assert report['forecast_probe_attempt_count'] == 2
        assert report['forecast_probe_success_count'] == 2
        assert sorted(station_client.calls) == ['KATL', 'KLGA']
        assert len(forecast_client.calls) == 2
        assert {row['station'] for row in report['forecast_probes']} == {'KATL', 'KLGA'}
        assert all(row['member_count'] == 31 for row in report['forecast_probes'])
        assert report['database_mutation'] is False
        assert report['collector_registration'] is False
        assert report['wrh_settlement_requests'] is False
        assert report['clob_requests'] is False
        assert report['telegram_delivery'] is False
        assert report['automatic_order_placement'] is False
        assert report['financial_authority'] is False

    asyncio.run(scenario())
