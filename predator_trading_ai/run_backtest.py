"""
Predator Trading AI - Backtesting Script

Runs monitoring strategies against historical yfinance data using Python 3.11.
This script intentionally uses the `ta` package instead of pandas-ta for
Python 3.11/3.14 compatibility.
"""

import argparse
import sys
from datetime import date, timedelta
from math import isfinite
from pathlib import Path

import pandas as pd
import ta
import yfinance as yf

if __package__ is None or __package__ == "":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from predator_trading_ai.engines.backtester import Backtester, ExecutionModel
from predator_trading_ai.engines.regime_detector import RegimeDetector
from predator_trading_ai.engines.strategy_engine import StrategyEngine


def download_data(ticker: str, start_date: str, end_date: str) -> pd.DataFrame:
    print(f"Downloading {ticker} data from {start_date} to {end_date}...")
    df = yf.download(
        ticker,
        start=start_date,
        end=end_date,
        progress=False,
        auto_adjust=False,
        threads=False,
    )

    if df.empty:
        raise ValueError(f"No data downloaded for {ticker}")

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    df = df.rename(
        columns={
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Volume": "volume",
        }
    )
    df = df.reset_index().rename(columns={"Date": "timestamp", "Datetime": "timestamp"})
    required = ["timestamp", "open", "high", "low", "close", "volume"]
    missing = [column for column in required if column not in df.columns]
    if missing:
        raise ValueError(f"Downloaded data missing columns: {missing}")

    print("Calculating indicators...")
    df["atr_14"] = ta.volatility.average_true_range(df["high"], df["low"], df["close"], window=14)
    df["rsi_14"] = ta.momentum.rsi(df["close"], window=14)
    df["ema_9"] = ta.trend.ema_indicator(df["close"], window=9)
    df["ema_21"] = ta.trend.ema_indicator(df["close"], window=21)
    macd_indicator = ta.trend.MACD(df["close"])
    df["macd"] = macd_indicator.macd()
    df["macd_signal"] = macd_indicator.macd_signal()
    df["volume_sma_20"] = ta.trend.sma_indicator(df["volume"], window=20)
    df["ema_50"] = ta.trend.ema_indicator(df["close"], window=50)
    df["ema_200"] = ta.trend.ema_indicator(df["close"], window=200)
    df = df.dropna().reset_index(drop=True)

    print(f"Data ready: {len(df)} bars")
    return df


def create_signal_function(
    ticker: str,
    strategy_engine: StrategyEngine,
    regime_detector: RegimeDetector,
    direction: str | None = None,
):
    def signal_fn(bars: pd.DataFrame, idx: int) -> bool:
        regime = regime_detector.detect(bars)
        setup = strategy_engine.evaluate(
            ticker=ticker,
            bars=bars,
            regime=regime,
            options_confirmation=None,
            sentiment_confirmation=None,
        )
        if setup is None or setup.score < 60:
            return False
        if direction is not None and setup.direction != direction:
            return False
        return True

    return signal_fn


def print_result(ticker: str, start_date: str, end_date: str, result) -> None:
    print("\n" + "=" * 60)
    print("BACKTEST RESULTS")
    print("=" * 60)
    print(f"Strategy: {result.strategy_name} v{result.strategy_version}")
    print(f"Ticker: {ticker}")
    print(f"Period: {start_date} to {end_date}")
    print(f"Trades: {result.trades}")
    print(f"Win Rate: {result.win_rate}%")
    print(f"Profit Factor: {result.profit_factor}")
    print(f"Avg R-Multiple: {result.avg_r_multiple}R")
    print(f"Max Drawdown: {result.max_drawdown}R")
    print(f"Sharpe Ratio: {result.sharpe_ratio}")
    print(f"Reject Strategy: {result.reject_strategy}")
    if result.warnings:
        print("Warnings:")
        for warning in result.warnings:
            print(f"  - {warning}")
    if result.monte_carlo:
        print("Monte Carlo:")
        for key, value in result.monte_carlo.items():
            print(f"  {key}: {value}")

    print("\nINTERPRETATION:")
    if result.trades == 0:
        print("No trades generated. Strategy may be too conservative or data may be insufficient.")
    elif result.reject_strategy:
        print("Rejected for activation. Sample size or validation quality is not strong enough.")
    elif result.win_rate >= 55 and result.profit_factor >= 1.5:
        print("Promising, but still requires live monitoring validation before paper execution.")
    elif result.win_rate >= 45 and result.profit_factor >= 1.2:
        print("Marginal. Keep monitoring and improve filters.")
    else:
        print("Unprofitable or unstable. Do not activate.")
    print("=" * 60)


def run_backtest(ticker: str, start_date: str, end_date: str, direction: str = "long"):
    print("\n" + "=" * 60)
    print("PREDATOR TRADING AI - BACKTESTING")
    print("=" * 60)

    df = download_data(ticker, start_date, end_date)
    strategy_engine = StrategyEngine()
    regime_detector = RegimeDetector()
    backtester = Backtester()
    signal_fn = create_signal_function(ticker, strategy_engine, regime_detector, direction=direction)
    execution = ExecutionModel(slippage_pct=0.075, spread_cost_pct=0.05, partial_fill_probability=0.05)

    print(f"\nRunning {direction} backtest on {ticker}...")
    if direction == "short":
        result = backtester.run_simple_short(
            bars=df,
            signal_fn=signal_fn,
            strategy_name="predator_combined_short",
            strategy_version="1.0.0",
            hold_bars=5,
            stop_atr=1.0,
            target_atr=1.5,
            execution=execution,
            min_trades=50,
        )
    else:
        result = backtester.run_simple_long(
            bars=df,
            signal_fn=signal_fn,
            strategy_name="predator_combined_long",
            strategy_version="1.0.0",
            hold_bars=5,
            stop_atr=1.0,
            target_atr=1.5,
            execution=execution,
            min_trades=50,
        )

    print_result(ticker, start_date, end_date, result)
    return result, df, signal_fn


def run_walk_forward(df: pd.DataFrame, signal_fn, direction: str) -> None:
    print("\n" + "=" * 60)
    print("WALK-FORWARD TESTING")
    print("=" * 60)

    backtester = Backtester()
    results = backtester.walk_forward(df, signal_fn, folds=4, direction=direction, min_trades=10)

    finite_profit_factors = [r.profit_factor for r in results if isfinite(r.profit_factor)]
    avg_win_rate = sum(r.win_rate for r in results) / len(results)
    avg_profit_factor = (
        sum(finite_profit_factors) / len(finite_profit_factors)
        if finite_profit_factors
        else float("inf")
    )

    for index, result in enumerate(results, 1):
        print(f"Fold {index}: trades={result.trades}, win_rate={result.win_rate}%, pf={result.profit_factor}, avg_r={result.avg_r_multiple}R")
        if result.warnings:
            print(f"  warnings: {'; '.join(result.warnings)}")

    print("\nAVERAGE ACROSS FOLDS:")
    print(f"Avg Win Rate: {avg_win_rate:.1f}%")
    print(f"Avg Profit Factor: {avg_profit_factor:.2f}" if isfinite(avg_profit_factor) else "Avg Profit Factor: inf")
    if any(result.reject_strategy for result in results):
        print("Walk-forward sample is statistically weak. Keep monitoring only; do not activate.")
    elif avg_win_rate >= 55 and avg_profit_factor >= 1.5:
        print("Consistent enough to keep monitoring, not enough for auto execution.")
    else:
        print("Inconsistent or weak. Treat as research only.")


def parse_args() -> argparse.Namespace:
    default_end = date.today().isoformat()
    default_start = (date.today() - timedelta(days=365 * 3)).isoformat()
    parser = argparse.ArgumentParser(description="Run Predator Trading AI historical backtest.")
    parser.add_argument("--ticker", default="SPY")
    parser.add_argument("--start", default=default_start)
    parser.add_argument("--end", default=default_end)
    parser.add_argument("--direction", choices=["long", "short"], default="long")
    parser.add_argument("--skip-walk-forward", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    result, data, signal = run_backtest(args.ticker, args.start, args.end, args.direction)
    if not args.skip_walk_forward and len(data) >= 160:
        run_walk_forward(data, signal, args.direction)
    print("\nBacktesting complete.")
