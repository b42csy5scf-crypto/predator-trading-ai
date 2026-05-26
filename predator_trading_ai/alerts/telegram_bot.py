from typing import Optional

from predator_trading_ai.config import Settings, get_settings
from predator_trading_ai.database.db import Database
from predator_trading_ai.engines.signal_engine import SignalEngine, TradingSignal
from predator_trading_ai.utils.logger import setup_logger


class TelegramAlertBot:
    def __init__(self, settings: Optional[Settings] = None, db: Optional[Database] = None) -> None:
        self.settings = settings or get_settings()
        self.db = db or Database(self.settings)
        self.logger = setup_logger(__name__, self.settings.log_level)

    async def send_signal(self, signal: TradingSignal, label: str = "Signal") -> None:
        await self.send_message(SignalEngine.format_alert(signal, label=label))

    async def send_message(self, text: str) -> None:
        if not self.settings.telegram_bot_token:
            self.logger.info("Telegram bot token missing; alert not sent: %s", text[:120])
            return
        try:
            from telegram import Bot
        
            bot = Bot(self.settings.telegram_bot_token)
            chat_ids = self.configured_chat_ids()
            sent = False
            for chat_id in chat_ids:
                await bot.send_message(chat_id=chat_id, text=text)
                sent = True
            if not sent:
                self.logger.info("Telegram chat id missing; alert not sent: %s", text[:120])
            
        except Exception as exc:
            self.logger.exception("Telegram send failed: %s", exc)

    def configured_chat_ids(self) -> list[str]:
        primary = self._split_chat_ids(getattr(self.settings, "telegram_chat_id", None))
        if primary:
            return primary
        fallback = [
            *self._split_chat_ids(getattr(self.settings, "telegram_chat_id_1", None)),
            *self._split_chat_ids(getattr(self.settings, "telegram_chat_id_2", None)),
        ]
        return list(dict.fromkeys(fallback))

    @staticmethod
    def _split_chat_ids(value: Optional[str]) -> list[str]:
        if not value:
            return []
        return [part.strip() for part in str(value).split(",") if part.strip()]

    def command_status(self) -> str:
        live = "ON" if self.settings.live_trading else "OFF"
        return f"Predator Trading AI status: live trading {live}, paper trading available."

    def command_performance(self) -> str:
        rows = self.db.fetch_all("SELECT * FROM performance_metrics ORDER BY created_at DESC LIMIT 1")
        if not rows:
            return "No performance metrics recorded yet."
        row = rows[0]
        return f"Performance {row['period']}: win rate {row['win_rate']:.1f}%, profit factor {row['profit_factor']:.2f}"

    def command_open_trades(self) -> str:
        rows = self.db.fetch_all("SELECT ticker, direction, entry_price, quantity FROM trades WHERE status='open'")
        if not rows:
            return "No open trades."
        return "\n".join(f"{r['ticker']} {r['direction']} entry {r['entry_price']:.2f} qty {r['quantity']:.2f}" for r in rows)

    def command_last_signals(self, limit: int = 5) -> str:
        rows = self.db.fetch_all("SELECT ticker, direction, confidence, setup_type FROM signals ORDER BY created_at DESC LIMIT ?", [limit])
        if not rows:
            return "No signals yet."
        return "\n".join(f"{r['ticker']} {r['direction']} {r['setup_type']} {r['confidence']:.0f}%" for r in rows)

    def command_disable_live(self) -> str:
        return "Live trading is controlled by LIVE_TRADING=false in config. Restart after changing .env."

    def command_enable_paper(self) -> str:
        return "Paper trading is enabled when Alpaca paper credentials are configured."
