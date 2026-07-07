from predator_trading_ai.config import Settings
from predator_trading_ai.database.db import Database
from predator_trading_ai.engines.active_signal_tracker import ActiveSignalTracker
from predator_trading_ai.alerts.telegram_bot import TelegramAlertBot


def make_tracker(tmp_path, **overrides) -> tuple[ActiveSignalTracker, Database]:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'tracker.db'}", **overrides)
    db = Database(settings)
    db.initialize()
    return ActiveSignalTracker(db, settings), db


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
    completed = db.fetch_all("SELECT * FROM completed_trades WHERE active_signal_id = ?", [signal_id])[0]
    assert completed["outcome"] == "TP3"
    assert completed["status"] == "closed"
    assert completed["r_multiple"] > 0


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
    completed = db.fetch_all("SELECT * FROM completed_trades WHERE active_signal_id = ?", [signal_id])[0]
    assert completed["outcome"] == "SL"
    assert completed["status"] == "closed"
    assert completed["r_multiple"] == -1.0


def test_active_signal_tp1_tp2_progress_updates_completed_trades(tmp_path) -> None:
    tracker, db = make_tracker(tmp_path)
    signal_id = register_signal(tracker)

    tracker.check_ticker("NVDA", 128.55)
    first = db.fetch_all("SELECT * FROM completed_trades WHERE active_signal_id = ?", [signal_id])[0]
    assert first["outcome"] == "TP1"
    assert first["status"] == "active"

    tracker.check_ticker("NVDA", 130.10)
    second = db.fetch_all("SELECT * FROM completed_trades WHERE active_signal_id = ?", [signal_id])[0]
    assert second["outcome"] == "TP2"
    assert second["status"] == "active"


def test_tp1_moves_stop_to_breakeven_for_trade_candidates(tmp_path) -> None:
    tracker, db = make_tracker(tmp_path, move_stop_to_breakeven_after_tp1=True)
    signal_id = register_signal(tracker)

    updates = tracker.check_ticker("NVDA", 128.55)

    assert [update.update_type for update in updates] == ["tp1"]
    row = db.fetch_all("SELECT * FROM active_signals WHERE id = ?", [signal_id])[0]
    expected_entry = (124.50 + 125.20) / 2
    assert row["tp1_hit"] == 1
    assert row["breakeven_active"] == 1
    assert row["stop_loss"] == expected_entry
    assert row["breakeven_price"] == expected_entry
    assert row["original_stop_loss"] == 121.80


def test_breakeven_exit_after_tp1_is_not_full_stop_loss(tmp_path) -> None:
    tracker, db = make_tracker(tmp_path, move_stop_to_breakeven_after_tp1=True)
    signal_id = register_signal(tracker)
    tracker.check_ticker("NVDA", 128.55)

    updates = tracker.check_ticker("NVDA", 124.85)

    assert [update.update_type for update in updates] == ["breakeven"]
    assert "Breakeven exit after TP1" in updates[0].message
    row = db.fetch_all("SELECT * FROM active_signals WHERE id = ?", [signal_id])[0]
    assert row["status"] == "closed"
    assert row["close_reason"] == "breakeven_after_tp1"
    completed = db.fetch_all("SELECT * FROM completed_trades WHERE active_signal_id = ?", [signal_id])[0]
    assert completed["outcome"] == "BE"
    assert completed["status"] == "closed"
    assert completed["r_multiple"] == 0.0


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


def test_active_signal_tracker_does_not_start_telegram_polling(tmp_path, monkeypatch) -> None:
    def fail_polling(*args, **kwargs):
        raise AssertionError("TP/SL tracker must not start Telegram polling")

    monkeypatch.setattr(TelegramAlertBot, "start_command_polling", fail_polling)
    tracker, _ = make_tracker(tmp_path)
    register_signal(tracker)

    updates = tracker.check_ticker("NVDA", 128.55)

    assert [update.update_type for update in updates] == ["tp1"]
