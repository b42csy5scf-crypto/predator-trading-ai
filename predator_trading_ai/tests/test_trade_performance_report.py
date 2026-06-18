import subprocess

from predator_trading_ai.config import Settings
from predator_trading_ai.database.db import Database
from predator_trading_ai.reports.trade_performance_report import TradePerformanceReport


def make_db(tmp_path) -> Database:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'performance.db'}")
    db = Database(settings)
    db.initialize()
    return db


def insert_active(
    db: Database,
    ticker: str,
    grade: str,
    score: float,
    regime: str,
    close_reason: str,
    message: str,
) -> None:
    sent_at = "2026-06-18T14:30:00+00:00"
    db.insert_dict(
        "sent_alerts",
        {
            "ticker": ticker,
            "grade": grade,
            "alert_type": "trade_candidate",
            "score": score,
            "setup_type": "test",
            "regime": regime,
            "message": message,
        },
    )
    db.insert_dict(
        "active_signals",
        {
            "ticker": ticker,
            "grade": grade,
            "direction": "long",
            "entry_zone_low": 100.0,
            "entry_zone_high": 101.0,
            "stop_loss": 98.0,
            "tp1": 104.0,
            "tp2": 106.0,
            "tp3": 108.0,
            "sent_at": sent_at,
            "status": "closed",
            "tp1_hit": 1 if close_reason == "tp3_completed" else 0,
            "tp2_hit": 1 if close_reason == "tp3_completed" else 0,
            "tp3_hit": 1 if close_reason == "tp3_completed" else 0,
            "close_reason": close_reason,
        },
    )


def test_trade_performance_report_breakdowns_and_recommendations(tmp_path) -> None:
    db = make_db(tmp_path)
    insert_active(db, "NVDA", "A+ Signal", 70, "bull-trend", "tp3_completed", "Note: strong momentum")
    insert_active(db, "PLD", "B Watch Alert", 53, "choppy", "invalidated", "Note: Volume not confirmed")
    db.insert_dict(
        "shadow_signals",
        {
            "ticker": "PLD",
            "status": "watch_alert",
            "rejection_stage": "strategy",
            "rejection_reason": "volume not confirmed",
            "regime": "choppy",
            "regime_reason": "Weak trend strength",
            "score": 53,
            "price": 100,
            "volume_condition": "low relative volume",
            "trend_condition": "trend building",
            "volatility_condition": "normal",
            "correlation_condition": "ok",
        },
    )
    db.insert_dict(
        "rejected_signals",
        {
            "ticker": "PLD",
            "rejection_stage": "strategy",
            "rejection_reason": "volume not confirmed",
            "regime": "choppy",
            "score": 53,
        },
    )

    report = TradePerformanceReport(db).build()

    assert "Telegram Summary" in report
    assert "Total completed trades: 2" in report
    assert "Wins: 1" in report
    assert "Losses: 1" in report
    assert "Best grade: A+ Signal" in report
    assert "Worst grade: B Watch Alert" in report
    assert "By Grade" in report
    assert "A+ Signal" in report
    assert "B Watch Alert" in report
    assert "65-75" in report
    assert "50-55" in report
    assert "Bull" in report
    assert "Choppy" in report
    assert "NVDA" in report
    assert "PLD" in report
    assert "Technology" in report
    assert "Real Estate" in report
    assert "weak volume: 1" in report
    assert "Do not auto-activate improvements" in report


def test_report_reads_completed_trades_table_directly(tmp_path) -> None:
    db = make_db(tmp_path)
    db.insert_dict(
        "completed_trades",
        {
            "active_signal_id": 100,
            "ticker": "AAPL",
            "grade": "A++ Signal",
            "direction": "long",
            "entry_zone_low": 100,
            "entry_zone_high": 101,
            "entry_price": 100.5,
            "stop_loss": 98,
            "tp1": 104,
            "tp2": 106,
            "tp3": 108,
            "outcome": "TP3",
            "status": "closed",
            "opened_at": "2026-06-18T14:30:00+00:00",
            "closed_at": "2026-06-18T15:30:00+00:00",
            "close_price": 108,
            "r_multiple": 3.0,
            "regime": "bull-trend",
            "score": 82,
        },
    )
    report = TradePerformanceReport(db).build()
    assert "Total completed trades: 1" in report
    assert "A++ Signal" in report
    assert "75+" in report


def test_report_backfills_from_terminal_signal_updates(tmp_path) -> None:
    db = make_db(tmp_path)
    signal_id = db.insert_dict(
        "active_signals",
        {
            "ticker": "PLD",
            "grade": "B Watch Alert",
            "direction": "long",
            "entry_zone_low": 100,
            "entry_zone_high": 101,
            "stop_loss": 98,
            "tp1": 104,
            "tp2": 106,
            "tp3": 108,
            "sent_at": "2026-06-18T14:30:00+00:00",
            "status": "active",
        },
    )
    db.insert_dict(
        "signal_updates",
        {
            "active_signal_id": signal_id,
            "ticker": "PLD",
            "update_type": "stop_loss",
            "price": 97.9,
            "status": "closed",
            "message": "Stop loss hit",
        },
    )

    report = TradePerformanceReport(db).build()
    rows = db.fetch_all("SELECT * FROM completed_trades WHERE active_signal_id = ?", [signal_id])
    assert len(rows) == 1
    assert rows[0]["outcome"] == "SL"
    assert "Total completed trades: 1" in report
    assert "Losses: 1" in report


def test_trade_performance_report_empty_state(tmp_path) -> None:
    db = make_db(tmp_path)
    report = TradePerformanceReport(db).build()
    assert "No completed signal outcomes" in report


def test_run_performance_report_command(tmp_path) -> None:
    db_path = tmp_path / "performance_cli.db"
    settings = Settings(database_url=f"sqlite:///{db_path}")
    Database(settings).initialize()
    result = subprocess.run(
        [
            "predator_trading_ai/.venv/bin/python",
            "-m",
            "predator_trading_ai.run_performance_report",
        ],
        cwd="/Users/apple/Documents/Codex/2026-05-19/build-a-complete-ai-assisted-trading",
        env={"DATABASE_URL": f"sqlite:///{db_path}"},
        text=True,
        capture_output=True,
        check=True,
    )
    assert "Predator Trading AI Performance Analytics" in result.stdout
