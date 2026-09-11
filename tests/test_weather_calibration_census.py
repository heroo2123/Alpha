from __future__ import annotations

import asyncio
from datetime import date
from types import SimpleNamespace

from polymarket_scanner.weather_calibration_census import run_census


def _rules(station: str, unit_word: str, date_text: str = "11 Sep '26") -> str:
    return (
        f'This market resolves to the range containing the highest reading in the "Temp" column from all times on this day '
        f'in Hourly Data after selecting Show Hourly Data at the listed NOAA station, in whole degrees {unit_word}, on {date_text}. '
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


def _market(
    station: str,
    unit_symbol: str,
    unit_word: str,
    mid: str,
    question: str,
    *,
    date_text: str,
) -> dict:
    return {
        'id': mid,
        'conditionId': f'condition-{mid}',
        'question': question,
        'description': _rules(station, unit_word, date_text),
        'resolutionSource': f'https://www.weather.gov/wrh/timeseries?site={station}',
        'outcomes': ['Yes', 'No'],
        'clobTokenIds': [f'yes-{mid}', f'no-{mid}'],
        'active': True,
        'closed': False,
        'acceptingOrders': True,
        'enableOrderBook': True,
    }


def _event(
    *,
    event_id: str,
    station: str,
    unit_symbol: str,
    unit_word: str,
    low: int,
    high: int,
    date_label: str,
    date_text: str,
) -> dict:
    rules = _rules(station, unit_word, date_text)
    return {
        'id': event_id,
        'slug': event_id,
        'title': f'Highest temperature on {date_label} at {station}?',
        'description': rules,
        'resolutionSource': f'https://www.weather.gov/wrh/timeseries?site={station}',
        'markets': [
            _market(station, unit_symbol, unit_word, f'{event_id}-low', f'Will the highest temperature be {low}{unit_symbol} or lower on {date_label}?', date_text=date_text),
            _market(station, unit_symbol, unit_word, f'{event_id}-mid', f'Will the highest temperature be between {low + 1}-{high - 1}{unit_symbol} on {date_label}?', date_text=date_text),
            _market(station, unit_symbol, unit_word, f'{event_id}-high', f'Will the highest temperature be {high}{unit_symbol} or higher on {date_label}?', date_text=date_text),
        ],
    }


class _Discovery:
    def __init__(self, events):
        self.events = tuple(events)
        self.calls = 0

    async def discover(self, tags):
        self.calls += 1
        return SimpleNamespace(
            events=self.events,
            summary=lambda: {
                'unique_event_count': len(self.events),
                'unique_market_count': sum(len(event['markets']) for event in self.events),
            },
        )


def test_census_separates_target_day_from_future_and_fahrenheit_from_celsius():
    async def scenario():
        discovery = _Discovery([
            _event(
                event_id='event-f-today',
                station='KLGA',
                unit_symbol='°F',
                unit_word='Fahrenheit',
                low=79,
                high=83,
                date_label='September 11',
                date_text="11 Sep '26",
            ),
            _event(
                event_id='event-f-future',
                station='KLGA',
                unit_symbol='°F',
                unit_word='Fahrenheit',
                low=79,
                high=83,
                date_label='September 12',
                date_text="12 Sep '26",
            ),
            _event(
                event_id='event-c-future',
                station='ZBAA',
                unit_symbol='°C',
                unit_word='Celsius',
                low=25,
                high=29,
                date_label='September 12',
                date_text="12 Sep '26",
            ),
        ])
        report = await run_census(
            discovery=discovery,
            as_of_date=date(2026, 9, 11),
        )
        assert discovery.calls == 1
        assert report['read_only'] is True
        assert report['gamma_only'] is True
        assert report['financial_authority'] is False
        assert report['financial_delivery'] is False
        assert report['automatic_order_placement'] is False
        assert report['nws_temperature_event_count'] == 3
        assert report['unit_counts'] == {'C': 1, 'F': 2}
        assert report['rule_semantics_proven_event_count'] == 3
        assert report['exactly_one_rule_proven_event_count'] == 3
        assert report['worker_capture_eligible_event_count'] == 2
        assert report['worker_target_today_event_count'] == 1
        assert report['worker_target_after_as_of_date_event_count'] == 1
        assert report['worker_near_term_after_as_of_date_event_count'] == 1
        assert report['celsius_structural_proven_event_count'] == 1
        assert report['celsius_target_today_structural_proven_event_count'] == 0
        assert report['celsius_target_after_as_of_date_structural_proven_event_count'] == 1
        assert report['unique_station_count'] == 2
        samples = {row['event_id']: row for row in report['samples']}
        assert samples['event-f-today']['worker_capture_eligible'] is True
        assert samples['event-f-today']['target_is_today'] is True
        assert samples['event-f-today']['target_after_as_of_date'] is False
        assert samples['event-f-future']['worker_capture_eligible'] is True
        assert samples['event-f-future']['target_after_as_of_date'] is True
        assert samples['event-c-future']['worker_capture_eligible'] is False
        assert samples['event-c-future']['exactly_one_outcome_proven'] is True
        assert samples['event-c-future']['target_after_as_of_date'] is True

    asyncio.run(scenario())
