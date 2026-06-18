import argparse
import asyncio

from predator_trading_ai.alerts.telegram_bot import TelegramAlertBot
from predator_trading_ai.config import get_settings
from predator_trading_ai.database.db import Database
from predator_trading_ai.reports.trade_performance_report import TradePerformanceReport


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build Predator Trading AI trade performance analytics.")
    parser.add_argument("--telegram", action="store_true", help="Send the report to Telegram.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    settings = get_settings()
    db = Database(settings)
    db.initialize()
    report = TradePerformanceReport(db).build()
    print(report)
    if args.telegram:
        asyncio.run(TelegramAlertBot(settings, db).send_message(report))


if __name__ == "__main__":
    main()
