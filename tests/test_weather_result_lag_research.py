from __future__ import annotations

import asyncio

from polymarket_scanner.weather_only_result_lag_research import (
    ResultLagResearchStore,
    evaluate_provisional_result_lag_research,
)
from test_weather_only_result_lag import AFTER_RECEIVED, _FakeCLOB, _inputs


def test_provisional_result_lag_can_research_current_publication_without_finality_claim():
    event, _compiled, _before, after, first, _second = _inputs()
    candidate, exact = asyncio.run(
        evaluate_provisional_result_lag_research(
            event,
            after,
            clob=_FakeCLOB((first,)),
        )
    )
    assert candidate is not None
    assert exact is first
    assert candidate.revision_sensitive is True
    assert candidate.deterministic_result is False
    assert candidate.settlement_label_authority is False
    assert candidate.included_in_validated_pnl is False
    assert candidate.financial_authority is False
    assert candidate.executable_ask == 0.92
    assert candidate.provisional_edge_per_share > 0.01


def test_result_lag_research_store_is_separate_and_restart_safe(tmp_path):
    event, _compiled, _before, after, first, second = _inputs()
    pre, _ = asyncio.run(
        evaluate_provisional_result_lag_research(
            event,
            after,
            clob=_FakeCLOB((first,)),
        )
    )
    post, _ = asyncio.run(
        evaluate_provisional_result_lag_research(
            event,
            after,
            clob=_FakeCLOB((second,)),
        )
    )
    assert pre is not None and post is not None

    store = ResultLagResearchStore(tmp_path / "paper.sqlite")
    row_id = store.save_pending(
        pre,
        fingerprint="research-one",
        target_stake_usd=10.0,
        created_at=AFTER_RECEIVED + 0.50,
    )
    assert row_id is not None
    store.mark_telegram_sent(row_id, 1234, AFTER_RECEIVED + 0.55)
    opened = store.open_post_receipt(
        row_id,
        post,
        telegram_sent_at=AFTER_RECEIVED + 0.55,
        target_stake_usd=10.0,
    )
    assert opened["status"] == "OPEN"
    assert opened["included_in_validated_pnl"] == 0
    assert opened["deterministic_result"] == 0
    assert opened["financial_authority"] == 0

    settled = store.resolve(
        row_id,
        1.0,
        {"source": "TEST_FINAL_TOKEN", "financial_authority": False},
    )
    assert settled is not None
    assert settled["status"] == "WON"
    stats = store.stats()
    assert stats["resolved"] == 1
    assert stats["won"] == 1
    assert stats["included_in_validated_pnl"] is False
    assert stats["pnl"] > 0

    pending = store.save_pending(
        pre,
        fingerprint="research-interrupted",
        target_stake_usd=10.0,
        created_at=AFTER_RECEIVED + 3.0,
    )
    assert pending is not None
    recovered = ResultLagResearchStore(tmp_path / "paper.sqlite").recover_after_restart()
    # __init__ has already performed the recovery, so a second pass is idempotent.
    assert recovered["delivery_uncertain"] == 0
    with store._conn() as db:
        status = db.execute(
            "SELECT status FROM weather_result_lag_research_positions WHERE id=?",
            (pending,),
        ).fetchone()["status"]
    assert status == "DELIVERY_UNCERTAIN"
