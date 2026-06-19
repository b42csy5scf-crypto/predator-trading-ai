import pandas as pd

from predator_trading_ai.config import Settings
from predator_trading_ai.data.market_data import MarketDataClient
from predator_trading_ai.engines.regime_detector import MarketRegime, RegimeDetector
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


def test_strategy_labels_a_plus_plus_signal() -> None:
    settings = Settings(min_score_a_plus_plus=75, min_score_a_plus=65, min_score_a=58)
    closes = [100 + i * 0.2 for i in range(79)] + [116.2]
    bars = make_bars(closes, last_volume=5000)
    regime = RegimeDetector().detect(bars, breadth_score=80)
    setup = StrategyEngine(settings).evaluate("AAPL", bars, regime)
    assert setup is not None
    assert setup.signal_tier == "A++ Signal"


def test_watch_alert_generated_for_near_setup() -> None:
    settings = Settings(enable_watchlist_alerts=True, enable_b_alerts=True, enable_c_alerts=True)
    closes = [100 + i * 0.15 for i in range(79)] + [111.7]
    bars = make_bars(closes, last_volume=2600)
    regime = RegimeDetector().detect(bars, breadth_score=70)
    watch = StrategyEngine(settings).evaluate_watch_alert("AAPL", bars, regime)
    assert watch is not None
    assert watch.signal_tier in {"B Watch Alert", "C Risky/Early Alert"}
    assert watch.score >= settings.min_score_c


def test_choppy_soft_regime_can_generate_c_watch_alert() -> None:
    settings = Settings(enable_watchlist_alerts=True, enable_c_alerts=True)
    closes = [100 + i * 0.05 for i in range(80)]
    bars = make_bars(closes, last_volume=1800)
    regime = MarketRegime(
        regime="choppy",
        volatility=1.0,
        volume_state="normal",
        trend_strength=0.05,
        is_safe=False,
        reason="Weak trend strength",
        risk_level="elevated",
        breadth_score=52,
    )
    result = StrategyEngine(settings).evaluate_watch_candidate("AAPL", bars, regime)
    assert result.setup is not None
    assert result.setup.signal_tier in {"B Watch Alert", "C Risky/Early Alert"}
    assert result.rejected_by == "none"


def test_hard_blocked_regime_prevents_watch_alert() -> None:
    settings = Settings(enable_watchlist_alerts=True, enable_c_alerts=True)
    bars = make_bars([100 + i * 0.1 for i in range(80)], last_volume=4000)
    regime = MarketRegime(
        regime="panic",
        volatility=6.0,
        volume_state="high",
        trend_strength=0.2,
        is_safe=False,
        reason="Panic mode",
        risk_level="blocked",
        breadth_score=20,
    )
    result = StrategyEngine(settings).evaluate_watch_candidate("AAPL", bars, regime)
    assert result.setup is None
    assert result.rejected_by == "regime"


def test_moderate_bear_allows_watch_alert_but_blocks_trade_entry() -> None:
    settings = Settings(enable_watchlist_alerts=True, enable_b_alerts=True, enable_c_alerts=True)
    closes = [100 + i * 0.12 for i in range(80)]
    bars = make_bars(closes, last_volume=3000)
    regime = MarketRegime(
        regime="bear",
        volatility=1.5,
        volume_state="normal",
        trend_strength=0.25,
        is_safe=False,
        reason="Moderate bear regime: trade entries blocked",
        risk_level="blocked",
        breadth_score=48,
        regime_severity="moderate",
    )
    engine = StrategyEngine(settings)
    assert engine.evaluate("AAPL", bars, regime) is None
    result = engine.evaluate_watch_candidate("AAPL", bars, regime)
    assert result.setup is not None
    assert result.setup.signal_tier in {"B Watch Alert", "C Risky/Early Alert"}


def test_severe_bear_blocks_watch_alert() -> None:
    settings = Settings(enable_watchlist_alerts=True, enable_c_alerts=True)
    bars = make_bars([100 + i * 0.1 for i in range(80)], last_volume=4000)
    regime = MarketRegime(
        regime="bear",
        volatility=5.5,
        volume_state="high",
        trend_strength=0.3,
        is_safe=False,
        reason="Severe bear regime",
        risk_level="blocked",
        breadth_score=25,
        regime_severity="severe",
    )
    result = StrategyEngine(settings).evaluate_watch_candidate("AAPL", bars, regime)
    assert result.setup is None
    assert result.rejected_by == "regime"


def test_grade_for_score_maps_all_tiers() -> None:
    settings = Settings(
        min_score_a_plus_plus=75,
        min_score_a_plus=65,
        min_score_a=58,
        min_score_b=50,
        min_score_c=40,
    )
    engine = StrategyEngine(settings)
    assert engine.grade_for_score(80) == "A++ Signal"
    assert engine.grade_for_score(70) == "A+ Signal"
    assert engine.grade_for_score(60) == "A Signal"
    assert engine.grade_for_score(52) == "B Watch Alert"
    assert engine.grade_for_score(42) == "C Risky/Early Alert"


def test_c_grade_can_still_be_generated_for_internal_analytics_when_disabled() -> None:
    settings = Settings(enable_watchlist_alerts=True, enable_c_alerts=False, min_score_b=65)
    closes = [100 + i * 0.04 for i in range(80)]
    bars = make_bars(closes, last_volume=1200)
    regime = MarketRegime(
        regime="choppy",
        volatility=1.0,
        volume_state="normal",
        trend_strength=0.05,
        is_safe=False,
        reason="Weak trend strength",
        risk_level="elevated",
        breadth_score=52,
    )
    result = StrategyEngine(settings).evaluate_watch_candidate("AAPL", bars, regime)
    assert result.setup is not None
    assert result.setup.signal_tier == "C Risky/Early Alert"


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
        reason="stacked EMA/MACD momentum, RSI 61.2, breadth confirmation 80",
        do_not_enter_conditions=[],
    )
    formatted = SignalEngine.format_alert(signal, label="A+ Signal")
    lines = formatted.splitlines()
    assert lines[0] == "Predator Signal: A+ Signal"
    assert "Action: Trade candidate — manual review required" in formatted
    assert "Market Regime" not in formatted
    assert "Do Not Enter" not in formatted
    assert len(lines) <= 9


def test_watch_alert_format_is_short_and_observe_only() -> None:
    settings = Settings(enable_watchlist_alerts=True, enable_b_alerts=True, enable_c_alerts=True)
    bars = make_bars([100 + i * 0.15 for i in range(79)] + [111.7], last_volume=2600)
    regime = RegimeDetector().detect(bars, breadth_score=70)
    watch = StrategyEngine(settings).evaluate_watch_alert("AAPL", bars, regime)
    assert watch is not None
    formatted = SignalEngine.format_watch_alert(watch)
    lines = formatted.splitlines()
    assert lines[0].startswith("Predator Signal:")
    assert "Action: Observe only — not a trade entry" in formatted
    assert "Risk Warning" not in formatted
    assert "watch risks:" not in formatted
    assert len(lines) <= 9


def test_bear_watch_alert_note_is_short() -> None:
    settings = Settings(enable_watchlist_alerts=True, enable_b_alerts=True, enable_c_alerts=True)
    bars = make_bars([100 + i * 0.12 for i in range(80)], last_volume=3000)
    regime = MarketRegime(
        regime="bear",
        volatility=1.5,
        volume_state="normal",
        trend_strength=0.25,
        is_safe=False,
        reason="Moderate bear regime: trade entries blocked",
        risk_level="blocked",
        breadth_score=48,
        regime_severity="moderate",
    )
    result = StrategyEngine(settings).evaluate_watch_candidate("AAPL", bars, regime)
    assert result.setup is not None
    formatted = SignalEngine.format_watch_alert(result.setup, bear_regime=True)
    assert "Note: Bear regime active — reduced confidence — observe only." in formatted
    assert len(formatted.splitlines()) <= 9
