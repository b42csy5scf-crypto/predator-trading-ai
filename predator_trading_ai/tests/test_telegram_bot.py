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
