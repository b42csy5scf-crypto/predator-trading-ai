from __future__ import annotations

import os
import sqlite3
import subprocess
import sys

import pytest

from predator_trading_ai.config import Settings
from predator_trading_ai.database.db import (
    Database,
    DatabaseConfigurationError,
    replace_qmark_placeholders,
)
from predator_trading_ai.engines.active_signal_tracker import ActiveSignalTracker


def test_sqlite_selected_when_database_url_is_sqlite(tmp_path) -> None:
    db = Database(Settings(database_url=f"sqlite:///{tmp_path / 'local.db'}"))
    assert db.backend == "sqlite"
    db.initialize()
    assert (tmp_path / "local.db").exists()


def test_postgresql_selected_when_database_url_is_postgresql() -> None:
    db = Database(Settings(database_url="postgresql://user:password@example.invalid:5432/dbname"))
    assert db.backend == "postgresql"


def test_malformed_database_url_fails_clearly() -> None:
    with pytest.raises(DatabaseConfigurationError):
        Database(Settings(database_url="mysql://user:password@example/db"))


def test_postgresql_unavailable_does_not_create_sqlite_fallback(tmp_path) -> None:
    sqlite_path = tmp_path / "should_not_exist.db"
    db = Database(Settings(database_url="postgresql://user:password@example.invalid:5432/dbname"))

    with pytest.raises((DatabaseConfigurationError, Exception)) as excinfo:
        db.initialize()

    assert "password" not in str(excinfo.value)
    assert not sqlite_path.exists()
    assert db.backend == "postgresql"


def test_placeholder_adapter_preserves_literals() -> None:
    sql = "SELECT '?' AS literal, value FROM table_name WHERE id = ? AND note = '?' AND other = ?"
    assert replace_qmark_placeholders(sql) == "SELECT '?' AS literal, value FROM table_name WHERE id = %s AND note = '?' AND other = %s"


def test_postgresql_query_adapter_handles_sqlite_datetime_helpers() -> None:
    db = Database(Settings(database_url="postgresql://user:password@example.invalid:5432/dbname"))
    sql, params = db.adapt_query(
        "SELECT * FROM signal_diagnostics WHERE created_at >= datetime('now', ?) AND date(created_at) = date('now') AND id = ?",
        ["-7 days", 1],
    )

    assert "%s" in sql
    assert "CURRENT_TIMESTAMP" in sql
    assert "CURRENT_DATE" in sql
    assert "datetime('now'" not in sql
    assert params == ("-7 days", 1)


def test_sqlite_mapping_rows_and_transaction_rollback(tmp_path) -> None:
    db = Database(Settings(database_url=f"sqlite:///{tmp_path / 'rollback.db'}"))
    db.initialize()

    with pytest.raises(RuntimeError):
        with db.transaction():
            db.insert_dict("system_state", {"key": "test", "value": "before"})
            raise RuntimeError("force rollback")

    assert db.fetch_all("SELECT * FROM system_state WHERE key = ?", ["test"]) == []


def test_active_signal_price_path_completed_and_diagnostics_survive_restart(tmp_path) -> None:
    db_path = tmp_path / "persist.db"
    settings = Settings(database_url=f"sqlite:///{db_path}")
    db = Database(settings)
    db.initialize()
    tracker = ActiveSignalTracker(db, settings)
    signal_id = tracker.register(
        ticker="NVDA",
        grade="A++ Signal",
        alert_type="trade_candidate",
        direction="long",
        entry_zone_low=100,
        entry_zone_high=101,
        stop_loss=98,
        targets=(104, 106, 108),
    )
    db.insert_dict(
        "price_path",
        {"signal_id": signal_id, "timestamp": "2026-07-16T14:30:00+00:00", "price": 101, "high": 101, "low": 100, "event_type": "scan"},
    )
    db.insert_dict(
        "signal_diagnostics",
        {
            "active_signal_id": signal_id,
            "ticker": "NVDA",
            "grade": "A++ Signal",
            "alert_type": "trade_candidate",
            "score": 78,
            "entry_zone_low": 100,
            "entry_zone_high": 101,
            "stop_loss": 98,
            "tp1": 104,
            "tp2": 106,
            "tp3": 108,
            "scoring_components_json": [],
            "raw_metrics_json": {},
        },
    )
    tracker.check_ticker("NVDA", current_price=98)

    restarted = Database(settings)
    assert restarted.fetch_all("SELECT * FROM active_signals WHERE id = ?", [signal_id])
    assert restarted.fetch_all("SELECT * FROM price_path WHERE signal_id = ?", [signal_id])
    assert restarted.fetch_all("SELECT * FROM completed_trades WHERE active_signal_id = ?", [signal_id])
    assert restarted.fetch_all("SELECT * FROM signal_diagnostics WHERE active_signal_id = ?", [signal_id])
    assert ActiveSignalTracker(restarted, settings).active_tickers() == []


def test_migration_cli_dry_run_does_not_require_postgresql_write(tmp_path) -> None:
    source = tmp_path / "source.db"
    conn = sqlite3.connect(source)
    conn.execute("CREATE TABLE active_signals (id INTEGER PRIMARY KEY AUTOINCREMENT, ticker TEXT)")
    conn.execute("INSERT INTO active_signals (ticker) VALUES ('NVDA')")
    conn.commit()
    conn.close()

    env = {**os.environ, "DATABASE_URL": "postgresql://user:password@example.invalid:5432/dbname"}
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "predator_trading_ai.migrate_sqlite_to_postgres",
            "--sqlite-path",
            str(source),
            "--database-url-env",
            "DATABASE_URL",
            "--dry-run",
        ],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )

    assert "active_signals: 1" in completed.stdout
    assert "password" not in completed.stdout
