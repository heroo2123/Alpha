import json
from pathlib import Path

from polymarket_scanner.shadow_telemetry import (
    SHADOW_MAX_ROWS,
    SHADOW_TELEMETRY_VERSION,
    _reset_shadow_throttle_for_tests,
    maybe_record_shadow_health_state,
    recent_shadow_samples,
    record_shadow_sample,
)
from polymarket_scanner.store import Store


def _snapshot(*, rss: int = 1000, release: str = "a" * 40) -> dict:
    return {
        "runtime_resources": {
            "process_rss_bytes": rss,
            "process_virtual_bytes": rss * 2,
            "process_swap_bytes": 10,
            "process_threads": 5,
            "open_file_descriptors": 12,
            "logical_cpu_count": 2,
            "load_average_1m": 0.2,
            "load_average_5m": 0.3,
            "load_average_15m": 0.4,
            "disk_free_bytes": 123456,
            "database_bytes": 100,
            "database_wal_bytes": 20,
            "database_shm_bytes": 10,
        },
        "feed_progress": {
            "market_clob": {
                "connected_workers": 2,
                "cached_synchronized_books": 100,
                "last_valid_book_update_age_seconds": 1.5,
                "last_full_book_age_seconds": 2.0,
                "books_invalidated_total": 3,
                "out_of_order_ignored_total": 4,
            },
            "sports": {
                "connected": True,
                "cached_result_payloads": 7,
                "latest_source_age_seconds": 8.0,
                "causal_quarantined_slugs": 1,
            },
            "crypto_rtds": {
                "connected": True,
                "valid_progress_now": True,
                "last_valid_update_age_seconds": 0.5,
                "invalid_rows_total": 2,
                "out_of_order_ignored_total": 3,
                "conflicting_timestamp_rows_total": 4,
            },
        },
        "price_discovery_authority": {
            "snapshot_age_seconds": 12.0,
            "usable_coverage_ratio": 0.91,
        },
        "runtime_manifest": {"authorized_release_sha": release},
        "database_schema": {"compatible": True},
        "production_runtime_authority_complete": True,
        "p0_containment": True,
        "universe_safe_for_detection": True,
        "signal_batches_pending": 1,
        "scan_in_progress": False,
        "settlement_in_progress": False,
        # These arbitrary fields must never be persisted by the whitelist.
        "telegram_bot_token": "SECRET_TOKEN",
        "telegram_chat_id": "SECRET_CHAT",
        "last_error": "https://api.telegram.org/botSECRET_TOKEN/sendMessage",
    }


def test_shadow_sample_persists_only_whitelisted_secret_free_evidence(tmp_path):
    store = Store(str(tmp_path / "signals.db"))
    result = record_shadow_sample(store, _snapshot(), sampled_at=1000.0)
    assert result["version"] == SHADOW_TELEMETRY_VERSION
    assert result["rows_retained"] == 1
    assert result["max_rows"] == SHADOW_MAX_ROWS

    row = recent_shadow_samples(store, limit=1)[0]
    payload = json.loads(row["payload_json"])
    text = row["payload_json"]
    assert "SECRET_TOKEN" not in text
    assert "SECRET_CHAT" not in text
    assert "telegram_bot_token" not in text
    assert payload["release_sha"] == "a" * 40
    assert payload["resources"]["process_rss_bytes"] == 1000
    assert payload["feeds"]["crypto_valid_progress_now"] is True
    assert payload["scanner"]["price_usable_coverage_ratio"] == 0.91


def test_shadow_history_prunes_by_age(tmp_path):
    store = Store(str(tmp_path / "signals.db"))
    record_shadow_sample(store, _snapshot(rss=1), sampled_at=1000.0, retention_days=1)
    record_shadow_sample(store, _snapshot(rss=2), sampled_at=1000.0 + 86401, retention_days=1)
    rows = recent_shadow_samples(store, limit=10)
    assert len(rows) == 1
    assert json.loads(rows[0]["payload_json"])["resources"]["process_rss_bytes"] == 2


def test_shadow_history_prunes_by_absolute_row_cap(tmp_path):
    store = Store(str(tmp_path / "signals.db"))
    for index in range(65):
        record_shadow_sample(
            store,
            _snapshot(rss=index),
            sampled_at=10_000.0 + index,
            retention_days=14,
            max_rows=60,
        )
    rows = recent_shadow_samples(store, limit=100)
    assert len(rows) == 60
    newest = json.loads(rows[0]["payload_json"])["resources"]["process_rss_bytes"]
    oldest = json.loads(rows[-1]["payload_json"])["resources"]["process_rss_bytes"]
    assert newest == 64
    assert oldest == 5


def test_shadow_payload_rejects_nonfinite_values_without_invalid_json(tmp_path):
    store = Store(str(tmp_path / "signals.db"))
    snap = _snapshot()
    snap["runtime_resources"]["load_average_1m"] = float("nan")
    record_shadow_sample(store, snap, sampled_at=1000.0)
    payload = json.loads(recent_shadow_samples(store, limit=1)[0]["payload_json"])
    assert payload["resources"]["load_average_1m"] is None


def test_health_state_sampler_waits_for_schema_and_throttles_to_one_minute(tmp_path):
    _reset_shadow_throttle_for_tests()
    store = Store(str(tmp_path / "signals.db"))
    snap = _snapshot(rss=10)

    blocked = dict(snap)
    blocked["database_schema"] = {"compatible": False}
    assert maybe_record_shadow_health_state(
        store,
        json.dumps(blocked),
        monotonic_now=100.0,
        sampled_at=1000.0,
    ) is None

    first = maybe_record_shadow_health_state(
        store,
        json.dumps(snap),
        monotonic_now=100.0,
        sampled_at=1000.0,
    )
    assert first is not None

    snap["runtime_resources"]["process_rss_bytes"] = 20
    assert maybe_record_shadow_health_state(
        store,
        json.dumps(snap),
        monotonic_now=159.9,
        sampled_at=1059.9,
    ) is None

    second = maybe_record_shadow_health_state(
        store,
        json.dumps(snap),
        monotonic_now=160.0,
        sampled_at=1060.0,
    )
    assert second is not None
    rows = recent_shadow_samples(store, limit=10)
    assert len(rows) == 2
    assert json.loads(rows[0]["payload_json"])["resources"]["process_rss_bytes"] == 20


def test_trade_only_wires_health_persistence_to_shadow_sampler():
    source = Path("app_trade_only.py").read_text(encoding="utf-8")
    assert "maybe_record_shadow_health_state" in source
    assert "_trade_only_set_state" in source
    assert "base.store.set_state = _trade_only_set_state" in source
