from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from predator_trading_ai.alerts.telegram_bot import TelegramAlertBot
from predator_trading_ai.config import Settings, get_settings
from predator_trading_ai.database.db import Database
from predator_trading_ai.reports.research_report import ResearchReport
from predator_trading_ai.utils.logger import setup_logger


@dataclass(frozen=True)
class ResearchReportRunResult:
    report: str
    sent: bool


class ResearchReportRunner:
    def __init__(self, settings: Optional[Settings] = None, db: Optional[Database] = None, days: int = 30) -> None:
        self.settings = settings or get_settings()
        self.db = db or Database(self.settings)
        self.days = max(int(days), 1)
        self.logger = setup_logger(__name__, self.settings.log_level)

    def build(self) -> str:
        self.logger.info("ResearchReportRunner building read-only report days=%s.", self.days)
        return ResearchReport(self.db, days=self.days).build()

    def build_json(self) -> str:
        self.logger.info("ResearchReportRunner building read-only JSON report days=%s.", self.days)
        return ResearchReport(self.db, days=self.days).export_json()

    def export_csv(self, directory: Path) -> None:
        self.logger.info("ResearchReportRunner exporting read-only CSV report days=%s dir=%s.", self.days, directory)
        ResearchReport(self.db, days=self.days).export_csv(directory)

    async def build_and_send(self) -> ResearchReportRunResult:
        self.logger.info("ResearchReportRunner sending report via sendMessage.")
        report = self.build()
        bot = TelegramAlertBot(self.settings, self.db)
        await bot.send_message(report)
        return ResearchReportRunResult(report=report, sent=bool(bot.configured_chat_ids() and self.settings.telegram_bot_token))

    def build_and_send_sync(self) -> ResearchReportRunResult:
        return asyncio.run(self.build_and_send())
