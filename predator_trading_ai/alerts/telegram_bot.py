import threading
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
        self._command_thread: Optional[threading.Thread] = None

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
                for chunk in self._telegram_chunks(text):
                    await bot.send_message(chat_id=chat_id, text=chunk)
                sent = True
            if not sent:
                self.logger.info("Telegram chat id missing; alert not sent: %s", text[:120])
            
        except Exception as exc:
            self.logger.exception("Telegram send failed: %s", exc)

    def start_command_polling(self) -> None:
        if self._command_thread and self._command_thread.is_alive():
            return
        if not self.settings.telegram_bot_token:
            self.logger.info("Telegram bot token missing; command polling disabled.")
            return
        self._command_thread = threading.Thread(target=self._run_command_polling, daemon=True)
        self._command_thread.start()

    def _run_command_polling(self) -> None:
        try:
            from telegram.ext import Application, CommandHandler

            async def report(update, context) -> None:
                from predator_trading_ai.reports.report_runner import PerformanceReportRunner

                chat_id = str(update.effective_chat.id) if update.effective_chat else ""
                if chat_id not in self.configured_chat_ids():
                    await update.message.reply_text("Unauthorized.")
                    return
                await update.message.reply_text("Generating Predator performance report...")
                result = await PerformanceReportRunner(self.settings, self.db).build_and_send()
                if not result.sent:
                    await update.message.reply_text("Report generated, but Telegram recipients are not configured.")

            application = Application.builder().token(self.settings.telegram_bot_token).build()
            application.add_handler(CommandHandler("report", report))
            self.logger.info("Telegram admin command polling started.")
            application.run_polling(close_loop=False, stop_signals=None)
        except Exception as exc:
            self.logger.exception("Telegram command polling stopped: %s", exc)

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

    @staticmethod
    def _telegram_chunks(text: str, limit: int = 3900) -> list[str]:
        if len(text) <= limit:
            return [text]
        chunks: list[str] = []
        current = ""
        for line in text.splitlines():
            addition = f"{line}\n"
            if len(current) + len(addition) > limit and current:
                chunks.append(current.rstrip())
                current = ""
            current += addition
        if current:
            chunks.append(current.rstrip())
        return chunks

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
