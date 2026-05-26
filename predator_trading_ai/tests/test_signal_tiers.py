import pandas as pd

from predator_trading_ai.config import Settings
from predator_trading_ai.data.market_data import MarketDataClient
from predator_trading_ai.engines.regime_detector import RegimeDetector
from predator_trading_ai.engines.signal_engine import SignalEngine, TradingSignal
from predator_trading_ai.engines.strategy_engine import StrategyEngine


def make_bars(closes: list[float], last_volume: int = 5000) -> pd.DataFrame:
    bars = pd.DataFrame(
        {
            "open": closes,
            "high": [value + 0.5 for value in closes],
            "low": [value - 0.5 for value in closes],
            "close": closes,
            "volume": [2000] * (len(closes) - 1) + [last_volume],
        }
    )
    return MarketDataClient(Settings()).add_indicators(bars)


def test_strategy_labels_a_plus_signal() -> None:
    settings = Settings(min_score_a_plus=65, min_score_a=58)
    closes = [100 + i * 0.2 for i in range(79)] + [116.2]
    bars = make_bars(closes, last_volume=5000)
    regime = RegimeDetector().detect(bars, breadth_score=80)
    setup = StrategyEngine(settings).evaluate("AAPL", bars, regime)
    assert setup is not None
    assert setup.signal_tier == "A+ Signal"


def test_watch_alert_generated_for_near_setup() -> None:
    settings = Settings(enable_watchlist_alerts=True, min_score_watch=50)
    closes = [100 + i * 0.15 for i in range(79)] + [111.7]
    bars = make_bars(closes, last_volume=2600)
    regime = RegimeDetector().detect(bars, breadth_score=70)
    watch = StrategyEngine(settings).evaluate_watch_alert("AAPL", bars, regime)
    assert watch is not None
    assert watch.signal_tier == "Watch Alert"
    assert 50 <= watch.score < settings.min_score_a


def test_signal_format_includes_tier_label() -> None:
    signal = TradingSignal(
        ticker="AAPL",
        direction="long",
        setup_type="test",
        entry_zone_low=100,
        entry_zone_high=101,
        target_1=103,
        target_2=105,
        target_3=108,
        stop_loss=98,
        risk_reward=1.5,
        confidence=66,
        expected_win_rate=None,
        position_size=10,
        liquidity_score=90,
        market_regime="bull-trend",
        reason="test",
        do_not_enter_conditions=[],
    )
    assert "A+ Signal" in SignalEngine.format_alert(signal, label="A+ Signal").splitlines()[0]
