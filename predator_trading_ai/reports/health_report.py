from __future__ import annotations

from typing import Optional

import predator_trading_ai.alerts.telegram_bot as telegram_module
from predator_trading_ai.config import Settings, get_settings
from predator_trading_ai.database.db import Database
from predator_trading_ai.reports.monitor_status import MonitorStatusReport, Section, status_icon


class HealthReport:
    """Compact read-only runtime health dashboard built from monitor status checks."""

    def __init__(self, settings: Optional[Settings] = None, db: Optional[Database] = None) -> None:
        self.settings = settings or get_settings()
        self.db = db or Database(self.settings)
        self.monitor = MonitorStatusReport(self.settings, self.db)

    def build(self) -> str:
        sections = [
            self.monitor.safe_section("Scanner", self.monitor.scanner_section),
            self.monitor.safe_section("TP/SL Monitor", self.monitor.tp_sl_section),
            self.monitor.safe_section("ActiveSignalTracker", self.monitor.active_tracker_section),
            self.monitor.safe_section("Telegram", self.monitor.telegram_section),
            self.monitor.safe_section("Database", self.monitor.database_section),
            self.monitor.safe_section("Runtime", self.monitor.runtime_section),
        ]
        section_by_name = {section.name: section for section in sections}
        overall = self.health_status(section_by_name)
        return "\n".join(
            [
                f"{self.overall_icon(overall)} Predator Trading AI Health",
                "",
                "Overall:",
                f"{status_icon(overall)} {overall}",
                "",
                "Components",
                "",
                *self.scanner_lines(section_by_name["Scanner"]),
                "",
                *self.tp_sl_lines(section_by_name["TP/SL Monitor"]),
                "",
                *self.tracker_lines(section_by_name["ActiveSignalTracker"]),
                "",
                *self.telegram_lines(section_by_name["Telegram"]),
                "",
                *self.database_lines(section_by_name["Database"]),
                "",
                *self.runtime_lines(section_by_name["Runtime"]),
            ]
        ).strip()

    def health_status(self, sections: dict[str, Section]) -> str:
        if sections["Database"].status == "ERROR":
            return "ERROR"
        if sections["Scanner"].status == "ERROR":
            return "ERROR"
        if sections["ActiveSignalTracker"].status in {"ERROR", "UNKNOWN"}:
            return "ERROR"
        telegram_status = self.telegram_runtime_status(sections["Telegram"])
        if telegram_status == "ERROR":
            return "ERROR"
        if any(section.status == "ERROR" for section in sections.values() if section.name != "Telegram"):
            return "ERROR"
        if sections["Scanner"].status == "WARNING" or sections["TP/SL Monitor"].status == "WARNING":
            return "WARNING"
        if telegram_status == "WARNING":
            return "WARNING"
        return "HEALTHY"

    def telegram_runtime_status(self, section: Section) -> str:
        send_enabled = self.value(section, "- sendMessage enabled:") == "YES"
        enabled = self.value(section, "- Command polling enabled:") == "YES"
        started = self.value(section, "- Command polling started:") == "YES"
        conflict = self.value(section, "- Command polling disabled after Conflict:") == "YES"
        if not send_enabled:
            return "WARNING"
        if not enabled or conflict:
            return "WARNING"
        if enabled and not started and telegram_module.TELEGRAM_POLLING_SKIPPED_REASON != "duplicate_startup":
            return "ERROR"
        return "HEALTHY"

    def scanner_lines(self, section: Section) -> list[str]:
        return [
            "Scanner:",
            f"- Running: {self.value(section, '- Running:')}",
            f"- Market status: {self.value(section, '- Market status:')}",
            f"- Last heartbeat: {self.value(section, '- Main-loop heartbeat:')}",
            f"- Last completed scan: {self.value(section, '- Last completed scan:')}",
            f"- Watchlist symbols: {self.value(section, '- Total configured symbols:')}",
        ]

    def tp_sl_lines(self, section: Section) -> list[str]:
        return [
            "TP/SL Monitor:",
            f"- Running: {self.value(section, '- Running:')}",
            f"- Last heartbeat: {self.value(section, '- Last monitor cycle:')}",
            f"- Active monitored signals: {self.value(section, '- Active monitored signals:')}",
        ]

    def tracker_lines(self, section: Section) -> list[str]:
        return [
            "ActiveSignalTracker:",
            f"- Running: {self.value(section, '- Running:')}",
            f"- Active signals: {self.value(section, '- Active signals:')}",
        ]

    def telegram_lines(self, section: Section) -> list[str]:
        return [
            "Telegram:",
            f"- Polling: {self.telegram_polling_label(section)}",
            f"- sendMessage: {self.value(section, '- sendMessage enabled:')}",
            f"- Conflict status: {self.value(section, '- Command polling disabled after Conflict:')}",
        ]

    def database_lines(self, section: Section) -> list[str]:
        return [
            "Database:",
            f"- Connected: {self.value(section, '- Healthy:')}",
            f"- Backend: {self.value(section, '- Backend/type:')}",
            f"- Read test: {self.value(section, '- Read test:')}",
        ]

    def runtime_lines(self, section: Section) -> list[str]:
        return [
            "Runtime:",
            f"- Uptime: {self.value(section, '- Process uptime:')}",
            f"- Memory: {self.value(section, '- Memory usage:')}",
            f"- Git commit: {self.value(section, '- Runtime revision:')}",
        ]

    def telegram_polling_label(self, section: Section) -> str:
        if self.value(section, "- Command polling disabled after Conflict:") == "YES":
            return "CONFLICT"
        if self.value(section, "- Command polling enabled:") != "YES":
            return "DISABLED"
        if self.value(section, "- Command polling started:") == "YES":
            return "YES"
        if telegram_module.TELEGRAM_POLLING_SKIPPED_REASON == "duplicate_startup":
            return "YES"
        return "NO"

    @staticmethod
    def value(section: Section, prefix: str) -> str:
        for line in section.lines:
            if line.startswith(prefix):
                return line.split(":", 1)[1].strip()
        return "unknown"

    @staticmethod
    def overall_icon(status: str) -> str:
        if status == "HEALTHY":
            return "🟢"
        if status == "ERROR":
            return "🔴"
        return "🟡"
