"""
Predator Trading AI - Backtesting Script
Tests strategies on historical data using yfinance
"""

import yfinance as yf
import pandas as pd
import ta
from datetime import datetime, timedelta

from predator_trading_ai.engines.backtester import Backtester
from predator_trading_ai.engines.strategy_engine import StrategyEngine
from predator_trading_ai.engines.regime_detector import RegimeDetector
from predator_trading_ai.config import get_settings


def download_data(ticker: str, start_date: str, end_date: str) -> pd.DataFrame:
    """Download historical data from yfinance"""
    print(f"📊 Downloading {ticker} data from {start_date} to {end_date}...")
    
    df = yf.download(ticker, start=start_date, end=end_date, progress=False)
    if isinstance(df.columns, pd.MultiIndex):
          df.columns = df.columns.get_level_values(0)
    df.columns = [col.lower() for col in df.columns]

    if df.empty:
        raise ValueError(f"No data downloaded for {ticker}")
    
    # Convert column names to lowercase
    

    
    # Add technical indicators
    print(f"📈 Calculating indicators...")
    df['atr_14'] = ta.volatility.average_true_range(df['high'], df['low'], df['close'], window=14)
    df['rsi_14'] = ta.momentum.rsi(df['close'], window=14)
    df['ema_9'] = ta.trend.ema_indicator(df['close'], window=9)
    df['ema_21'] = ta.trend.ema_indicator(df['close'], window=21)

    macd_indicator = ta.trend.MACD(df['close'])
    df['macd'] = macd_indicator.macd()
    df['macd_signal'] = macd_indicator.macd_signal()

    df['volume_sma_20'] = ta.trend.sma_indicator(df['volume'], window=20)

    
    # Drop NaN rows
    df = df.dropna()
    
    print(f"✅ Data ready: {len(df)} bars")
    return df


def create_signal_function(ticker: str, strategy_engine: StrategyEngine, regime_detector: RegimeDetector):
    """Create a signal function for backtesting"""
    def signal_fn(bars: pd.DataFrame, idx: int) -> bool:
        """Returns True if strategy generates a signal at this bar"""
        
        # Get regime
        regime = regime_detector.detect(bars)
        
        # Get setup from strategy engine
        setup = strategy_engine.evaluate(
            ticker=ticker,
            bars=bars,
            regime=regime,
            options_confirmation=None,  # No options data in backtest
            sentiment_confirmation=None  # No sentiment in backtest
        )
        
        # Return True if we have a valid setup
        return setup is not None and setup.score >= 60
    
    return signal_fn


def run_backtest(ticker: str, start_date: str, end_date: str):
    """Run backtest on a ticker"""
    
    print("\n" + "="*60)
    print(f"🎯 PREDATOR TRADING AI - BACKTESTING")
    print("="*60)
    
    # Download data
    df = download_data(ticker, start_date, end_date)
    
    # Initialize engines
    settings = get_settings()
    strategy_engine = StrategyEngine()
    regime_detector = RegimeDetector()
    backtester = Backtester()
    
    # Create signal function
    signal_fn = create_signal_function(ticker, strategy_engine, regime_detector)
    
    # Run backtest
    print(f"\n🔬 Running backtest on {ticker}...")
    result = backtester.run_simple_long(
        bars=df,
        signal_fn=signal_fn,
        strategy_name="predator_combined",
        strategy_version="1.0.0",
        hold_bars=5,
        stop_atr=1.0,
        target_atr=1.5
    )
    
    # Print results
    print("\n" + "="*60)
    print("📊 BACKTEST RESULTS")
    print("="*60)
    print(f"Strategy: {result.strategy_name} v{result.strategy_version}")
    print(f"Ticker: {ticker}")
    print(f"Period: {start_date} to {end_date}")
    print(f"\n💰 Performance Metrics:")
    print(f"  Total Trades: {result.trades}")
    print(f"  Win Rate: {result.win_rate}%")
    print(f"  Profit Factor: {result.profit_factor}")
    print(f"  Avg R-Multiple: {result.avg_r_multiple}R")
    print(f"  Max Drawdown: {result.max_drawdown}R")
    print(f"  Sharpe Ratio: {result.sharpe_ratio}")
    print("="*60)
    
    # Interpretation
    print("\n📈 INTERPRETATION:")
    
    if result.trades == 0:
        print("❌ No trades generated. Strategy too conservative or data issues.")
    elif result.trades < 20:
        print(f"⚠️  Only {result.trades} trades - sample size too small for reliable conclusions.")
    else:
        if result.win_rate >= 55 and result.profit_factor >= 1.5:
            print("✅ PROMISING - Strategy shows profitable potential!")
        elif result.win_rate >= 45 and result.profit_factor >= 1.2:
            print("⚠️  MARGINAL - Strategy barely profitable, needs improvement.")
        else:
            print("❌ UNPROFITABLE - Strategy needs major revision.")
        
        if result.max_drawdown > 10:
            print(f"⚠️  HIGH DRAWDOWN ({result.max_drawdown}R) - Risk management needed!")
    
    print("\n" + "="*60)
    
    return result


def run_walk_forward(ticker: str, start_date: str, end_date: str):
    """Run walk-forward test (more robust)"""
    
    print("\n" + "="*60)
    print(f"🔬 WALK-FORWARD TESTING")
    print("="*60)
    
    df = download_data(ticker, start_date, end_date)
    
    strategy_engine = StrategyEngine()
    regime_detector = RegimeDetector()
    backtester = Backtester()
    
    signal_fn = create_signal_function(ticker, strategy_engine, regime_detector)
    
    print(f"\n🔄 Running walk-forward test (4 folds)...")
    results = backtester.walk_forward(df, signal_fn, folds=4)
    
    print("\n📊 WALK-FORWARD RESULTS:")
    print("="*60)
    
    for i, result in enumerate(results, 1):
        print(f"\nFold {i}:")
        print(f"  Trades: {result.trades}")
        print(f"  Win Rate: {result.win_rate}%")
        print(f"  Profit Factor: {result.profit_factor}")
        print(f"  Avg R: {result.avg_r_multiple}R")
    
    # Average metrics
    avg_win_rate = sum(r.win_rate for r in results) / len(results)
    avg_profit_factor = sum(r.profit_factor for r in results if r.profit_factor != float('inf')) / len(results)
    
    print("\n" + "="*60)
    print("📊 AVERAGE ACROSS FOLDS:")
    print(f"  Avg Win Rate: {avg_win_rate:.1f}%")
    print(f"  Avg Profit Factor: {avg_profit_factor:.2f}")
    print("="*60)
    
    if avg_win_rate >= 55 and avg_profit_factor >= 1.5:
        print("✅ CONSISTENT PERFORMANCE - Strategy is robust!")
    else:
        print("⚠️  INCONSISTENT - Strategy may be overfitted.")


if __name__ == "__main__":
    # Configuration
    TICKER = "SPY"  # S&P 500 ETF
    
    # Test last 1 year
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
    
    print("🚀 Starting Predator Trading AI Backtest...")
    
    # Run simple backtest
    result = run_backtest(TICKER, start_date, end_date)
    
    # Run walk-forward test (more reliable)
    run_walk_forward(TICKER, start_date, end_date)
    
    print("\n✅ Backtesting complete!")
