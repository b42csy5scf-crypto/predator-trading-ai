import pandas as pd

from predator_trading_ai.data.market_data import MarketDataClient
from predator_trading_ai.engines.regime_detector import RegimeDetector
from predator_trading_ai.engines.strategy_engine import StrategyEngine


def test_strategy_engine_generates_breakout_signal() -> None:
    closes = [100 + i * 0.2 for i in range(79)] + [116.2]
    bars = pd.DataFrame(
        {
            "open": closes,
            "high": [value + 0.5 for value in closes],
            "low": [value - 0.5 for value in closes],
            "close": closes,
            "volume": [2000] * 79 + [5000],
        }
    )
    bars = MarketDataClient().add_indicators(bars)
    regime = RegimeDetector().detect(bars, breadth_score=80)
    setup = StrategyEngine().evaluate("AAPL", bars, regime)
    assert setup is not None
    assert setup.setup_type in {"high-quality breakout", "institutional momentum continuation"}
    assert setup.direction == "long"
    assert setup.entry_zone_low < setup.entry_zone_high
