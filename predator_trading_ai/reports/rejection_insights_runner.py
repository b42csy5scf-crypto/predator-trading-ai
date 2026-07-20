from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Optional

from predator_trading_ai.alerts.telegram_bot import TelegramAlertBot
from predator_trading_ai.config import Settings, get_settings
from predator_trading_ai.database.db import Database
from predator_trading_ai.reports.rejection_insights import RejectionInsightsReport
from predator_trading_ai.utils.logger import setup_logger


@dataclass(frozen=True)
class RejectionInsightsRunResult:
    report: str
    sent: bool


class RejectionInsightsRunner:
    def __init__(self, settings: Optional[Settings] = None, db: Optional[Database] = None) -> None:
        self.settings = settings or get_settings()
        self.db = db or Database(self.settings)
        self.logger = setup_logger(__name__, self.settings.log_level)

    def rejected_examples(self, limit: int = 10) -> str:
        self.logger.info("RejectionInsightsRunner building rejected examples read-only report.")
        return RejectionInsightsReport(self.settings, self.db).rejected_examples(limit=limit)

    def score_distribution(self, period: str = "today") -> str:
        self.logger.info("RejectionInsightsRunner building score distribution read-only report period=%s.", period)
        return RejectionInsightsReport(self.settings, self.db).score_distribution(period_arg=period)

    async def send_rejected_examples(self, limit: int = 10) -> RejectionInsightsRunResult:
        report = self.rejected_examples(limit=limit)
        bot = TelegramAlertBot(self.settings, self.db)
        await bot.send_message(report)
        return RejectionInsightsRunResult(report=report, sent=bool(bot.configured_chat_ids() and self.settings.telegram_bot_token))

    async def send_score_distribution(self, period: str = "today") -> RejectionInsightsRunResult:
        report = self.score_distribution(period=period)
        bot = TelegramAlertBot(self.settings, self.db)
        await bot.send_message(report)
        return RejectionInsightsRunResult(report=report, sent=bool(bot.configured_chat_ids() and self.settings.telegram_bot_token))

    def send_rejected_examples_sync(self, limit: int = 10) -> RejectionInsightsRunResult:
        return asyncio.run(self.send_rejected_examples(limit=limit))

    def send_score_distribution_sync(self, period: str = "today") -> RejectionInsightsRunResult:
        return asyncio.run(self.send_score_distribution(period=period))
