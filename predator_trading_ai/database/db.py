from __future__ import annotations

import contextlib
import contextvars
import json
import re
import sqlite3
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional
from urllib.parse import urlparse

from predator_trading_ai.config import Settings, get_settings
from predator_trading_ai.utils.logger import setup_logger


POSTGRES_SCHEME_PREFIXES = ("postgresql://", "postgres://")
SQLITE_SCHEME_PREFIX = "sqlite:///"
ID_TABLES = {
    "signals",
    "trades",
    "backtest_results",
    "options_flow",
    "sentiment_data",
    "market_regime",
    "strategy_versions",
    "performance_metrics",
    "health_events",
    "shadow_signals",
    "rejected_signals",
    "forward_test_results",
    "sent_alerts",
    "active_signals",
    "signal_updates",
    "completed_trades",
    "signal_diagnostics",
    "rejected_candidate_diagnostics",
    "price_path",
    "universe_snapshot",
}


class DatabaseConfigurationError(RuntimeError):
    pass


class DatabaseConnectionError(RuntimeError):
    pass


class Database:
    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings = settings or get_settings()
        self.logger = setup_logger(__name__, self.settings.log_level)
        self.database_url = self.settings.database_url
        self.backend = self._select_backend(self.database_url)
        self.path = Path(self.settings.sqlite_path) if self.backend == "sqlite" else Path("postgresql")
        self._transaction_conn: contextvars.ContextVar[Any] = contextvars.ContextVar("db_transaction_conn", default=None)
        self.logger.info("Database backend selected: %s", self.backend)

    @staticmethod
    def _select_backend(database_url: str) -> str:
        if database_url.startswith(SQLITE_SCHEME_PREFIX):
            return "sqlite"
        if database_url.startswith(POSTGRES_SCHEME_PREFIXES):
            parsed = urlparse(database_url)
            if not parsed.hostname or not parsed.scheme:
                raise DatabaseConfigurationError("Invalid PostgreSQL DATABASE_URL.")
            return "postgresql"
        raise DatabaseConfigurationError("Unsupported DATABASE_URL scheme. Use PostgreSQL or sqlite:/// for local development.")

    @property
    def is_postgresql(self) -> bool:
        return self.backend == "postgresql"

    @property
    def is_sqlite(self) -> bool:
        return self.backend == "sqlite"

    def connect(self):
        if self.is_sqlite:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.path)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys=ON")
            return conn
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ModuleNotFoundError as exc:
            raise DatabaseConfigurationError("psycopg is required for PostgreSQL DATABASE_URL.") from exc
        try:
            return psycopg.connect(self.database_url, row_factory=dict_row, autocommit=False, connect_timeout=10)
        except Exception as exc:
            raise DatabaseConnectionError(f"PostgreSQL connection failed: {self._sanitize_error(exc)}") from exc

    def initialize(self) -> None:
        self.validate_connection()
        if self.is_sqlite:
            schema_path = Path(__file__).with_name("schema.sql")
            with self.connect() as conn:
                conn.executescript(schema_path.read_text(encoding="utf-8"))
                self._apply_migrations(conn)
            self.logger.info("Database connectivity check: passed")
            self.logger.info("Database schema initialization: passed")
            self.logger.info("Database schema version: research-schema-v1.0")
            return
        statements = self._postgres_schema_statements()
        with self.connect() as conn:
            try:
                with conn.cursor() as cursor:
                    for statement in statements:
                        if statement.strip():
                            cursor.execute(statement)
                self._apply_migrations(conn)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        self.verify_required_tables()
        self.logger.info("Database connectivity check: passed")
        self.logger.info("Database schema initialization: passed")
        self.logger.info("Database schema version: research-schema-v1.0")

    def validate_connection(self) -> None:
        with self.connect() as conn:
            if self.is_sqlite:
                conn.execute("SELECT 1").fetchone()
            else:
                with conn.cursor() as cursor:
                    cursor.execute("SELECT 1")
                    cursor.fetchone()

    def verify_required_tables(self) -> None:
        required = {
            "active_signals",
            "completed_trades",
            "signal_diagnostics",
            "signal_outcome_diagnostics",
            "rejected_candidate_diagnostics",
            "price_path",
            "universe_snapshot",
            "config_snapshots",
        }
        existing = set(self.table_names())
        missing = sorted(required - existing)
        if missing:
            raise DatabaseConfigurationError(f"Database schema missing required tables: {', '.join(missing)}")

    def table_names(self) -> list[str]:
        if self.is_sqlite:
            rows = self.fetch_all("SELECT name FROM sqlite_master WHERE type='table'")
            return [str(row["name"]) for row in rows]
        rows = self.fetch_all(
            """
            SELECT table_name AS name
            FROM information_schema.tables
            WHERE table_schema = 'public'
            """
        )
        return [str(row["name"]) for row in rows]

    def _postgres_schema_statements(self) -> list[str]:
        schema_path = Path(__file__).with_name("schema.sql")
        raw = schema_path.read_text(encoding="utf-8")
        raw = re.sub(r"^PRAGMA .*$", "", raw, flags=re.MULTILINE)
        raw = raw.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY")
        raw = raw.replace("REAL", "DOUBLE PRECISION")
        raw = raw.replace("DATETIME", "TIMESTAMP")
        return split_sql_statements(raw)

    def _apply_migrations(self, conn) -> None:
        self._add_column_if_missing(conn, "active_signals", "original_stop_loss", "REAL")
        self._add_column_if_missing(conn, "active_signals", "breakeven_active", "INTEGER NOT NULL DEFAULT 0")
        self._add_column_if_missing(conn, "active_signals", "breakeven_price", "REAL")
        self._add_column_if_missing(conn, "active_signals", "alert_type", "TEXT NOT NULL DEFAULT 'trade_candidate'")
        self._add_column_if_missing(conn, "completed_trades", "alert_type", "TEXT NOT NULL DEFAULT 'trade_candidate'")
        self._add_column_if_missing(conn, "signal_diagnostics", "alert_type", "TEXT NOT NULL DEFAULT 'trade_candidate'")
        self._add_column_if_missing(conn, "signal_outcome_diagnostics", "alert_type", "TEXT NOT NULL DEFAULT 'trade_candidate'")
        for column, definition in {
            "git_commit_hash": "TEXT",
            "strategy_version": "TEXT",
            "schema_version": "TEXT",
            "research_dataset_version": "TEXT",
            "config_hash": "TEXT",
            "distance_from_ema21": "REAL",
            "distance_from_ema50": "REAL",
            "distance_from_recent_swing_low": "REAL",
            "stop_to_swing_low_distance": "REAL",
            "bars_since_breakout": "INTEGER",
            "entry_open": "REAL",
            "entry_high": "REAL",
            "entry_low": "REAL",
            "entry_close": "REAL",
            "entry_volume": "REAL",
            "previous_open": "REAL",
            "previous_high": "REAL",
            "previous_low": "REAL",
            "previous_close": "REAL",
            "previous_volume": "REAL",
            "spy_state": "TEXT",
            "qqq_state": "TEXT",
            "vix_value": "REAL",
            "spread_at_entry": "REAL",
            "slippage_proxy": "REAL",
            "gap_flag": "INTEGER",
            "minutes_after_market_open": "REAL",
            "day_of_week": "INTEGER",
            "open_positions_count": "INTEGER",
            "open_positions_same_sector": "INTEGER",
        }.items():
            self._add_column_if_missing(conn, "signal_diagnostics", column, definition)
        for column, definition in {
            "breakout_distance_atr": "REAL",
            "distance_from_ema21": "REAL",
            "distance_from_ema50": "REAL",
            "distance_from_recent_swing_low": "REAL",
            "stop_to_swing_low_distance": "REAL",
            "bars_since_breakout": "INTEGER",
            "entry_open": "REAL",
            "entry_high": "REAL",
            "entry_low": "REAL",
            "entry_close": "REAL",
            "entry_volume": "REAL",
            "previous_open": "REAL",
            "previous_high": "REAL",
            "previous_low": "REAL",
            "previous_close": "REAL",
            "previous_volume": "REAL",
            "gap_flag": "INTEGER",
        }.items():
            self._add_column_if_missing(conn, "rejected_candidate_diagnostics", column, definition)
        for column, definition in {
            "time_to_mfe_seconds": "REAL",
            "time_to_mae_seconds": "REAL",
            "time_to_025r_seconds": "REAL",
            "time_to_050r_seconds": "REAL",
            "time_to_075r_seconds": "REAL",
            "time_to_100r_seconds": "REAL",
            "exit_price": "REAL",
            "exit_timestamp": "TEXT",
            "exit_atr": "REAL",
            "realized_r": "REAL",
        }.items():
            self._add_column_if_missing(conn, "signal_outcome_diagnostics", column, definition)

    def _add_column_if_missing(self, conn, table: str, column: str, definition: str) -> None:
        if self.is_sqlite:
            existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
            if column not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
            return
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = %s
                """,
                [table],
            )
            existing = {row["column_name"] for row in cursor.fetchall()}
            if column not in existing:
                cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {self._postgres_column_definition(definition)}")

    @staticmethod
    def _postgres_column_definition(definition: str) -> str:
        return definition.replace("REAL", "DOUBLE PRECISION")

    @contextlib.contextmanager
    def transaction(self) -> Iterator[None]:
        existing = self._transaction_conn.get()
        if existing is not None:
            yield
            return
        conn = self.connect()
        token = self._transaction_conn.set(conn)
        try:
            yield
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            self._transaction_conn.reset(token)
            conn.close()

    def execute(self, sql: str, params: Iterable[Any] = ()) -> int:
        conn = self._transaction_conn.get()
        own_conn = conn is None
        conn = conn or self.connect()
        try:
            adapted_sql, adapted_params = self.adapt_query(sql, params)
            if self.is_sqlite:
                cursor = conn.execute(adapted_sql, tuple(adapted_params))
                if own_conn:
                    conn.commit()
                return int(cursor.lastrowid or 0)
            with conn.cursor() as cursor:
                cursor.execute(adapted_sql, tuple(adapted_params))
                if own_conn:
                    conn.commit()
                return 0
        except Exception:
            if own_conn:
                conn.rollback()
            raise
        finally:
            if own_conn:
                conn.close()

    def executemany(self, sql: str, rows: Iterable[Iterable[Any]]) -> None:
        conn = self._transaction_conn.get()
        own_conn = conn is None
        conn = conn or self.connect()
        try:
            adapted_sql, _ = self.adapt_query(sql, ())
            if self.is_sqlite:
                conn.executemany(adapted_sql, [tuple(row) for row in rows])
            else:
                with conn.cursor() as cursor:
                    cursor.executemany(adapted_sql, [tuple(row) for row in rows])
            if own_conn:
                conn.commit()
        except Exception:
            if own_conn:
                conn.rollback()
            raise
        finally:
            if own_conn:
                conn.close()

    def fetch_one(self, sql: str, params: Iterable[Any] = ()) -> Optional[Any]:
        rows = self.fetch_all(sql, params)
        return rows[0] if rows else None

    def fetch_all(self, sql: str, params: Iterable[Any] = ()) -> list[Any]:
        conn = self._transaction_conn.get()
        own_conn = conn is None
        conn = conn or self.connect()
        try:
            adapted_sql, adapted_params = self.adapt_query(sql, params)
            if self.is_sqlite:
                return list(conn.execute(adapted_sql, tuple(adapted_params)).fetchall())
            with conn.cursor() as cursor:
                cursor.execute(adapted_sql, tuple(adapted_params))
                return list(cursor.fetchall())
        finally:
            if own_conn:
                conn.close()

    def insert_dict(self, table: str, payload: dict[str, Any]) -> int:
        columns = list(payload)
        values = [self._serialize(payload[column]) for column in columns]
        placeholders = ", ".join(["?"] * len(columns))
        column_sql = ", ".join(columns)
        if self.is_postgresql and table in ID_TABLES:
            sql = f"INSERT INTO {table} ({column_sql}) VALUES ({placeholders}) RETURNING id"
            conn = self._transaction_conn.get()
            own_conn = conn is None
            conn = conn or self.connect()
            try:
                adapted_sql, adapted_params = self.adapt_query(sql, values)
                with conn.cursor() as cursor:
                    cursor.execute(adapted_sql, tuple(adapted_params))
                    row = cursor.fetchone()
                if own_conn:
                    conn.commit()
                return int(row["id"]) if row and row.get("id") is not None else int(payload.get("id") or 0)
            except Exception:
                if own_conn:
                    conn.rollback()
                raise
            finally:
                if own_conn:
                    conn.close()
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

    def adapt_query(self, sql: str, params: Iterable[Any] = ()) -> tuple[str, tuple[Any, ...]]:
        params_tuple = tuple(params)
        if self.is_sqlite:
            return sql, params_tuple
        adapted = self._adapt_postgresql_sql(sql)
        return replace_qmark_placeholders(adapted), params_tuple

    @staticmethod
    def _adapt_postgresql_sql(sql: str) -> str:
        adapted = sql
        adapted = adapted.replace("datetime('now', ?)", "(CURRENT_TIMESTAMP + ?::interval)")
        adapted = adapted.replace('datetime("now", ?)', "(CURRENT_TIMESTAMP + ?::interval)")
        adapted = re.sub(r"datetime\('now'\s*,\s*'([^']+)'\)", r"(CURRENT_TIMESTAMP + INTERVAL '\1')", adapted)
        adapted = adapted.replace("datetime('now')", "CURRENT_TIMESTAMP")
        adapted = adapted.replace("date('now')", "CURRENT_DATE")
        adapted = re.sub(r"\bdate\(([A-Za-z_][A-Za-z0-9_\.]*)\)", r"(\1::date)", adapted)
        adapted = re.sub(r"\bdate\(([^)]+)\)", r"DATE(\1)", adapted)
        adapted = adapted.replace("(julianday('now') - julianday(created_at)) * 86400", "EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - created_at::timestamp))")
        adapted = adapted.replace("(julianday('now') - julianday(updated_at)) * 86400", "EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - updated_at::timestamp))")
        return adapted

    @staticmethod
    def _sanitize_error(exc: Exception) -> str:
        return exc.__class__.__name__

    @staticmethod
    def _serialize(value: Any) -> Any:
        if isinstance(value, (dict, list, tuple)):
            return json.dumps(value, default=str)
        return value


def replace_qmark_placeholders(sql: str) -> str:
    result = []
    in_single = False
    in_double = False
    idx = 0
    while idx < len(sql):
        char = sql[idx]
        if char == "'" and not in_double:
            in_single = not in_single
            result.append(char)
        elif char == '"' and not in_single:
            in_double = not in_double
            result.append(char)
        elif char == "?" and not in_single and not in_double:
            result.append("%s")
        else:
            result.append(char)
        idx += 1
    return "".join(result)


def split_sql_statements(sql: str) -> list[str]:
    statements: list[str] = []
    current: list[str] = []
    in_single = False
    in_double = False
    for char in sql:
        if char == "'" and not in_double:
            in_single = not in_single
        elif char == '"' and not in_single:
            in_double = not in_double
        if char == ";" and not in_single and not in_double:
            statement = "".join(current).strip()
            if statement:
                statements.append(statement)
            current = []
            continue
        current.append(char)
    tail = "".join(current).strip()
    if tail:
        statements.append(tail)
    return statements
