from __future__ import annotations

import argparse
import os
import sqlite3
from pathlib import Path
from typing import Any

from predator_trading_ai.config import Settings
from predator_trading_ai.database.db import Database, ID_TABLES


MIGRATION_ORDER = [
    "signals",
    "trades",
    "backtest_results",
    "options_flow",
    "sentiment_data",
    "market_regime",
    "strategy_versions",
    "performance_metrics",
    "system_state",
    "health_events",
    "shadow_signals",
    "rejected_signals",
    "forward_test_results",
    "sent_alerts",
    "active_signals",
    "signal_updates",
    "alert_daily_limits",
    "completed_trades",
    "signal_diagnostics",
    "rejected_candidate_diagnostics",
    "signal_outcome_diagnostics",
    "price_path",
    "universe_snapshot",
    "config_snapshots",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Safely migrate Predator SQLite data into configured PostgreSQL.")
    parser.add_argument("--sqlite-path", required=True, type=Path, help="Path to source SQLite database.")
    parser.add_argument("--database-url-env", default="DATABASE_URL", help="Environment variable containing PostgreSQL DATABASE_URL.")
    parser.add_argument("--dry-run", action="store_true", help="Validate and print row counts without writing.")
    return parser.parse_args()


def sqlite_connect_readonly(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise FileNotFoundError(f"Source SQLite database not found: {path}")
    uri = f"file:{path.resolve()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def source_tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {str(row["name"]) for row in rows}


def table_rows(conn: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    rows = conn.execute(f"SELECT * FROM {table}").fetchall()
    return [dict(row) for row in rows]


def migrate_table(dest: Database, table: str, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    columns = list(rows[0])
    placeholders = ", ".join(["?"] * len(columns))
    column_sql = ", ".join(columns)
    sql = f"INSERT INTO {table} ({column_sql}) VALUES ({placeholders}) ON CONFLICT DO NOTHING"
    for row in rows:
        dest.execute(sql, [row[column] for column in columns])
    if table in ID_TABLES and "id" in columns and dest.is_postgresql:
        dest.execute(
            "SELECT setval(pg_get_serial_sequence(?, 'id'), COALESCE((SELECT MAX(id) FROM " + table + "), 1), true)",
            [table],
        )


def main() -> None:
    args = parse_args()
    database_url = os.getenv(args.database_url_env)
    if not database_url:
        raise SystemExit(f"{args.database_url_env} is not set.")
    if not database_url.startswith(("postgresql://", "postgres://")):
        raise SystemExit(f"{args.database_url_env} must point to PostgreSQL.")
    with sqlite_connect_readonly(args.sqlite_path) as source:
        available = source_tables(source)
        counts: dict[str, int | str] = {}
        for table in MIGRATION_ORDER:
            if table not in available:
                counts[table] = "unavailable"
                continue
            counts[table] = len(table_rows(source, table))
        print("SQLite to PostgreSQL migration plan:")
        for table, count in counts.items():
            print(f"- {table}: {count}")
        if args.dry_run:
            print("Dry run complete. No destination writes performed.")
            return
        dest = Database(Settings(database_url=database_url))
        dest.initialize()
        try:
            with dest.transaction():
                for table in MIGRATION_ORDER:
                    if table not in available:
                        continue
                    migrate_table(dest, table, table_rows(source, table))
        except Exception as exc:
            raise SystemExit(f"Migration failed; destination transaction rolled back: {exc.__class__.__name__}") from exc
        print("Migration complete.")


if __name__ == "__main__":
    main()
