from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Optional

from predator_trading_ai.alerts.telegram_bot import TelegramAlertBot
from predator_trading_ai.config import Settings, get_settings
from predator_trading_ai.database.db import Database
from predator_trading_ai.reports.signal_forensics import SignalForensicsReport
from predator_trading_ai.utils.logger import setup_logger


@dataclass(frozen=True)
class SignalForensicsRunResult:
    report: str
    sent: bool


class SignalForensicsRunner:
    def __init__(self, settings: Optional[Settings] = None, db: Optional[Database] = None) -> None:
        self.settings = settings or get_settings()
        self.db = db or Database(self.settings)
        self.logger = setup_logger(__name__, self.settings.log_level)

    def build(self, ticker: str, limit: int = 3) -> str:
        self.logger.info("SignalForensicsRunner building read-only signal forensics ticker=%s limit=%s.", ticker, limit)
        return SignalForensicsReport(self.settings, self.db).build(ticker=ticker, limit=limit)

    async def send_signal_forensics(self, ticker: str, limit: int = 3) -> SignalForensicsRunResult:
        report = self.build(ticker=ticker, limit=limit)
        bot = TelegramAlertBot(self.settings, self.db)
        await bot.send_message(report)
        return SignalForensicsRunResult(report=report, sent=bool(bot.configured_chat_ids() and self.settings.telegram_bot_token))

    def send_signal_forensics_sync(self, ticker: str, limit: int = 3) -> SignalForensicsRunResult:
        return asyncio.run(self.send_signal_forensics(ticker=ticker, limit=limit))
