from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Optional

from predator_trading_ai.alerts.telegram_bot import TelegramAlertBot
from predator_trading_ai.config import Settings, get_settings
from predator_trading_ai.database.db import Database
from predator_trading_ai.reports.monitor_status import MonitorStatusReport
from predator_trading_ai.utils.logger import setup_logger


@dataclass(frozen=True)
class MonitorStatusRunResult:
    report: str
    sent: bool


class MonitorStatusRunner:
    def __init__(self, settings: Optional[Settings] = None, db: Optional[Database] = None) -> None:
        self.settings = settings or get_settings()
        self.db = db or Database(self.settings)
        self.logger = setup_logger(__name__, self.settings.log_level)

    def build(self) -> str:
        self.logger.info("MonitorStatusRunner building read-only status report.")
        return MonitorStatusReport(self.settings, self.db).build()

    async def build_and_send(self) -> MonitorStatusRunResult:
        self.logger.info("MonitorStatusRunner sending status via sendMessage.")
        report = self.build()
        bot = TelegramAlertBot(self.settings, self.db)
        await bot.send_message(report)
        return MonitorStatusRunResult(report=report, sent=bool(bot.configured_chat_ids() and self.settings.telegram_bot_token))

    def build_and_send_sync(self) -> MonitorStatusRunResult:
        return asyncio.run(self.build_and_send())
