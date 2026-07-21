from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Optional

from predator_trading_ai.alerts.telegram_bot import TelegramAlertBot
from predator_trading_ai.config import Settings, get_settings
from predator_trading_ai.database.db import Database
from predator_trading_ai.reports.production_audit import ProductionAuditReport
from predator_trading_ai.utils.logger import setup_logger


@dataclass(frozen=True)
class ProductionAuditRunResult:
    report: str
    sent: bool


class ProductionAuditRunner:
    def __init__(self, settings: Optional[Settings] = None, db: Optional[Database] = None) -> None:
        self.settings = settings or get_settings()
        self.db = db or Database(self.settings)
        self.logger = setup_logger(__name__, self.settings.log_level)

    def grade_trace(self, limit: int = 10) -> str:
        self.logger.info("ProductionAuditRunner building read-only grade trace limit=%s.", limit)
        return ProductionAuditReport(self.settings, self.db).grade_trace(limit=limit)

    def spread_forensics(self, ticker: str, limit: int = 5) -> str:
        self.logger.info("ProductionAuditRunner building read-only spread forensics ticker=%s limit=%s.", ticker, limit)
        return ProductionAuditReport(self.settings, self.db).spread_forensics(ticker=ticker, limit=limit)

    async def send_grade_trace(self, limit: int = 10) -> ProductionAuditRunResult:
        report = self.grade_trace(limit=limit)
        bot = TelegramAlertBot(self.settings, self.db)
        await bot.send_message(report)
        return ProductionAuditRunResult(report=report, sent=bool(bot.configured_chat_ids() and self.settings.telegram_bot_token))

    async def send_spread_forensics(self, ticker: str, limit: int = 5) -> ProductionAuditRunResult:
        report = self.spread_forensics(ticker=ticker, limit=limit)
        bot = TelegramAlertBot(self.settings, self.db)
        await bot.send_message(report)
        return ProductionAuditRunResult(report=report, sent=bool(bot.configured_chat_ids() and self.settings.telegram_bot_token))

    def send_grade_trace_sync(self, limit: int = 10) -> ProductionAuditRunResult:
        return asyncio.run(self.send_grade_trace(limit=limit))

    def send_spread_forensics_sync(self, ticker: str, limit: int = 5) -> ProductionAuditRunResult:
        return asyncio.run(self.send_spread_forensics(ticker=ticker, limit=limit))
