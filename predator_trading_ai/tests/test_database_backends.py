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
    DatabaseConnectionError,
    TIMESTAMPTZ_COLUMNS,
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


def test_postgresql_unavailable_does_not_create_sqlite_fallback(tmp_path, monkeypatch) -> None:
    sqlite_path = tmp_path / "should_not_exist.db"
    db = Database(Settings(database_url="postgresql://user:password@example.invalid:5432/dbname"))

    def fail_connect():
        raise DatabaseConnectionError("PostgreSQL connection failed: OperationalError")

    monkeypatch.setattr(db, "connect", fail_connect)

    with pytest.raises(DatabaseConnectionError) as excinfo:
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


def test_postgresql_schema_uses_native_timestamp_types() -> None:
    db = Database(Settings(database_url="postgresql://user:password@example.invalid:5432/dbname"))
    statements = "\n".join(db._postgres_schema_statements())

    assert "created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP" in statements
    assert "timestamp TIMESTAMPTZ NOT NULL" in statements
    assert "quote_timestamp TIMESTAMPTZ" in statements
    assert "evaluation_timestamp TIMESTAMPTZ" in statements
    assert "exit_timestamp TIMESTAMPTZ" in statements
    assert "signal_diagnostics" in statements
    assert "price_path" in statements


def test_postgresql_schema_defers_quote_indexes_until_after_migration() -> None:
    db = Database(Settings(database_url="postgresql://user:password@example.invalid:5432/dbname"))
    statements = "\n".join(db._postgres_schema_statements())

    assert "idx_rejected_candidate_diagnostics_quote_quality" not in statements
    assert "idx_signal_diagnostics_quote_quality" not in statements


def test_sqlite_diagnostics_classification_and_quote_columns(tmp_path) -> None:
    db = Database(Settings(database_url=f"sqlite:///{tmp_path / 'schema.db'}"))
    db.initialize()
    conn = db.connect()
    signal_columns = {row["name"] for row in conn.execute("PRAGMA table_info(signal_diagnostics)").fetchall()}
    rejected_columns = {row["name"] for row in conn.execute("PRAGMA table_info(rejected_candidate_diagnostics)").fetchall()}
    indexes = {row["name"] for row in conn.execute("PRAGMA index_list(rejected_candidate_diagnostics)").fetchall()}
    conn.close()

    expected = {
        "raw_score",
        "setup_grade",
        "eligibility_status",
        "eligibility_stage",
        "block_reason_code",
        "final_acceptance_status",
        "displayed_grade_legacy",
        "classification_format_version",
        "raw_bid",
        "raw_ask",
        "quote_timestamp",
        "evaluation_timestamp",
        "spread_percentage",
        "quote_validity_status",
        "quote_validity_reasons",
    }
    assert expected <= signal_columns
    assert expected <= rejected_columns
    assert "idx_rejected_candidate_diagnostics_quote_quality" in indexes


def test_postgresql_quote_index_creation_skips_missing_columns() -> None:
    class FakeCursor:
        def __init__(self):
            self.statements: list[tuple[str, list | None]] = []

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, sql, params=None):
            self.statements.append((sql, params))

        def fetchall(self):
            return [{"column_name": "ticker"}]

        def fetchone(self):
            return None

    class FakeConn:
        def __init__(self):
            self.cursor_obj = FakeCursor()

        def cursor(self):
            return self.cursor_obj

    db = Database(Settings(database_url="postgresql://user:password@example.invalid:5432/dbname"))
    conn = FakeConn()
    db._create_index_if_missing(
        conn,
        "idx_rejected_candidate_diagnostics_quote_quality",
        "rejected_candidate_diagnostics",
        "ticker, quote_validity_status, spread_percentage",
    )
    sql_text = "\n".join(sql for sql, _ in conn.cursor_obj.statements)

    assert "CREATE INDEX idx_rejected_candidate_diagnostics_quote_quality" not in sql_text


def test_postgresql_timestamp_migration_generates_safe_alters() -> None:
    class FakeCursor:
        def __init__(self):
            self.statements: list[tuple[str, list | None]] = []
            self.current_table = ""

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, sql, params=None):
            self.statements.append((sql, params))
            if "information_schema.columns" in sql:
                self.current_table = params[0]

        def fetchall(self):
            return [
                {"column_name": column, "data_type": "text"}
                for column in TIMESTAMPTZ_COLUMNS.get(self.current_table, set())
            ]

    class FakeConn:
        def __init__(self):
            self.cursor_obj = FakeCursor()

        def cursor(self):
            return self.cursor_obj

    db = Database(Settings(database_url="postgresql://user:password@example.invalid:5432/dbname"))
    conn = FakeConn()
    db._migrate_postgres_timestamp_columns(conn)
    sql_text = "\n".join(sql for sql, _ in conn.cursor_obj.statements)

    assert "ALTER TABLE signal_diagnostics" in sql_text
    assert "ALTER COLUMN created_at TYPE TIMESTAMPTZ" in sql_text
    assert "WHEN created_at IS NULL THEN NULL" in sql_text
    assert "WHEN btrim(created_at::text) = '' THEN NULL" in sql_text
    assert "created_at::timestamptz" in sql_text
    assert "ALTER TABLE price_path" in sql_text


def test_postgresql_retention_cleanup_uses_cutoff_parameters() -> None:
    class CaptureDatabase(Database):
        def __init__(self):
            super().__init__(Settings(database_url="postgresql://user:password@example.invalid:5432/dbname"))
            self.calls = []

        def execute(self, sql, params=()):
            self.calls.append((sql, list(params)))
            return 0

    db = CaptureDatabase()
    db.cleanup_signal_diagnostics(retention_days=30)

    assert len(db.calls) == 3
    assert all("CURRENT_TIMESTAMP" not in sql for sql, _ in db.calls)
    assert all("datetime('now'" not in sql for sql, _ in db.calls)
    assert all(params and hasattr(params[0], "tzinfo") for _, params in db.calls)


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
