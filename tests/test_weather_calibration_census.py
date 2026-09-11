from __future__ import annotations

import asyncio
from datetime import date
from types import SimpleNamespace

from polymarket_scanner.weather_calibration_census import run_census


def _rules(station: str, unit_word: str) -> str:
    return (
        f'This market resolves to the range containing the highest reading in the "Temp" column from all times on this day '
        f'in Hourly Data after selecting Show Hourly Data at the listed NOAA station, in whole degrees {unit_word}, on 11 Sep \'26. '
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


def _market(station: str, unit_symbol: str, unit_word: str, mid: str, question: str) -> dict:
    return {
        'id': mid,
        'conditionId': f'condition-{mid}',
        'question': question.replace('{U}', unit_symbol),
        'description': _rules(station, unit_word),
        'resolutionSource': f'https://www.weather.gov/wrh/timeseries?site={station}',
        'outcomes': ['Yes', 'No'],
        'clobTokenIds': [f'yes-{mid}', f'no-{mid}'],
        'active': True,
        'closed': False,
        'acceptingOrders': True,
        'enableOrderBook': True,
    }


def _event(*, event_id: str, station: str, unit_symbol: str, unit_word: str, low: int, high: int) -> dict:
    rules = _rules(station, unit_word)
    return {
        'id': event_id,
        'slug': event_id,
        'title': f'Highest temperature on September 11 at {station}?',
        'description': rules,
        'resolutionSource': f'https://www.weather.gov/wrh/timeseries?site={station}',
        'markets': [
            _market(station, unit_symbol, unit_word, f'{event_id}-low', f'Will the highest temperature be {low}{unit_symbol} or lower on September 11?'),
            _market(station, unit_symbol, unit_word, f'{event_id}-mid', f'Will the highest temperature be between {low + 1}-{high - 1}{unit_symbol} on September 11?'),
            _market(station, unit_symbol, unit_word, f'{event_id}-high', f'Will the highest temperature be {high}{unit_symbol} or higher on September 11?'),
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


def test_census_separates_fahrenheit_worker_eligibility_from_celsius_structural_coverage():
    async def scenario():
        discovery = _Discovery([
            _event(
                event_id='event-f',
                station='KLGA',
                unit_symbol='°F',
                unit_word='Fahrenheit',
                low=79,
                high=83,
            ),
            _event(
                event_id='event-c',
                station='ZBAA',
                unit_symbol='°C',
                unit_word='Celsius',
                low=25,
                high=29,
            ),
        ])
        report = await run_census(
            discovery=discovery,
            as_of_date=date(2026, 9, 10),
        )
        assert discovery.calls == 1
        assert report['read_only'] is True
        assert report['gamma_only'] is True
        assert report['financial_authority'] is False
        assert report['financial_delivery'] is False
        assert report['automatic_order_placement'] is False
        assert report['nws_temperature_event_count'] == 2
        assert report['unit_counts'] == {'C': 1, 'F': 1}
        assert report['rule_semantics_proven_event_count'] == 2
        assert report['exactly_one_rule_proven_event_count'] == 2
        assert report['worker_capture_eligible_event_count'] == 1
        assert report['future_worker_capture_eligible_event_count'] == 1
        assert report['near_term_worker_capture_eligible_event_count'] == 1
        assert report['celsius_structural_proven_event_count'] == 1
        assert report['celsius_future_structural_proven_event_count'] == 1
        assert report['unique_station_count'] == 2
        samples = {row['event_id']: row for row in report['samples']}
        assert samples['event-f']['worker_capture_eligible'] is True
        assert samples['event-c']['worker_capture_eligible'] is False
        assert samples['event-c']['exactly_one_outcome_proven'] is True

    asyncio.run(scenario())
