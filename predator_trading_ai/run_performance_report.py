import argparse

from predator_trading_ai.config import get_settings
from predator_trading_ai.reports.report_runner import PerformanceReportRunner


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build Predator Trading AI trade performance analytics.")
    parser.add_argument("--telegram", action="store_true", help="Send the report to Telegram.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    settings = get_settings()
    runner = PerformanceReportRunner(settings)
    if args.telegram:
        result = runner.build_and_send_sync()
        print(result.report)
        print(f"Telegram sent: {result.sent}")
        return
    print(runner.build())


if __name__ == "__main__":
    main()
