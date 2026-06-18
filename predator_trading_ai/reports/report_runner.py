from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Optional

from predator_trading_ai.alerts.telegram_bot import TelegramAlertBot
from predator_trading_ai.config import Settings, get_settings
from predator_trading_ai.database.db import Database
from predator_trading_ai.reports.trade_performance_report import TradePerformanceReport


@dataclass(frozen=True)
class ReportRunResult:
    report: str
    sent: bool


class PerformanceReportRunner:
    def __init__(self, settings: Optional[Settings] = None, db: Optional[Database] = None) -> None:
        self.settings = settings or get_settings()
        self.db = db or Database(self.settings)

    def build(self) -> str:
        self.db.initialize()
        return TradePerformanceReport(self.db).build()

    async def build_and_send(self) -> ReportRunResult:
        report = self.build()
        bot = TelegramAlertBot(self.settings, self.db)
        await bot.send_message(report)
        return ReportRunResult(report=report, sent=bool(bot.configured_chat_ids() and self.settings.telegram_bot_token))

    def build_and_send_sync(self) -> ReportRunResult:
        return asyncio.run(self.build_and_send())
