import predator_trading_ai.alerts.telegram_bot as telegram_module
from predator_trading_ai.alerts.telegram_bot import TelegramAlertBot
from predator_trading_ai.config import Settings


def test_telegram_chat_id_supports_comma_separated_primary() -> None:
    settings = Settings(
        telegram_chat_id="111, 222",
        telegram_chat_id_1="333",
        telegram_chat_id_2="444",
    )
    bot = TelegramAlertBot(settings)
    assert bot.configured_chat_ids() == ["111", "222"]


def test_telegram_chat_id_falls_back_to_numbered_ids() -> None:
    settings = Settings(
        telegram_chat_id=None,
        telegram_chat_id_1="111",
        telegram_chat_id_2="222,333",
    )
    bot = TelegramAlertBot(settings)
    assert bot.configured_chat_ids() == ["111", "222", "333"]


def test_telegram_chunks_long_messages() -> None:
    text = "\n".join([f"line {idx}" for idx in range(100)])
    chunks = TelegramAlertBot._telegram_chunks(text, limit=80)
    assert len(chunks) > 1
    assert all(len(chunk) <= 80 for chunk in chunks)


def reset_polling_globals() -> None:
    telegram_module.TELEGRAM_POLLING_ALREADY_STARTED = False
    telegram_module.TELEGRAM_POLLING_STARTED = False
    telegram_module.TELEGRAM_POLLING_SKIPPED_REASON = "not_started"
    telegram_module.TELEGRAM_POLLING_OWNER = None
    telegram_module.TELEGRAM_POLLING_DISABLED_REASON = None


def test_duplicate_telegram_polling_startup_is_prevented(monkeypatch) -> None:
    reset_polling_globals()
    starts: list[str] = []

    class DummyThread:
        def __init__(self, target, daemon):
            self.target = target
            self.daemon = daemon

        def is_alive(self) -> bool:
            return True

        def start(self) -> None:
            starts.append("started")

    monkeypatch.setattr(telegram_module.threading, "Thread", DummyThread)
    settings = Settings(telegram_bot_token="token", enable_telegram_polling=True)

    first = TelegramAlertBot(settings)
    second = TelegramAlertBot(settings)
    first.start_command_polling(source_module="test.first")
    second.start_command_polling(source_module="test.second")

    assert starts == ["started"]
    assert telegram_module.TELEGRAM_POLLING_ALREADY_STARTED is True
    assert telegram_module.TELEGRAM_POLLING_STARTED is True
    assert telegram_module.TELEGRAM_POLLING_SKIPPED_REASON == "duplicate_startup"
    assert telegram_module.TELEGRAM_POLLING_OWNER == "test.first"
    reset_polling_globals()


def test_telegram_polling_disabled_does_not_start_thread(monkeypatch) -> None:
    reset_polling_globals()
    starts: list[str] = []

    class DummyThread:
        def __init__(self, target, daemon):
            pass

        def start(self) -> None:
            starts.append("started")

    monkeypatch.setattr(telegram_module.threading, "Thread", DummyThread)
    bot = TelegramAlertBot(Settings(telegram_bot_token="token", enable_telegram_polling=False))

    bot.start_command_polling(source_module="test.disabled")

    assert starts == []
    assert telegram_module.TELEGRAM_POLLING_ALREADY_STARTED is False
    assert telegram_module.TELEGRAM_POLLING_SKIPPED_REASON == "disabled_by_config"
    reset_polling_globals()


def test_telegram_conflict_is_detected() -> None:
    class ConflictLike(Exception):
        pass

    assert TelegramAlertBot.is_conflict_error(Exception("terminated by other getUpdates request"))
    assert not TelegramAlertBot.is_conflict_error(ConflictLike("network timeout"))


def test_telegram_conflict_disables_command_polling_only() -> None:
    reset_polling_globals()
    bot = TelegramAlertBot(Settings(telegram_bot_token="token"))
    telegram_module.TELEGRAM_POLLING_ALREADY_STARTED = True
    telegram_module.TELEGRAM_POLLING_STARTED = True

    bot.mark_polling_conflict("test.conflict", Exception("terminated by other getUpdates request"))

    assert telegram_module.TELEGRAM_POLLING_ALREADY_STARTED is True
    assert telegram_module.TELEGRAM_POLLING_STARTED is False
    assert telegram_module.TELEGRAM_POLLING_SKIPPED_REASON == "conflict_detected"
    assert "getUpdates" in telegram_module.TELEGRAM_POLLING_DISABLED_REASON
    reset_polling_globals()
