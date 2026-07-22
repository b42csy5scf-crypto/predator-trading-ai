from predator_trading_ai.config import Settings
from predator_trading_ai.database.db import Database
from predator_trading_ai.reports.signal_forensics import SignalForensicsReport


def make_db(tmp_path) -> Database:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'signal_forensics.db'}")
    db = Database(settings)
    db.initialize()
    return db


def insert_signal(db: Database, signal_id: int = 1, ticker: str = "NVDA") -> None:
    db.insert_dict(
        "active_signals",
        {
            "id": signal_id,
            "ticker": ticker,
            "grade": "A++ Signal",
            "alert_type": "trade_candidate",
            "direction": "long",
            "entry_zone_low": 209.21,
            "entry_zone_high": 209.71,
            "stop_loss": 206.50,
            "tp1": 212.81,
            "tp2": 215.00,
            "tp3": 218.00,
            "sent_at": "2026-07-21T14:30:00+00:00",
            "status": "closed",
            "close_reason": "invalidated",
        },
    )
    db.insert_dict(
        "signal_diagnostics",
        {
            "signal_id": signal_id,
            "active_signal_id": signal_id,
            "ticker": ticker,
            "grade": "A++ Signal",
            "setup_grade": "A++ Signal",
            "alert_type": "trade_candidate",
            "score": 78,
            "entry_zone_low": 209.21,
            "entry_zone_high": 209.71,
            "stop_loss": 206.50,
            "tp1": 212.81,
            "tp2": 215.00,
            "tp3": 218.00,
            "telegram_note": "test",
            "scoring_components_json": [],
            "raw_metrics_json": {},
        },
    )
    db.insert_dict(
        "signal_outcome_diagnostics",
        {
            "active_signal_id": signal_id,
            "ticker": ticker,
            "grade": "A++ Signal",
            "alert_type": "trade_candidate",
            "direction": "long",
            "entry_price": 209.46,
            "original_stop_loss": 206.50,
            "risk_per_share": 2.96,
            "mfe_r": 0,
            "mae_r": -1,
            "current_r": -1,
            "final_outcome": "SL",
            "exit_reason": "stop_loss",
            "sl_hit_at": "2026-07-21T15:05:00+00:00",
            "realized_r": -1,
        },
    )
    db.insert_dict(
        "completed_trades",
        {
            "active_signal_id": signal_id,
            "ticker": ticker,
            "grade": "A++ Signal",
            "alert_type": "trade_candidate",
            "direction": "long",
            "entry_zone_low": 209.21,
            "entry_zone_high": 209.71,
            "entry_price": 209.46,
            "stop_loss": 206.50,
            "tp1": 212.81,
            "tp2": 215.00,
            "tp3": 218.00,
            "outcome": "SL",
            "status": "closed",
            "opened_at": "2026-07-21T14:30:00+00:00",
            "closed_at": "2026-07-21T15:05:00+00:00",
            "close_price": 206.40,
            "r_multiple": -1,
        },
    )


def insert_path(db: Database, signal_id: int, rows: list[tuple[str, float, float, float, str]]) -> None:
    for timestamp, price, high, low, event_type in rows:
        db.insert_dict(
            "price_path",
            {
                "signal_id": signal_id,
                "timestamp": timestamp,
                "price": price,
                "high": high,
                "low": low,
                "event_type": event_type,
            },
        )


def test_signal_forensics_reports_sampled_tp_hit(tmp_path) -> None:
    db = make_db(tmp_path)
    insert_signal(db)
    insert_path(db, 1, [("2026-07-21T14:35:00+00:00", 212.90, 213.05, 211.00, "scan")])

    report = SignalForensicsReport(db=db).build("NVDA")

    assert "SAMPLED_PRICE_TP_HIT" in report
    assert "price_path.price max/min: 212.90 / 212.90" in report


def test_signal_forensics_distinguishes_candle_touch_sample_miss(tmp_path) -> None:
    db = make_db(tmp_path)
    insert_signal(db)
    insert_path(db, 1, [("2026-07-21T14:35:00+00:00", 212.20, 213.05, 211.00, "scan")])

    report = SignalForensicsReport(db=db).build("NVDA")

    assert "CANDLE_HIGH_TOUCHED_BUT_SAMPLE_MISSED" in report
    assert "TP1 sampled/high: no / yes" in report


def test_signal_forensics_reports_not_touched_and_stop(tmp_path) -> None:
    db = make_db(tmp_path)
    insert_signal(db)
    insert_path(db, 1, [("2026-07-21T15:05:00+00:00", 206.40, 207.00, 206.30, "stop_loss")])

    report = SignalForensicsReport(db=db).build("NVDA")

    assert "TP_NOT_TOUCHED" in report
    assert "Stop sampled/candle: yes / yes" in report
    assert "price_path stop_loss" in report


def test_signal_forensics_reports_insufficient_price_path(tmp_path) -> None:
    db = make_db(tmp_path)
    insert_signal(db)

    report = SignalForensicsReport(db=db).build("NVDA")

    assert "INSUFFICIENT_PRICE_PATH_DATA" in report
    assert "price_path.price max/min: n/a / n/a" in report
