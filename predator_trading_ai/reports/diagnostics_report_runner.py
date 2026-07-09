from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Optional

from predator_trading_ai.alerts.telegram_bot import TelegramAlertBot
from predator_trading_ai.config import Settings, get_settings
from predator_trading_ai.database.db import Database
from predator_trading_ai.reports.diagnostics_report import DiagnosticsReport
from predator_trading_ai.utils.logger import setup_logger


@dataclass(frozen=True)
class DiagnosticsReportRunResult:
    report: str
    sent: bool


class DiagnosticsReportRunner:
    def __init__(self, settings: Optional[Settings] = None, db: Optional[Database] = None, days: int = 7) -> None:
        self.settings = settings or get_settings()
        self.db = db or Database(self.settings)
        self.days = days
        self.logger = setup_logger(__name__, self.settings.log_level)

    def build(self) -> str:
        self.logger.info("DiagnosticsReportRunner building report days=%s.", self.days)
        self.db.initialize()
        return DiagnosticsReport(self.db, days=self.days).build()

    async def build_and_send(self) -> DiagnosticsReportRunResult:
        self.logger.info("DiagnosticsReportRunner sending report via sendMessage.")
        report = self.build()
        bot = TelegramAlertBot(self.settings, self.db)
        await bot.send_message(report)
        return DiagnosticsReportRunResult(report=report, sent=bool(bot.configured_chat_ids() and self.settings.telegram_bot_token))

    def build_and_send_sync(self) -> DiagnosticsReportRunResult:
        return asyncio.run(self.build_and_send())
