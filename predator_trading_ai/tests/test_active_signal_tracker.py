from predator_trading_ai.config import Settings
from predator_trading_ai.database.db import Database
from predator_trading_ai.engines.active_signal_tracker import ActiveSignalTracker


def make_tracker(tmp_path) -> tuple[ActiveSignalTracker, Database]:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'tracker.db'}")
    db = Database(settings)
    db.initialize()
    return ActiveSignalTracker(db), db


def register_signal(tracker: ActiveSignalTracker) -> int:
    return tracker.register(
        ticker="NVDA",
        grade="A+ Signal",
        direction="long",
        entry_zone_low=124.50,
        entry_zone_high=125.20,
        stop_loss=121.80,
        targets=(128.40, 130.00, 132.00),
    )


def test_active_signal_tp_tracking_and_no_duplicate_messages(tmp_path) -> None:
    tracker, db = make_tracker(tmp_path)
    signal_id = register_signal(tracker)

    first = tracker.check_ticker("NVDA", 128.55)
    duplicate = tracker.check_ticker("NVDA", 128.70)
    second = tracker.check_ticker("NVDA", 130.10)
    completed = tracker.check_ticker("NVDA", 132.20)

    assert [update.update_type for update in first] == ["tp1"]
    assert duplicate == []
    assert [update.update_type for update in second] == ["tp2"]
    assert [update.update_type for update in completed] == ["tp3"]
    row = db.fetch_all("SELECT * FROM active_signals WHERE id = ?", [signal_id])[0]
    assert row["status"] == "closed"
    assert row["close_reason"] == "tp3_completed"
    updates = db.fetch_all("SELECT update_type FROM signal_updates WHERE active_signal_id = ? ORDER BY id", [signal_id])
    assert [row["update_type"] for row in updates] == ["tp1", "tp2", "tp3"]


def test_active_signal_stop_loss_tracking(tmp_path) -> None:
    tracker, db = make_tracker(tmp_path)
    signal_id = register_signal(tracker)

    updates = tracker.check_ticker("NVDA", 121.65)
    repeated = tracker.check_ticker("NVDA", 121.50)

    assert [update.update_type for update in updates] == ["stop_loss"]
    assert "Status: Closed / Invalidated" in updates[0].message
    assert repeated == []
    row = db.fetch_all("SELECT * FROM active_signals WHERE id = ?", [signal_id])[0]
    assert row["status"] == "closed"
    assert row["close_reason"] == "invalidated"


def test_new_grade_supersedes_previous_active_signal(tmp_path) -> None:
    tracker, db = make_tracker(tmp_path)
    first_id = register_signal(tracker)
    second_id = tracker.register(
        ticker="NVDA",
        grade="A++ Signal",
        direction="long",
        entry_zone_low=126,
        entry_zone_high=127,
        stop_loss=123,
        targets=(130, 133, 136),
    )

    first = db.fetch_all("SELECT status, close_reason FROM active_signals WHERE id = ?", [first_id])[0]
    second = db.fetch_all("SELECT status FROM active_signals WHERE id = ?", [second_id])[0]
    assert first["status"] == "closed"
    assert first["close_reason"] == "superseded"
    assert second["status"] == "active"
