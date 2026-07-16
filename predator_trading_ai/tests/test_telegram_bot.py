import asyncio

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
    telegram_module.TELEGRAM_POLLING_STARTING = False
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
    assert telegram_module.TELEGRAM_POLLING_STARTING is True
    assert telegram_module.TELEGRAM_POLLING_STARTED is False
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


def test_send_message_still_works_when_polling_disabled(monkeypatch) -> None:
    sent: list[tuple[str, str]] = []

    class FakeBot:
        def __init__(self, token):
            self.token = token

        async def send_message(self, chat_id, text):
            sent.append((chat_id, text))

    import telegram

    monkeypatch.setattr(telegram, "Bot", FakeBot)
    bot = TelegramAlertBot(
        Settings(
            telegram_bot_token="token",
            telegram_chat_id="123",
            enable_telegram_polling=False,
        )
    )

    asyncio.run(bot.send_message("hello"))

    assert sent == [("123", "hello")]


def test_telegram_conflict_is_detected() -> None:
    class ConflictLike(Exception):
        pass

    assert TelegramAlertBot.is_conflict_error(Exception("terminated by other getUpdates request"))
    assert not TelegramAlertBot.is_conflict_error(ConflictLike("network timeout"))


def test_telegram_conflict_disables_command_polling_only() -> None:
    reset_polling_globals()
    bot = TelegramAlertBot(Settings(telegram_bot_token="token"))
    telegram_module.TELEGRAM_POLLING_ALREADY_STARTED = True
    telegram_module.TELEGRAM_POLLING_STARTING = True
    telegram_module.TELEGRAM_POLLING_STARTED = True

    bot.mark_polling_conflict("test.conflict", Exception("terminated by other getUpdates request"))

    assert telegram_module.TELEGRAM_POLLING_ALREADY_STARTED is True
    assert telegram_module.TELEGRAM_POLLING_STARTING is False
    assert telegram_module.TELEGRAM_POLLING_STARTED is False
    assert telegram_module.TELEGRAM_POLLING_SKIPPED_REASON == "conflict_detected"
    assert "getUpdates" in telegram_module.TELEGRAM_POLLING_DISABLED_REASON
    reset_polling_globals()


def test_polling_stable_is_marked_only_after_startup() -> None:
    reset_polling_globals()
    bot = TelegramAlertBot(Settings(telegram_bot_token="token"))
    telegram_module.TELEGRAM_POLLING_ALREADY_STARTED = True
    telegram_module.TELEGRAM_POLLING_STARTING = True

    bot.mark_polling_stable("test.stable")

    assert telegram_module.TELEGRAM_POLLING_STARTING is False
    assert telegram_module.TELEGRAM_POLLING_STARTED is True
    assert telegram_module.TELEGRAM_POLLING_SKIPPED_REASON == "none"
    reset_polling_globals()


def test_command_polling_does_not_retry_after_conflict(monkeypatch) -> None:
    reset_polling_globals()
    starts: list[str] = []

    class DummyThread:
        def __init__(self, target, daemon):
            pass

        def start(self) -> None:
            starts.append("started")

    monkeypatch.setattr(telegram_module.threading, "Thread", DummyThread)
    bot = TelegramAlertBot(Settings(telegram_bot_token="token"))
    bot.mark_polling_conflict("test.conflict", Exception("terminated by other getUpdates request"))

    bot.start_command_polling(source_module="test.retry")

    assert starts == []
    assert telegram_module.TELEGRAM_POLLING_SKIPPED_REASON == "conflict_detected"
    reset_polling_globals()


def test_manual_get_updates_conflict_is_caught(monkeypatch) -> None:
    reset_polling_globals()

    class FakeBot:
        def __init__(self, token):
            self.token = token

        async def get_updates(self, **kwargs):
            raise Exception("terminated by other getUpdates request")

    import telegram

    monkeypatch.setattr(telegram, "Bot", FakeBot)
    bot = TelegramAlertBot(Settings(telegram_bot_token="token"))

    asyncio.run(bot._run_command_polling_async("test.manual"))

    assert telegram_module.TELEGRAM_POLLING_STARTED is False
    assert telegram_module.TELEGRAM_POLLING_SKIPPED_REASON == "conflict_detected"
    assert "getUpdates" in telegram_module.TELEGRAM_POLLING_DISABLED_REASON
    reset_polling_globals()


def test_report_command_is_handled_without_application_polling(monkeypatch) -> None:
    sent: list[tuple[str, str]] = []

    class FakeMessage:
        text = "/report"

    update = type(
        "FakeUpdate",
        (),
        {
            "message": FakeMessage(),
            "effective_chat": type("FakeChat", (), {"id": "123"})(),
        },
    )()

    class FakeBot:
        async def send_message(self, chat_id, text):
            sent.append((chat_id, text))

    class FakeRunner:
        def __init__(self, settings, db):
            pass

        async def build_and_send(self):
            return type("Result", (), {"sent": True})()

    import predator_trading_ai.reports.report_runner as report_runner

    monkeypatch.setattr(report_runner, "PerformanceReportRunner", FakeRunner)
    bot = TelegramAlertBot(Settings(telegram_bot_token="token", telegram_chat_id="123"))

    asyncio.run(bot.handle_command_update(FakeBot(), update))

    assert sent == [("123", "Generating Predator performance report...")]


def test_diagnostics_report_command_is_handled_without_application_polling(monkeypatch) -> None:
    sent: list[tuple[str, str]] = []

    class FakeMessage:
        text = "/diagnostics_report"

    update = type(
        "FakeUpdate",
        (),
        {
            "message": FakeMessage(),
            "effective_chat": type("FakeChat", (), {"id": "123"})(),
        },
    )()

    class FakeBot:
        async def send_message(self, chat_id, text):
            sent.append((chat_id, text))

    class FakeRunner:
        def __init__(self, settings, db, days):
            self.days = days

        async def build_and_send(self):
            return type("Result", (), {"sent": True})()

    import predator_trading_ai.reports.diagnostics_report_runner as diagnostics_report_runner

    monkeypatch.setattr(diagnostics_report_runner, "DiagnosticsReportRunner", FakeRunner)
    bot = TelegramAlertBot(Settings(telegram_bot_token="token", telegram_chat_id="123"))

    asyncio.run(bot.handle_command_update(FakeBot(), update))

    assert sent == [("123", "Generating Predator diagnostics report...")]


def test_research_report_commands_are_handled_without_application_polling(monkeypatch) -> None:
    sent: list[tuple[str, str]] = []
    days_seen: list[int] = []

    class FakeBot:
        async def send_message(self, chat_id, text):
            sent.append((chat_id, text))

    class FakeRunner:
        def __init__(self, settings, db, days):
            days_seen.append(days)

        async def build_and_send(self):
            return type("Result", (), {"sent": True})()

    import predator_trading_ai.reports.research_report_runner as research_report_runner

    monkeypatch.setattr(research_report_runner, "ResearchReportRunner", FakeRunner)
    bot = TelegramAlertBot(Settings(telegram_bot_token="token", telegram_chat_id="123"))

    for command in ("/research_report", "/research_report_7d", "/research_report_30d"):
        update = type(
            "FakeUpdate",
            (),
            {
                "message": type("FakeMessage", (), {"text": command})(),
                "effective_chat": type("FakeChat", (), {"id": "123"})(),
            },
        )()
        asyncio.run(bot.handle_command_update(FakeBot(), update))

    assert days_seen == [30, 7, 30]
    assert sent == [
        ("123", "Generating Predator research report (30d)..."),
        ("123", "Generating Predator research report (7d)..."),
        ("123", "Generating Predator research report (30d)..."),
    ]
