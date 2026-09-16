import sqlite3
from contextlib import contextmanager

from polymarket_scanner.weather_only_paper_commands_all import AllPaperCommandController
from polymarket_scanner.weather_only_paper_corrective import PAPER_EXECUTION_PROTOCOL_V4
from polymarket_scanner.weather_only_paper_post_receipt import PAPER_EXECUTION_PROTOCOL_V5


class _Store:
    def __init__(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.execute(
            """
            CREATE TABLE weather_paper_positions(
                lane TEXT NOT NULL,
                status TEXT NOT NULL,
                validation_state TEXT,
                execution_protocol TEXT,
                capital_used REAL,
                proceeds REAL,
                pnl REAL
            )
            """
        )

    @contextmanager
    def _conn(self):
        yield self.db


def _controller(store):
    controller = object.__new__(AllPaperCommandController)
    controller.store = store
    return controller


def test_protocol_performance_does_not_mix_v4_and_v5():
    store = _Store()
    rows = [
        ("future", "WON", "VALIDATED", PAPER_EXECUTION_PROTOCOL_V4, 10.0, 15.0, 5.0),
        ("future", "LOST", "VALIDATED", PAPER_EXECUTION_PROTOCOL_V4, 10.0, 0.0, -10.0),
        ("same_day", "WON", "VALIDATED", PAPER_EXECUTION_PROTOCOL_V5, 10.0, 18.0, 8.0),
        ("source_shock", "OPEN", "VALIDATED", PAPER_EXECUTION_PROTOCOL_V5, 7.0, None, None),
        ("ignored", "WON", "UNVERIFIED", PAPER_EXECUTION_PROTOCOL_V5, 99.0, 199.0, 100.0),
    ]
    store.db.executemany(
        "INSERT INTO weather_paper_positions VALUES(?,?,?,?,?,?,?)", rows
    )

    controller = _controller(store)
    v5, v5_lanes = controller._protocol_performance(PAPER_EXECUTION_PROTOCOL_V5)
    v4, v4_lanes = controller._protocol_performance(PAPER_EXECUTION_PROTOCOL_V4)

    assert v5["total"] == 2
    assert v5["open"] == 1
    assert v5["resolved"] == 1
    assert v5["won"] == 1
    assert v5["lost"] == 0
    assert v5["resolved_capital"] == 10.0
    assert v5["pnl"] == 8.0
    assert v5["resolved_roi"] == 0.8
    assert {row["lane"] for row in v5_lanes} == {"same_day", "source_shock"}

    assert v4["total"] == 2
    assert v4["resolved"] == 2
    assert v4["won"] == 1
    assert v4["lost"] == 1
    assert v4["resolved_capital"] == 20.0
    assert v4["pnl"] == -5.0
    assert v4["resolved_roi"] == -0.25
    assert {row["lane"] for row in v4_lanes} == {"future"}
