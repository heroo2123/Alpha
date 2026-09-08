from polymarket_scanner.config import settings
from polymarket_scanner.runtime_manifest import _nonsecret_policy


def test_universe_capacity_is_part_of_attested_nonsecret_policy(monkeypatch):
    monkeypatch.setattr(settings, "max_events", 23456)
    monkeypatch.setattr(settings, "gamma_page_size", 77)
    monkeypatch.setattr(settings, "gamma_page_concurrency", 9)

    policy = _nonsecret_policy()

    assert policy["max_events"] == 23456
    assert policy["gamma_page_size"] == 77
    assert policy["gamma_page_concurrency"] == 9
