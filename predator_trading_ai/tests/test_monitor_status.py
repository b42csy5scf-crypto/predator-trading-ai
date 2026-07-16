from __future__ import annotations

from datetime import datetime, timedelta, timezone

import predator_trading_ai.alerts.telegram_bot as telegram_module
from predator_trading_ai.config import Settings
from predator_trading_ai.database.db import Database
from predator_trading_ai.reports.monitor_status import MonitorStatusReport


def iso_ago(seconds: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()


def make_db(tmp_path) -> Database:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'monitor.db'}")
    db = Database(settings)
    db.initialize()
    return db


def table_counts(db: Database) -> dict[str, int]:
    tables = [
        "system_state",
        "universe_snapshot",
        "active_signals",
        "completed_trades",
        "signal_diagnostics",
        "signal_outcome_diagnostics",
        "rejected_candidate_diagnostics",
        "price_path",
        "signal_updates",
    ]
    return {table: db.fetch_all(f"SELECT COUNT(*) AS count FROM {table}")[0]["count"] for table in tables}


def seed_healthy(db: Database) -> None:
    now = iso_ago(10)
    db.set_state("heartbeat_utc", now)
    db.set_state("tp_sl_monitor_heartbeat_utc", now)
    db.execute(
        """
        INSERT INTO universe_snapshot (
            timestamp, symbols_scanned, symbols_skipped, api_failures,
            missing_market_data, symbols_successfully_evaluated
        )
        VALUES (?, 50, 0, 0, 0, 50)
        """,
        [now],
    )
    db.execute(
        """
        INSERT INTO active_signals (
            id, ticker, grade, alert_type, direction, entry_zone_low, entry_zone_high,
            stop_loss, original_stop_loss, tp1, tp2, tp3, sent_at, status
        )
        VALUES (1, 'NVDA', 'A++ Signal', 'trade_candidate', 'long', 100, 101, 98, 98, 104, 106, 108, ?, 'active')
        """,
        [now],
    )
    db.execute(
        """
        INSERT INTO price_path (signal_id, timestamp, price, high, low, event_type)
        VALUES (1, ?, 101, 101.5, 100.5, 'scan')
        """,
        [now],
    )
    db.execute(
        """
        INSERT INTO signal_updates (active_signal_id, ticker, update_type, price, status, message)
        VALUES (1, 'NVDA', 'tp1', 104, 'active', 'TP1 hit')
        """,
    )
    db.execute(
        """
        INSERT INTO signal_diagnostics (
            active_signal_id, ticker, grade, alert_type, score,
            entry_zone_low, entry_zone_high, stop_loss, tp1, tp2, tp3,
            git_commit_hash, strategy_version, schema_version, research_dataset_version,
            config_hash, scoring_components_json, raw_metrics_json
        )
        VALUES (1, 'NVDA', 'A++ Signal', 'trade_candidate', 78, 100, 101, 98, 104, 106, 108,
                'abc123', '1.0', 'research-schema-v1.0', 'v1.0', 'hash-1', '[]', '{}')
        """,
    )
    db.execute(
        """
        INSERT INTO signal_outcome_diagnostics (
            active_signal_id, ticker, grade, alert_type, direction, entry_price,
            original_stop_loss, risk_per_share
        )
        VALUES (1, 'NVDA', 'A++ Signal', 'trade_candidate', 'long', 100, 98, 2)
        """,
    )


def test_monitor_status_fully_healthy_runtime(tmp_path) -> None:
    db = make_db(tmp_path)
    seed_healthy(db)
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'monitor.db'}",
        loop_interval_seconds=300,
        telegram_bot_token="secret-token",
        telegram_chat_id="secret-chat",
    )

    report = MonitorStatusReport(settings, db).build()

    assert "Predator System Monitor" in report
    assert "Overall" in report
    assert "Scanner" in report
    assert "- Running: YES" in report
    assert "TP/SL Monitor" in report
    assert "ActiveSignalTracker" in report
    assert "Research Dataset" in report
    assert "secret-token" not in report
    assert "secret-chat" not in report


def test_monitor_status_scanner_heartbeat_stale(tmp_path) -> None:
    db = make_db(tmp_path)
    db.set_state("heartbeat_utc", iso_ago(2000))
    db.execute(
        """
        INSERT INTO universe_snapshot (
            timestamp, symbols_scanned, symbols_skipped, api_failures,
            missing_market_data, symbols_successfully_evaluated
        )
        VALUES (?, 50, 0, 0, 0, 50)
        """,
        [iso_ago(2000)],
    )
    report = MonitorStatusReport(Settings(database_url=f"sqlite:///{tmp_path / 'monitor.db'}", loop_interval_seconds=300), db).build()

    assert "❌ ERROR" in report
    assert "No completed scan within the expected interval." in report


def test_monitor_status_tp_sl_stale_with_active_signals(tmp_path) -> None:
    db = make_db(tmp_path)
    seed_healthy(db)
    db.set_state("tp_sl_monitor_heartbeat_utc", iso_ago(2000))

    report = MonitorStatusReport(Settings(database_url=f"sqlite:///{tmp_path / 'monitor.db'}", loop_interval_seconds=300), db).build()

    assert "TP/SL monitor heartbeat is delayed." in report
    assert "❌ ERROR" in report


def test_monitor_status_tp_sl_idle_with_zero_active_signals(tmp_path) -> None:
    db = make_db(tmp_path)
    now = iso_ago(10)
    db.set_state("heartbeat_utc", now)
    db.set_state("tp_sl_monitor_heartbeat_utc", now)

    report = MonitorStatusReport(Settings(database_url=f"sqlite:///{tmp_path / 'monitor.db'}"), db).build()

    assert "- Running: IDLE" in report
    assert "TP/SL monitor is stale while active signals exist" not in report


def test_monitor_status_active_signals_with_stale_price_path(tmp_path) -> None:
    db = make_db(tmp_path)
    seed_healthy(db)
    db.execute("DELETE FROM price_path")
    db.execute(
        """
        INSERT INTO price_path (signal_id, timestamp, price, high, low, event_type)
        VALUES (1, ?, 101, 101, 101, 'scan')
        """,
        [iso_ago(2000)],
    )

    report = MonitorStatusReport(Settings(database_url=f"sqlite:///{tmp_path / 'monitor.db'}", loop_interval_seconds=300), db).build()

    assert "Active signals exist but price updates are stale." in report


def test_monitor_status_telegram_conflict_and_disabled_polling(tmp_path) -> None:
    db = make_db(tmp_path)
    telegram_module.TELEGRAM_POLLING_STARTED = False
    telegram_module.TELEGRAM_POLLING_SKIPPED_REASON = "conflict_detected"
    telegram_module.TELEGRAM_POLLING_DISABLED_REASON = "terminated by other getUpdates request"
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'monitor.db'}",
        telegram_bot_token="token",
        telegram_chat_id="123",
        enable_telegram_polling=True,
    )

    report = MonitorStatusReport(settings, db).build()

    assert "Command polling disabled after Conflict: YES" in report
    assert "Conflict caught: YES" in report
    assert "Telegram command polling is disabled after Conflict; sendMessage alerts remain available." in report
    telegram_module.TELEGRAM_POLLING_SKIPPED_REASON = "not_started"
    telegram_module.TELEGRAM_POLLING_DISABLED_REASON = None


def test_monitor_status_database_unavailable() -> None:
    class BrokenDB:
        def fetch_all(self, sql, params=()):
            raise RuntimeError("database unavailable")

    report = MonitorStatusReport(Settings(), BrokenDB()).build()

    assert "Database" in report
    assert "Read test: FAILED" in report or "status query failed" in report


def test_monitor_status_empty_database_and_legacy_null_rows(tmp_path) -> None:
    db = make_db(tmp_path)
    db.execute(
        """
        INSERT INTO signal_diagnostics (
            ticker, grade, alert_type, score, entry_zone_low, entry_zone_high,
            stop_loss, tp1, tp2, tp3, scoring_components_json, raw_metrics_json
        )
        VALUES ('AAPL', 'A Signal', 'trade_candidate', 60, 100, 101, 98, 104, 106, 108, '[]', '{}')
        """
    )

    report = MonitorStatusReport(Settings(database_url=f"sqlite:///{tmp_path / 'monitor.db'}"), db).build()

    assert "Predator System Monitor" in report
    assert "Schema version:" in report
    assert "Config hash:" in report


def test_monitor_status_command_is_read_only(tmp_path) -> None:
    db = make_db(tmp_path)
    seed_healthy(db)
    before = table_counts(db)

    MonitorStatusReport(Settings(database_url=f"sqlite:///{tmp_path / 'monitor.db'}"), db).build()

    assert table_counts(db) == before
