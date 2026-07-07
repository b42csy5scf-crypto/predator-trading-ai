import asyncio
import threading
from typing import Optional

from predator_trading_ai.config import Settings, get_settings
from predator_trading_ai.database.db import Database
from predator_trading_ai.engines.signal_engine import SignalEngine, TradingSignal
from predator_trading_ai.utils.logger import setup_logger


TELEGRAM_POLLING_LOCK = threading.Lock()
TELEGRAM_POLLING_ALREADY_STARTED = False
TELEGRAM_POLLING_STARTING = False
TELEGRAM_POLLING_STARTED = False
TELEGRAM_POLLING_SKIPPED_REASON = "not_started"
TELEGRAM_POLLING_OWNER = None
TELEGRAM_POLLING_DISABLED_REASON = None


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

    def start_command_polling(self, source_module: str = "unknown") -> None:
        global TELEGRAM_POLLING_ALREADY_STARTED, TELEGRAM_POLLING_STARTING, TELEGRAM_POLLING_STARTED
        global TELEGRAM_POLLING_SKIPPED_REASON
        global TELEGRAM_POLLING_OWNER
        self.logger.info("Telegram polling startup attempted by module=%s", source_module)
        if TELEGRAM_POLLING_SKIPPED_REASON == "conflict_detected":
            self.log_polling_startup(started=False, skipped_reason="conflict_detected", source_module=source_module)
            self.logger.info("Telegram command polling disabled after Conflict; skipping startup.")
            return
        if not self.settings.enable_telegram_polling:
            TELEGRAM_POLLING_SKIPPED_REASON = "disabled_by_config"
            self.log_polling_startup(started=False, skipped_reason=TELEGRAM_POLLING_SKIPPED_REASON, source_module=source_module)
            return
        if self._command_thread and self._command_thread.is_alive():
            TELEGRAM_POLLING_SKIPPED_REASON = "instance_thread_alive"
            self.log_polling_startup(started=True, skipped_reason=TELEGRAM_POLLING_SKIPPED_REASON, source_module=source_module)
            return
        if not self.settings.telegram_bot_token:
            TELEGRAM_POLLING_SKIPPED_REASON = "missing_bot_token"
            self.log_polling_startup(started=False, skipped_reason=TELEGRAM_POLLING_SKIPPED_REASON, source_module=source_module)
            self.logger.info("Telegram bot token missing; command polling disabled.")
            return
        with TELEGRAM_POLLING_LOCK:
            if TELEGRAM_POLLING_ALREADY_STARTED:
                TELEGRAM_POLLING_SKIPPED_REASON = "duplicate_startup"
                self.log_polling_startup(started=TELEGRAM_POLLING_STARTED, skipped_reason=TELEGRAM_POLLING_SKIPPED_REASON, source_module=source_module)
                self.logger.info(
                    "Telegram polling already running; skipping duplicate startup. owner=%s attempted_by=%s",
                    TELEGRAM_POLLING_OWNER,
                    source_module,
                )
                return
            TELEGRAM_POLLING_ALREADY_STARTED = True
            TELEGRAM_POLLING_STARTING = True
            TELEGRAM_POLLING_STARTED = False
            TELEGRAM_POLLING_SKIPPED_REASON = "none"
            TELEGRAM_POLLING_OWNER = source_module
            self._command_thread = threading.Thread(target=lambda: self._run_command_polling(source_module), daemon=True)
            self._command_thread.start()
            self.log_polling_startup(started=False, skipped_reason="none", source_module=source_module)

    def _run_command_polling(self, source_module: str = "unknown") -> None:
        try:
            asyncio.run(self._run_command_polling_async(source_module))
        except Exception as exc:
            self.logger.exception("Telegram command polling thread exited safely: %s", exc)

    async def _run_command_polling_async(self, source_module: str = "unknown") -> None:
        try:
            from telegram import Bot

            bot = Bot(self.settings.telegram_bot_token)
            self.logger.info("Telegram admin command polling starting by module=%s.", source_module)
            self.mark_polling_stable(source_module)
            self.logger.info("Telegram admin command polling started by module=%s.", source_module)
            offset: Optional[int] = None
            while True:
                try:
                    updates = await bot.get_updates(
                        offset=offset,
                        timeout=20,
                        allowed_updates=["message"],
                    )
                except Exception as exc:
                    if self.is_conflict_error(exc):
                        self.mark_polling_conflict(source_module, exc)
                        self.logger.warning(
                            "Telegram polling conflict detected, alerts sending will continue, scanning will continue."
                        )
                        return
                    self.logger.exception("Telegram getUpdates failed; retrying command polling: %s", exc)
                    await asyncio.sleep(5)
                    continue
                for update in updates:
                    offset = int(update.update_id) + 1
                    await self.handle_command_update(bot, update)
        except Exception as exc:
            if self.is_conflict_error(exc):
                self.mark_polling_conflict(source_module, exc)
                self.logger.warning(
                    "Telegram polling conflict detected, alerts sending will continue, scanning will continue."
                )
                return
            self.logger.exception("Telegram command polling stopped: %s", exc)

    async def handle_command_update(self, bot, update) -> None:
        message = getattr(update, "message", None)
        if message is None or not getattr(message, "text", ""):
            return
        if not str(message.text).strip().startswith("/report"):
            return
        chat_id = str(update.effective_chat.id) if getattr(update, "effective_chat", None) else ""
        if chat_id not in self.configured_chat_ids():
            await bot.send_message(chat_id=chat_id, text="Unauthorized.")
            return
        await bot.send_message(chat_id=chat_id, text="Generating Predator performance report...")
        from predator_trading_ai.reports.report_runner import PerformanceReportRunner

        result = await PerformanceReportRunner(self.settings, self.db).build_and_send()
        if not result.sent:
            await bot.send_message(
                chat_id=chat_id,
                text="Report generated, but Telegram recipients are not configured.",
            )

    def mark_polling_stable(self, source_module: str) -> None:
        global TELEGRAM_POLLING_STARTING, TELEGRAM_POLLING_STARTED, TELEGRAM_POLLING_SKIPPED_REASON
        with TELEGRAM_POLLING_LOCK:
            TELEGRAM_POLLING_STARTING = False
            TELEGRAM_POLLING_STARTED = True
            TELEGRAM_POLLING_SKIPPED_REASON = "none"
        self.log_polling_startup(started=True, skipped_reason="none", source_module=source_module)

    def mark_polling_conflict(self, source_module: str, exc: Exception) -> None:
        global TELEGRAM_POLLING_STARTING, TELEGRAM_POLLING_STARTED, TELEGRAM_POLLING_SKIPPED_REASON
        global TELEGRAM_POLLING_DISABLED_REASON
        with TELEGRAM_POLLING_LOCK:
            TELEGRAM_POLLING_STARTING = False
            TELEGRAM_POLLING_STARTED = False
            TELEGRAM_POLLING_SKIPPED_REASON = "conflict_detected"
            TELEGRAM_POLLING_DISABLED_REASON = str(exc)
        self.logger.warning("Telegram polling disabled for commands after Conflict. module=%s", source_module)
        self.logger.warning("TELEGRAM_POLLING_CONFLICT_CAUGHT=True")
        self.logger.warning("TELEGRAM_COMMAND_POLLING_DISABLED=True")
        self.logger.warning("ALERTS_SENDMESSAGE_STILL_ENABLED=True")

    def log_polling_startup(self, started: bool, skipped_reason: str, source_module: str) -> None:
        self.logger.info("SERVICE_ROLE=%s", self.settings.service_role)
        self.logger.info("ENABLE_TELEGRAM_POLLING=%s", self.settings.enable_telegram_polling)
        self.logger.info("TELEGRAM_POLLING_STARTED=%s", started)
        self.logger.info("TELEGRAM_POLLING_SKIPPED_REASON=%s", skipped_reason)
        self.logger.info("TELEGRAM_POLLING_ATTEMPT_MODULE=%s", source_module)

    @staticmethod
    def is_conflict_error(exc: Exception) -> bool:
        if exc.__class__.__name__ == "Conflict":
            return True
        return "terminated by other getUpdates request" in str(exc) or "Conflict" in str(exc)

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
