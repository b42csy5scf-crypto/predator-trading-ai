from __future__ import annotations

import os
import platform
import resource
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

import predator_trading_ai.alerts.telegram_bot as telegram_module
from predator_trading_ai.config import Settings, get_settings
from predator_trading_ai.database.db import Database
from predator_trading_ai.engines.signal_diagnostics import SignalDiagnosticsRecorder
from predator_trading_ai.utils.logger import setup_logger
from predator_trading_ai.utils.watchlist import parse_watchlist


EASTERN = ZoneInfo("America/New_York")
PROCESS_STARTED_AT = time.time()
RUNTIME_HEARTBEAT_HEALTHY_SECONDS = 120
RUNTIME_HEARTBEAT_WARNING_SECONDS = 300
COMMANDS = (
    "/report",
    "/diagnostics_report",
    "/research_report",
    "/research_report_7d",
    "/research_report_30d",
    "/monitor_status",
    "/health",
    "/rejected_examples",
    "/score_distribution",
    "/grade_trace",
    "/spread_forensics",
    "/signal_forensics",
)


@dataclass(frozen=True)
class Section:
    name: str
    lines: list[str]
    status: str = "UNKNOWN"


class MonitorStatusReport:
    """Builds an operational dashboard from persisted state without mutating trading state."""

    def __init__(self, settings: Optional[Settings] = None, db: Optional[Database] = None) -> None:
        self.settings = settings or get_settings()
        self.db = db or Database(self.settings)
        self.logger = setup_logger(__name__, self.settings.log_level)
        self.started_at = time.perf_counter()
        self.now = datetime.now(timezone.utc)
        self.scan_interval = max(int(self.settings.loop_interval_seconds or 300), 1)
        self.warnings: list[str] = []
        self.errors: list[str] = []
        self.heartbeat_items: list[str] = []
        self.db_ok = False

    def build(self) -> str:
        sections = [
            self.safe_section("Scanner", self.scanner_section),
            self.safe_section("TP/SL Monitor", self.tp_sl_section),
            self.safe_section("ActiveSignalTracker", self.active_tracker_section),
            self.safe_section("Telegram", self.telegram_section),
            self.safe_section("Database", self.database_section),
            self.safe_section("Research Dataset", self.research_dataset_section),
            self.safe_section("Runtime", self.runtime_section),
        ]
        overall = self.overall_status(sections)
        lines = ["Predator System Monitor", "", "Overall", f"- Status: {overall}", ""]
        for section in sections:
            lines.append(section.name)
            lines.extend(section.lines)
            lines.append("")
        lines.append("Warnings")
        lines.extend([f"- {warning}" for warning in self.warnings] or ["- none"])
        lines.append("")
        lines.append("Heartbeats")
        lines.extend(self.heartbeat_items or ["- none / unknown"])
        elapsed = time.perf_counter() - self.started_at
        self.logger.info("MonitorStatusReport built status=%s duration=%.3fs", overall, elapsed)
        return "\n".join(lines).strip()

    def safe_section(self, name: str, builder) -> Section:
        try:
            return builder()
        except Exception as exc:
            self.logger.exception("%s status query failed: %s", name, exc)
            self.warnings.append(f"{name} status unavailable.")
            return Section(name, ["- Healthy: UNKNOWN", "- Error: status query failed"], "UNKNOWN")

    def scanner_section(self) -> Section:
        process_instance = self.db_state("process_instance_id")
        process_started = self.db_state("process_started_at")
        main_heartbeat = self.current_process_timestamp(
            self.db_state("main_loop_heartbeat_at") or self.db_state("heartbeat_utc"),
            process_started,
            self.db_state("main_loop_process_instance_id"),
            process_instance,
        )
        market_status = (self.db_state("market_status") or "UNKNOWN").upper()
        next_check = self.db_state("next_market_check_at")
        next_check_age = seconds_until(next_check, self.now)
        last_completed_scan = self.current_process_timestamp(self.db_state("last_completed_scan_at"), process_started)
        last_scan_state = self.current_process_timestamp(self.runtime_last_scan_time(), process_started)
        last_snapshot = self.one(
            """
            SELECT *
            FROM universe_snapshot
            ORDER BY timestamp DESC, id DESC
            LIMIT 1
            """
        )
        today = self.scalar(
            """
            SELECT COUNT(*) AS count
            FROM universe_snapshot
            WHERE date(timestamp) = date('now')
            """,
            default=0,
        )
        last_snapshot_ts = self.current_process_timestamp(row_get(last_snapshot, "timestamp"), process_started)
        last_scan = last_completed_scan or last_snapshot_ts or last_scan_state
        main_age = age_seconds(main_heartbeat, self.now)
        scan_age = age_seconds(last_scan, self.now)
        status = self.scanner_status(market_status, main_age, scan_age, next_check_age)
        if status == "ERROR":
            if self.runtime_freshness_status(main_age) == "ERROR":
                self.errors.append("Main-loop heartbeat is stale.")
                self.warnings.append("Main-loop heartbeat is stale.")
                if market_status == "OPEN" and self.freshness_status(scan_age) == "ERROR":
                    self.warnings.append("No completed scan within the expected interval.")
            else:
                self.errors.append("Completed scans are overdue while market is open.")
                self.warnings.append("No completed scan within the expected interval.")
        elif status == "WARNING":
            self.warnings.append("Scanner heartbeat is delayed.")
        running = self.scanner_running_label(market_status, status, next_check_age)
        self.heartbeat_items.append(f"- Scanner: {age_label(main_age)} {status_icon(status)}")
        total_symbols = len(parse_watchlist(self.settings.watchlist))
        return Section(
            "Scanner",
            [
                f"- Running: {running}",
                f"- Main-loop heartbeat: {age_label(main_age)}",
                f"- Last completed scan: {last_scan or 'n/a for current process'}",
                f"- Seconds since completed scan: {seconds_label(scan_age)}",
                f"- Market status: {market_status}",
                f"- Next market check: {next_check or 'n/a'}",
                f"- Seconds until next check: {seconds_until_label(next_check_age)}",
                f"- Configured scan interval: {self.scan_interval}s",
                f"- Symbols scanned last cycle: {row_get(last_snapshot, 'symbols_scanned', 'n/a')}",
                f"- Total configured symbols: {total_symbols}",
                f"- Last scan cycle status: {self.db_state('last_scan_cycle_status') or self.scan_status(last_snapshot)}",
                f"- Scan cycles today: {today}",
                f"- Last API/data failures: {self.failure_count(last_snapshot)}",
            ],
            status,
        )

    def tp_sl_section(self) -> Section:
        active_total = self.scalar("SELECT COUNT(*) AS count FROM active_signals WHERE status = 'active'", default=0)
        active_trade = self.scalar(
            """
            SELECT COUNT(*) AS count
            FROM active_signals
            WHERE status = 'active' AND alert_type = 'trade_candidate'
            """,
            default=0,
        )
        active_b = self.scalar(
            """
            SELECT COUNT(*) AS count
            FROM active_signals
            WHERE status = 'active' AND alert_type = 'experimental_watch'
            """,
            default=0,
        )
        process_instance = self.db_state("process_instance_id")
        process_started = self.db_state("process_started_at")
        heartbeat = self.current_process_timestamp(
            self.db_state("tp_sl_monitor_heartbeat_at") or self.db_state("tp_sl_monitor_heartbeat_utc"),
            process_started,
            self.db_state("tp_sl_monitor_process_instance_id"),
            process_instance,
        )
        monitor_state = (self.db_state("tp_sl_monitor_state") or "").upper()
        monitor_age = age_seconds(heartbeat, self.now)
        monitor_status = self.runtime_freshness_status(monitor_age)
        running = "IDLE" if active_total == 0 and monitor_status == "HEALTHY" else monitor_state or self.running_label(monitor_age, heartbeat is None)
        if active_total > 0 and monitor_status == "ERROR":
            self.errors.append("TP/SL monitor is stale while active signals exist.")
            self.warnings.append("TP/SL monitor heartbeat is delayed.")
        elif active_total == 0 and monitor_status == "UNKNOWN":
            self.warnings.append("No active signals currently exist; TP/SL event delivery cannot be empirically confirmed yet.")
        elif monitor_status == "WARNING":
            self.warnings.append("TP/SL monitor heartbeat is delayed.")
        path_today = self.scalar(
            "SELECT COUNT(*) AS count FROM price_path WHERE date(timestamp) = date('now')",
            default=0,
        )
        latest_path = self.scalar("SELECT MAX(timestamp) AS value FROM price_path", default=None)
        path_age = age_seconds(latest_path, self.now)
        path_status = self.freshness_status(path_age)
        if active_total > 0 and path_status in {"WARNING", "ERROR"}:
            self.warnings.append("Active signals exist but price updates are stale.")
        self.heartbeat_items.append(f"- TP/SL monitor: {age_label(monitor_age)} {status_icon(monitor_status)}")
        if active_total == 0:
            self.heartbeat_items.append("- Price path: not applicable / no active signals")
        else:
            self.heartbeat_items.append(f"- Price path: {age_label(path_age)} {status_icon(path_status)}")
        return Section(
            "TP/SL Monitor",
            [
                f"- Running: {running}",
                f"- Last monitor cycle: {heartbeat or 'never / unknown'}",
                f"- Seconds since monitor cycle: {seconds_label(monitor_age)}",
                f"- Active monitored signals: {active_total}",
                f"- Active A/A+/A++: {active_trade}",
                f"- Active Strong B Experimental Watch: {active_b}",
                f"- Price-path rows today: {path_today}",
                f"- Latest price-path update: {latest_path or 'n/a'}",
                f"- Seconds since price-path update: {seconds_label(path_age)}",
                f"- Last TP1 event: {self.last_update('tp1')}",
                f"- Last TP2 event: {self.last_update('tp2')}",
                f"- Last TP3 event: {self.last_update('tp3')}",
                f"- Last Stop Loss event: {self.last_update('stop_loss')}",
                f"- Last Breakeven event: {self.last_update('breakeven')}",
            ],
            "ERROR" if active_total > 0 and monitor_status == "ERROR" else monitor_status,
        )

    def active_tracker_section(self) -> Section:
        active = self.scalar("SELECT COUNT(*) AS count FROM active_signals WHERE status = 'active'", default=0)
        added_today = self.scalar("SELECT COUNT(*) AS count FROM active_signals WHERE date(created_at) = date('now')", default=0)
        removed_today = self.scalar(
            "SELECT COUNT(*) AS count FROM active_signals WHERE status = 'closed' AND date(closed_at) = date('now')",
            default=0,
        )
        completed_today = self.scalar("SELECT COUNT(*) AS count FROM completed_trades WHERE date(created_at) = date('now')", default=0)
        last_added = self.scalar("SELECT MAX(created_at) AS value FROM active_signals", default=None)
        last_removed = self.one("SELECT closed_at, close_reason FROM active_signals WHERE closed_at IS NOT NULL ORDER BY closed_at DESC LIMIT 1")
        process_instance = self.db_state("process_instance_id")
        process_started = self.db_state("process_started_at")
        tracker_started = self.current_process_timestamp(
            self.db_state("tracker_started_at"),
            process_started,
            self.db_state("tracker_process_instance_id"),
            process_instance,
        )
        tracker_running = bool(tracker_started) and (self.db_state("tracker_running") or "").lower() == "true"
        heartbeat = tracker_started or self.current_process_timestamp(
            self.db_state("tp_sl_monitor_heartbeat_at") or self.db_state("tp_sl_monitor_heartbeat_utc"),
            process_started,
            self.db_state("tp_sl_monitor_process_instance_id"),
            process_instance,
        )
        age = age_seconds(heartbeat, self.now)
        status = "HEALTHY" if tracker_running else "WARNING" if heartbeat else "UNKNOWN"
        running = "YES" if tracker_running else "UNKNOWN"
        if not tracker_running:
            self.warnings.append("ActiveSignalTracker startup heartbeat is unavailable for the current process.")
        return Section(
            "ActiveSignalTracker",
            [
                f"- Running: {running}",
                f"- Active signals: {active}",
                f"- Tracker started: {tracker_started or 'n/a'}",
                f"- Signals added today: {added_today}",
                f"- Signals removed today: {removed_today}",
                f"- Completed trades today: {completed_today}",
                f"- Last signal added: {last_added or 'n/a'}",
                f"- Last signal removed: {row_get(last_removed, 'closed_at', 'n/a')}",
                f"- Last removal reason: {self.removal_reason(row_get(last_removed, 'close_reason'))}",
            ],
            status,
        )

    def telegram_section(self) -> Section:
        send_enabled = bool(self.settings.telegram_bot_token and self.configured_chat_count() > 0)
        command_enabled = bool(self.settings.enable_telegram_polling)
        polling_started = bool(telegram_module.TELEGRAM_POLLING_STARTED)
        conflict = telegram_module.TELEGRAM_POLLING_SKIPPED_REASON == "conflict_detected"
        if conflict:
            self.warnings.append("Telegram command polling is disabled after Conflict; sendMessage alerts remain available.")
        self.heartbeat_items.append(f"- Telegram sendMessage: {'enabled' if send_enabled else 'disabled'} {status_icon('HEALTHY' if send_enabled else 'WARNING')}")
        self.heartbeat_items.append(f"- Command polling: {'enabled' if command_enabled else 'disabled'} {status_icon('HEALTHY' if command_enabled else 'WARNING')}")
        return Section(
            "Telegram",
            [
                f"- sendMessage enabled: {yes_no(send_enabled)}",
                f"- Command polling enabled: {yes_no(command_enabled)}",
                f"- Command polling started: {yes_no(polling_started)}",
                f"- Command polling disabled after Conflict: {yes_no(conflict)}",
                f"- Conflict caught: {yes_no(conflict or bool(telegram_module.TELEGRAM_POLLING_DISABLED_REASON))}",
                "- Last successful command timestamp: n/a",
                "- Last successful sendMessage timestamp: n/a",
                f"- Available commands: {', '.join(COMMANDS)}",
            ],
            "WARNING" if conflict or not send_enabled else "HEALTHY",
        )

    def database_section(self) -> Section:
        try:
            self.scalar("SELECT 1 AS value", default=1)
            self.db_ok = True
        except Exception:
            self.db_ok = False
            self.errors.append("Database read failed.")
            self.warnings.append("Database read failed.")
            self.heartbeat_items.append("- Database: FAILED ❌")
            return Section("Database", ["- Healthy: NO", "- Read test: FAILED"], "ERROR")
        latest_diag = self.scalar("SELECT MAX(created_at) AS value FROM signal_diagnostics", default=None)
        latest_path = self.scalar("SELECT MAX(timestamp) AS value FROM price_path", default=None)
        price_path_today = self.scalar(
            "SELECT COUNT(*) AS count FROM price_path WHERE date(timestamp) = date('now')",
            default=0,
        )
        self.heartbeat_items.append("- Database: OK ✅")
        return Section(
            "Database",
            [
                "- Healthy: YES",
                "- Read test: OK",
                f"- Backend/type: {self.database_backend()}",
                f"- Persistent database: {self.persistent_database_label()}",
                f"- Connection scope: {self.connection_scope_label()}",
                f"- Last diagnostics write: {latest_diag or 'n/a'}",
                f"- Last price_path write: {latest_path or 'n/a'}",
                f"- Active signal rows: {self.scalar('SELECT COUNT(*) AS count FROM active_signals', default=0)}",
                f"- Completed trade rows: {self.scalar('SELECT COUNT(*) AS count FROM completed_trades', default=0)}",
                f"- Signal diagnostics recent: {self.count_recent('signal_diagnostics')}",
                f"- Rejected diagnostics recent: {self.count_recent('rejected_candidate_diagnostics')}",
                f"- Price-path rows today: {price_path_today}",
                f"- Database size: {self.database_size_label()}",
            ],
            "HEALTHY",
        )

    def research_dataset_section(self) -> Section:
        latest_diag = self.one("SELECT * FROM signal_diagnostics ORDER BY created_at DESC LIMIT 1")
        return Section(
            "Research Dataset",
            [
                f"- Research dataset version: {row_get(latest_diag, 'research_dataset_version', SignalDiagnosticsRecorder.RESEARCH_DATASET_VERSION)}",
                f"- Schema version: {row_get(latest_diag, 'schema_version', SignalDiagnosticsRecorder.SCHEMA_VERSION)}",
                f"- Strategy version: {row_get(latest_diag, 'strategy_version', SignalDiagnosticsRecorder.STRATEGY_VERSION)}",
                f"- Current Git commit: {self.git_commit()}",
                f"- Config hash: {row_get(latest_diag, 'config_hash', 'n/a') or 'n/a'}",
                f"- Latest universe_snapshot: {self.scalar('SELECT MAX(timestamp) AS value FROM universe_snapshot', default=None) or 'n/a'}",
                f"- Latest signal_diagnostics: {self.scalar('SELECT MAX(created_at) AS value FROM signal_diagnostics', default=None) or 'n/a'}",
                f"- Latest signal_outcome_diagnostics: {self.scalar('SELECT MAX(updated_at) AS value FROM signal_outcome_diagnostics', default=None) or 'n/a'}",
                f"- Latest rejected_candidate_diagnostics: {self.scalar('SELECT MAX(created_at) AS value FROM rejected_candidate_diagnostics', default=None) or 'n/a'}",
                f"- Latest price_path: {self.scalar('SELECT MAX(timestamp) AS value FROM price_path', default=None) or 'n/a'}",
            ],
            "HEALTHY",
        )

    def runtime_section(self) -> Section:
        return Section(
            "Runtime",
            [
                f"- Process uptime: {self.process_uptime_label()}",
                f"- Service role: {self.settings.service_role}",
                f"- Railway environment: {os.getenv('RAILWAY_ENVIRONMENT_NAME') or os.getenv('RAILWAY_ENVIRONMENT') or 'n/a'}",
                f"- Current UTC: {self.now.isoformat()}",
                f"- Current New York: {datetime.now(EASTERN).isoformat()}",
                f"- Memory usage: {self.memory_usage_label()}",
                "- CPU usage: n/a",
                f"- Runtime revision: {self.git_commit()}",
                f"- Python: {platform.python_version()}",
            ],
            "HEALTHY",
        )

    def overall_status(self, sections: list[Section]) -> str:
        if self.errors or any(section.status == "ERROR" for section in sections if section.name != "Telegram"):
            return "❌ ERROR"
        degrading_warnings = [warning for warning in self.warnings if not self.is_informational_warning(warning)]
        if degrading_warnings or any(section.status == "WARNING" for section in sections):
            return "⚠️ WARNING"
        return "✅ HEALTHY"

    @staticmethod
    def is_informational_warning(warning: str) -> bool:
        return warning.startswith("No active signals currently exist")

    def scanner_status(
        self,
        market_status: str,
        main_age: Optional[float],
        scan_age: Optional[float],
        next_check_seconds: Optional[float],
    ) -> str:
        main_status = self.runtime_freshness_status(main_age)
        if market_status == "CLOSED" and next_check_seconds is not None and next_check_seconds >= 0:
            if main_status in {"HEALTHY", "WARNING", "ERROR"}:
                return main_status
            return "WARNING"
        if main_status == "ERROR":
            return "ERROR"
        if market_status == "OPEN" and self.freshness_status(scan_age) == "ERROR":
            return "ERROR"
        if main_status == "WARNING":
            return "WARNING"
        return "HEALTHY"

    @staticmethod
    def scanner_running_label(market_status: str, status: str, next_check_seconds: Optional[float]) -> str:
        if market_status == "CLOSED" and next_check_seconds is not None and next_check_seconds >= 0:
            return "IDLE / MARKET CLOSED"
        if status == "HEALTHY":
            return "YES"
        if status == "WARNING":
            return "UNKNOWN"
        return "NO"

    def freshness_status(self, seconds: Optional[float]) -> str:
        if seconds is None:
            return "UNKNOWN"
        if seconds <= 2 * self.scan_interval:
            return "HEALTHY"
        if seconds <= 4 * self.scan_interval:
            return "WARNING"
        return "ERROR"

    @staticmethod
    def runtime_freshness_status(seconds: Optional[float]) -> str:
        if seconds is None:
            return "UNKNOWN"
        if seconds <= RUNTIME_HEARTBEAT_HEALTHY_SECONDS:
            return "HEALTHY"
        if seconds <= RUNTIME_HEARTBEAT_WARNING_SECONDS:
            return "WARNING"
        return "ERROR"

    def running_label(self, seconds: Optional[float], allow_unknown: bool = False) -> str:
        if allow_unknown or seconds is None:
            return "UNKNOWN"
        status = self.freshness_status(seconds)
        if status == "HEALTHY":
            return "YES"
        if status == "WARNING":
            return "UNKNOWN"
        return "NO"

    def scan_status(self, row: Any) -> str:
        if row is None:
            return "UNKNOWN"
        failures = self.failure_count(row)
        return "OK" if failures == 0 else f"WARNING failures={failures}"

    @staticmethod
    def failure_count(row: Any) -> int | str:
        if row is None:
            return "n/a"
        return int(row_get(row, "api_failures", 0) or 0) + int(row_get(row, "missing_market_data", 0) or 0)

    def last_update(self, update_type: str) -> str:
        row = self.one(
            """
            SELECT created_at, ticker
            FROM signal_updates
            WHERE update_type = ?
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            [update_type],
        )
        if row is None:
            return "n/a"
        return f"{row['created_at']} {row['ticker']}"

    @staticmethod
    def removal_reason(value: Any) -> str:
        mapping = {
            "tp3_completed": "TP_HIT",
            "invalidated": "SL_HIT",
            "stop_loss": "SL_HIT",
            "breakeven_after_tp1": "BREAKEVEN",
            "superseded": "DUPLICATE",
        }
        if not value:
            return "n/a"
        return mapping.get(str(value), str(value).upper())

    def database_backend(self) -> str:
        backend = getattr(self.db, "backend", None)
        if backend:
            return str(backend)
        return "sqlite" if self.settings.database_url.startswith("sqlite") else "postgresql"

    def persistent_database_label(self) -> str:
        return "YES" if self.database_backend() == "postgresql" else "NO / LOCAL"

    def connection_scope_label(self) -> str:
        if self.database_backend() == "sqlite":
            return "local"
        value = str(self.settings.database_url)
        lowered = value.lower()
        if "railway.internal" in lowered or ".internal" in lowered:
            return "Railway private network"
        if value.startswith(("postgresql://", "postgres://")):
            return "external"
        return "unknown"

    def database_size_label(self) -> str:
        if not self.settings.database_url.startswith("sqlite:///"):
            return "n/a"
        try:
            path = self.db.path
            return f"{path.stat().st_size} bytes" if path.exists() else "n/a"
        except Exception:
            return "n/a"

    def configured_chat_count(self) -> int:
        primary = split_chat_ids(getattr(self.settings, "telegram_chat_id", None))
        if primary:
            return len(primary)
        return len(
            list(
                dict.fromkeys(
                    [
                        *split_chat_ids(getattr(self.settings, "telegram_chat_id_1", None)),
                        *split_chat_ids(getattr(self.settings, "telegram_chat_id_2", None)),
                    ]
                )
            )
        )

    def count_recent(self, table: str, days: int = 7) -> int:
        return int(
            self.scalar(
                f"SELECT COUNT(*) AS count FROM {table} WHERE created_at >= datetime('now', ?)",
                [f"-{days} days"],
                default=0,
            )
            or 0
        )

    def db_state(self, key: str) -> Optional[str]:
        row = self.one("SELECT value FROM system_state WHERE key = ?", [key])
        return str(row["value"]) if row is not None and row["value"] else None

    def runtime_last_scan_time(self) -> Optional[str]:
        try:
            from predator_trading_ai.state.runtime_state import RuntimeStateStore

            return RuntimeStateStore().load().last_scan_time
        except Exception:
            return None

    def current_process_timestamp(
        self,
        value: Any,
        process_started: Any,
        component_process_id: Any = None,
        current_process_id: Any = None,
    ) -> Optional[str]:
        if not value:
            return None
        if component_process_id and current_process_id and str(component_process_id) != str(current_process_id):
            return None
        started = parse_timestamp(process_started)
        timestamp = parse_timestamp(value)
        if started is not None and timestamp is not None and timestamp < started:
            return None
        return str(value)

    def process_uptime_label(self) -> str:
        process_started = parse_timestamp(self.db_state("process_started_at"))
        if process_started is not None:
            return duration_label(max((self.now - process_started).total_seconds(), 0.0))
        return duration_label(time.time() - PROCESS_STARTED_AT)

    def git_commit(self) -> str:
        for key in ("RAILWAY_GIT_COMMIT_SHA", "RAILWAY_GIT_COMMIT", "SOURCE_COMMIT", "GIT_COMMIT_SHA"):
            value = os.getenv(key)
            if value:
                return value[:12]
        try:
            completed = subprocess.run(
                ["git", "rev-parse", "--short=12", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
                timeout=2,
            )
            return completed.stdout.strip()
        except Exception:
            return "unknown"

    @staticmethod
    def memory_usage_label() -> str:
        try:
            usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            if platform.system() == "Darwin":
                return f"{usage / 1024 / 1024:.1f} MB"
            return f"{usage / 1024:.1f} MB"
        except Exception:
            return "n/a"

    def one(self, sql: str, params: list[Any] | tuple[Any, ...] = ()) -> Any:
        rows = self.db.fetch_all(sql, params)
        return rows[0] if rows else None

    def scalar(self, sql: str, params: list[Any] | tuple[Any, ...] = (), default: Any = None) -> Any:
        row = self.one(sql, params)
        if row is None:
            return default
        if "value" in row.keys():
            return row["value"]
        if "count" in row.keys():
            return row["count"]
        return row[0]


def row_get(row: Any, key: str, default: Any = None) -> Any:
    if row is None:
        return default
    try:
        return row[key]
    except (KeyError, IndexError):
        return default


def parse_timestamp(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def age_seconds(value: Any, now: datetime) -> Optional[float]:
    parsed = parse_timestamp(value)
    if parsed is None:
        return None
    return max((now - parsed).total_seconds(), 0.0)


def seconds_until(value: Any, now: datetime) -> Optional[float]:
    parsed = parse_timestamp(value)
    if parsed is None:
        return None
    return (parsed - now).total_seconds()


def seconds_label(value: Optional[float]) -> str:
    return "unknown" if value is None else f"{int(value)}"


def seconds_until_label(value: Optional[float]) -> str:
    return "unknown" if value is None else f"{max(int(value), 0)}"


def age_label(value: Optional[float]) -> str:
    if value is None:
        return "never / unknown"
    if value < 90:
        return f"{int(value)} sec ago"
    return f"{int(value // 60)} min ago"


def duration_label(seconds: float) -> str:
    if seconds < 90:
        return f"{int(seconds)}s"
    if seconds < 7200:
        return f"{int(seconds // 60)}m"
    return f"{seconds / 3600:.1f}h"


def status_icon(status: str) -> str:
    if status == "HEALTHY":
        return "✅"
    if status == "ERROR":
        return "❌"
    return "⚠️"


def yes_no(value: bool) -> str:
    return "YES" if value else "NO"


def split_chat_ids(value: Optional[str]) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in str(value).split(",") if part.strip()]
