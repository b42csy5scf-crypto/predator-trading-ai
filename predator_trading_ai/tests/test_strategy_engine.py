import pandas as pd

from predator_trading_ai.data.market_data import MarketDataClient
from predator_trading_ai.engines.regime_detector import RegimeDetector
from predator_trading_ai.engines.strategy_engine import StrategyEngine


def test_strategy_engine_generates_breakout_signal() -> None:
    closes = [100 + i * 0.1 for i in range(35)] + [108]
    bars = pd.DataFrame(
        {
            "open": closes,
            "high": [value + 0.5 for value in closes],
            "low": [value - 0.5 for value in closes],
            "close": closes,
            "volume": [1000] * 35 + [2500],
        }
    )
    bars = MarketDataClient().add_indicators(bars)
    regime = RegimeDetector().detect(bars)
    setup = StrategyEngine().evaluate("AAPL", bars, regime)
    assert setup is not None
    assert setup.setup_type in {"breakout", "momentum continuation"}
    assert setup.direction == "long"
    assert setup.entry_zone_low < setup.entry_zone_high

