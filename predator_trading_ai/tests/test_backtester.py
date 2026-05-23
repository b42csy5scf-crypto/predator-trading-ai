import pandas as pd

from predator_trading_ai.data.market_data import MarketDataClient
from predator_trading_ai.engines.backtester import Backtester, ExecutionModel


def test_backtester_returns_metrics() -> None:
    closes = [100 + i * 0.2 for i in range(80)]
    bars = pd.DataFrame(
        {
            "open": closes,
            "high": [value + 1 for value in closes],
            "low": [value - 1 for value in closes],
            "close": closes,
            "volume": [1000 + i * 5 for i in range(80)],
        }
    )
    bars = MarketDataClient().add_indicators(bars)

    def signal_fn(history: pd.DataFrame, idx: int) -> bool:
        return idx % 10 == 0 and history.iloc[-1]["ema_9"] > history.iloc[-1]["ema_21"]

    result = Backtester().run_simple_long(bars, signal_fn)
    assert result.trades > 0
    assert result.win_rate >= 0
    assert result.avg_r_multiple != 0
    assert result.reject_strategy is True
    assert result.monte_carlo is not None


def test_backtester_supports_short_with_execution_costs() -> None:
    closes = [120 - i * 0.2 for i in range(90)]
    bars = pd.DataFrame(
        {
            "open": closes,
            "high": [value + 1 for value in closes],
            "low": [value - 1 for value in closes],
            "close": closes,
            "volume": [1500 + i * 5 for i in range(90)],
        }
    )
    bars = MarketDataClient().add_indicators(bars)

    def signal_fn(history: pd.DataFrame, idx: int) -> bool:
        return idx % 8 == 0 and history.iloc[-1]["ema_9"] < history.iloc[-1]["ema_21"]

    result = Backtester().run_simple_short(
        bars,
        signal_fn,
        execution=ExecutionModel(slippage_pct=0.1, spread_cost_pct=0.1, partial_fill_probability=0),
        min_trades=5,
    )
    assert result.trades >= 5
    assert result.win_rate >= 0
