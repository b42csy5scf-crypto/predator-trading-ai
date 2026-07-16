import argparse
from pathlib import Path

from predator_trading_ai.config import get_settings
from predator_trading_ai.reports.research_report_runner import ResearchReportRunner


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build Predator Trading AI read-only quant research report.")
    parser.add_argument("--days", type=int, default=30, help="Number of recent days to include.")
    parser.add_argument("--telegram", action="store_true", help="Send the report to Telegram.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON instead of text.")
    parser.add_argument("--csv-dir", type=Path, help="Write CSV exports to this directory.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    settings = get_settings()
    runner = ResearchReportRunner(settings, days=args.days)
    if args.csv_dir:
        runner.export_csv(args.csv_dir)
        print(f"CSV exported to: {args.csv_dir}")
    if args.telegram:
        result = runner.build_and_send_sync()
        if not args.json:
            print(result.report)
        print(f"Telegram sent: {result.sent}")
        return
    if args.json:
        print(runner.build_json())
        return
    print(runner.build())


if __name__ == "__main__":
    main()
