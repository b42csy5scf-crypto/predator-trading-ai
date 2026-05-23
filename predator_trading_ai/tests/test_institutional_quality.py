import pandas as pd

from predator_trading_ai.config import Settings
from predator_trading_ai.data.market_data import MarketDataClient
from predator_trading_ai.engines.regime_detector import RegimeDetector
from predator_trading_ai.engines.risk_engine import RiskEngine
from predator_trading_ai.engines.strategy_engine import StrategyEngine, StrategySetup
from predator_trading_ai.utils.watchlist import (
    DEFAULT_WATCHLIST,
    EXPECTED_SECTOR_COUNTS,
    parse_watchlist,
    sector_counts,
    validate_watchlist,
)


def make_bars(closes: list[float], volume: int = 2_000_000) -> pd.DataFrame:
    frame = pd.DataFrame(
        {
            "open": closes,
            "high": [value + 1 for value in closes],
            "low": [value - 1 for value in closes],
            "close": closes,
            "volume": [volume] * len(closes),
        }
    )
    return MarketDataClient(Settings()).add_indicators(frame)


def test_elite_watchlist_has_no_duplicates_and_expected_sector_counts() -> None:
    tickers = parse_watchlist(DEFAULT_WATCHLIST)
    assert len(tickers) == 50
    assert len(tickers) == len(set(tickers))
    assert validate_watchlist(tickers) == []
    assert sector_counts(tickers) == EXPECTED_SECTOR_COUNTS


def test_config_loads_elite_watchlist() -> None:
    settings = Settings()
    tickers = parse_watchlist(settings.watchlist)
    assert len(tickers) == 50
    assert tickers[:3] == ["AAPL", "MSFT", "NVDA"]


def test_correlation_caps_reject_crowded_group() -> None:
    settings = Settings(max_correlation_group_positions=2)
    setup = StrategySetup(
        ticker="NVDA",
        direction="long",
        setup_type="institutional momentum continuation",
        score=85,
        entry_zone_low=100,
        entry_zone_high=101,
        stop_loss=98,
        targets=(105, 108, 112),
        reason="test",
        do_not_enter_conditions=[],
    )
    active = {
        "AMD:momentum:long": {"ticker": "AMD"},
        "AVGO:momentum:long": {"ticker": "AVGO"},
    }
    decision = RiskEngine(settings).evaluate(setup, 100_000, 100, 100.5, 0, 0, 90, True, ticker="NVDA", active_positions=active)
    assert decision.approved is False
    assert any("correlation group cap" in reason for reason in decision.reasons)


def test_regime_detector_blocks_bear_market() -> None:
    bars = make_bars([200 - i for i in range(260)])
    regime = RegimeDetector().detect(bars)
    assert regime.is_safe is False
    assert regime.regime in {"bear", "bear-trend", "panic", "weak-breadth"}


def test_strategy_rejects_weak_low_volume_setup() -> None:
    closes = [100 + i * 0.2 for i in range(80)]
    bars = make_bars(closes, volume=1000)
    bars.loc[bars.index[-1], "volume"] = 100
    bars = MarketDataClient(Settings()).add_indicators(bars)
    regime = RegimeDetector().detect(bars, breadth_score=70)
    setup = StrategyEngine(Settings()).evaluate("AAPL", bars, regime)
    assert setup is None
