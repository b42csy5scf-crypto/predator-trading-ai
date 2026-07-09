import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable, Optional

from predator_trading_ai.config import Settings, get_settings
from predator_trading_ai.utils.logger import setup_logger


class Database:
    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings = settings or get_settings()
        self.logger = setup_logger(__name__, self.settings.log_level)
        self.path = Path(self.settings.sqlite_path)

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def initialize(self) -> None:
        schema_path = Path(__file__).with_name("schema.sql")
        with self.connect() as conn:
            conn.executescript(schema_path.read_text(encoding="utf-8"))
            self._apply_migrations(conn)
        self.logger.info("SQLite schema initialized at %s", self.path)

    def _apply_migrations(self, conn: sqlite3.Connection) -> None:
        self._add_column_if_missing(conn, "active_signals", "original_stop_loss", "REAL")
        self._add_column_if_missing(conn, "active_signals", "breakeven_active", "INTEGER NOT NULL DEFAULT 0")
        self._add_column_if_missing(conn, "active_signals", "breakeven_price", "REAL")
        self._add_column_if_missing(conn, "active_signals", "alert_type", "TEXT NOT NULL DEFAULT 'trade_candidate'")
        self._add_column_if_missing(conn, "completed_trades", "alert_type", "TEXT NOT NULL DEFAULT 'trade_candidate'")
        self._add_column_if_missing(conn, "signal_diagnostics", "alert_type", "TEXT NOT NULL DEFAULT 'trade_candidate'")
        self._add_column_if_missing(conn, "signal_outcome_diagnostics", "alert_type", "TEXT NOT NULL DEFAULT 'trade_candidate'")

    @staticmethod
    def _add_column_if_missing(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def execute(self, sql: str, params: Iterable[Any] = ()) -> int:
        with self.connect() as conn:
            cursor = conn.execute(sql, tuple(params))
            conn.commit()
            return int(cursor.lastrowid)

    def fetch_all(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return list(conn.execute(sql, tuple(params)).fetchall())

    def insert_dict(self, table: str, payload: dict[str, Any]) -> int:
        columns = list(payload)
        values = [self._serialize(payload[column]) for column in columns]
        placeholders = ", ".join(["?"] * len(columns))
        column_sql = ", ".join(columns)
        return self.execute(
            f"INSERT INTO {table} ({column_sql}) VALUES ({placeholders})",
            values,
        )

    def cleanup_signal_diagnostics(self, retention_days: int = 30) -> None:
        self.execute(
            "DELETE FROM signal_diagnostics WHERE created_at < datetime('now', ?)",
            [f"-{retention_days} days"],
        )
        self.execute(
            "DELETE FROM rejected_candidate_diagnostics WHERE created_at < datetime('now', ?)",
            [f"-{retention_days} days"],
        )
        self.execute(
            "DELETE FROM signal_outcome_diagnostics WHERE updated_at < datetime('now', ?)",
            [f"-{retention_days} days"],
        )

    def set_state(self, key: str, value: Any) -> None:
        self.execute(
            """
            INSERT INTO system_state (key, value, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP
            """,
            [key, self._serialize(value)],
        )

    def get_state(self, key: str, default: Optional[Any] = None) -> Any:
        rows = self.fetch_all("SELECT value FROM system_state WHERE key = ?", [key])
        return rows[0]["value"] if rows else default

    @staticmethod
    def _serialize(value: Any) -> Any:
        if isinstance(value, (dict, list, tuple)):
            return json.dumps(value, default=str)
        return value
