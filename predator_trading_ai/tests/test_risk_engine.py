from datetime import datetime, timezone

from predator_trading_ai.config import Settings
from predator_trading_ai.engines.risk_engine import RiskEngine
from predator_trading_ai.engines.strategy_engine import StrategySetup
from predator_trading_ai.main import LiquidityAssessment, PredatorTradingAI
from predator_trading_ai.data.market_data import MarketSnapshot


def setup(score: float = 75) -> StrategySetup:
    return StrategySetup(
        ticker="AAPL",
        direction="long",
        setup_type="breakout",
        score=score,
        entry_zone_low=100,
        entry_zone_high=101,
        stop_loss=98,
        targets=(105, 107, 110),
        reason="test",
        do_not_enter_conditions=[],
    )


def test_risk_engine_approves_valid_setup() -> None:
    engine = RiskEngine(Settings())
    decision = engine.evaluate(setup(), 100_000, 100, 100.5, 0, 0, 90, True)
    assert decision.approved is True
    assert decision.position_size > 0
    assert decision.risk_reward >= 1.5


def test_risk_engine_rejects_wide_spread_and_low_confidence() -> None:
    engine = RiskEngine(Settings())
    decision = engine.evaluate(setup(score=40), 100_000, 95, 105, 0, 0, 90, True)
    assert decision.approved is False
    assert any("spread too wide" in reason for reason in decision.reasons)
    assert any("confidence below minimum" in reason for reason in decision.reasons)


def test_options_confirmation_missing_liquidity_score_uses_spread_calculation() -> None:
    snapshot = MarketSnapshot("AAPL", 100, 100, 100.5, 1000, None, datetime.now(timezone.utc))

    liquidity = PredatorTradingAI.estimate_liquidity_score(snapshot, {"premium": 100_000})

    assert isinstance(liquidity, LiquidityAssessment)
    assert liquidity.status == "CALCULATED_FROM_SPREAD"
    assert liquidity.score is not None
    assert liquidity.score > 0


def test_options_confirmation_explicit_zero_liquidity_remains_measured_zero() -> None:
    snapshot = MarketSnapshot("AAPL", 100, 100, 100.5, 1000, None, datetime.now(timezone.utc))

    liquidity = PredatorTradingAI.estimate_liquidity_score(snapshot, {"liquidity_score": 0})

    assert liquidity.status == "PROVIDED_BY_OPTIONS_CONFIRMATION"
    assert liquidity.score == 0


def test_missing_bid_ask_liquidity_is_unavailable_not_zero() -> None:
    snapshot = MarketSnapshot("AAPL", 100, None, None, 1000, None, datetime.now(timezone.utc))

    liquidity = PredatorTradingAI.estimate_liquidity_score(snapshot, None)

    assert liquidity.status == "UNAVAILABLE"
    assert liquidity.score is None


def test_real_wide_spread_can_calculate_zero_liquidity() -> None:
    snapshot = MarketSnapshot("AAPL", 100, 100, 110, 1000, None, datetime.now(timezone.utc))

    liquidity = PredatorTradingAI.estimate_liquidity_score(snapshot, None)

    assert liquidity.status == "CALCULATED_FROM_SPREAD"
    assert liquidity.score == 0


def test_unavailable_liquidity_does_not_trigger_low_liquidity_rejection() -> None:
    engine = RiskEngine(Settings())

    decision = engine.evaluate(setup(), 100_000, None, None, 0, 0, None, True, liquidity_status="UNAVAILABLE")

    assert decision.approved is False
    assert any("spread too wide" in reason for reason in decision.reasons)
    assert not any("liquidity score too low" in reason for reason in decision.reasons)
    assert decision.liquidity_score is None
    assert decision.liquidity_status == "UNAVAILABLE"


def test_spread_veto_and_real_zero_liquidity_still_work() -> None:
    engine = RiskEngine(Settings())

    decision = engine.evaluate(setup(), 100_000, 100, 110, 0, 0, 0, True, liquidity_status="CALCULATED_FROM_SPREAD")

    assert decision.approved is False
    assert any("spread too wide" in reason for reason in decision.reasons)
    assert any("liquidity score too low: 0" in reason for reason in decision.reasons)
    assert decision.liquidity_score == 0
    assert decision.liquidity_status == "CALCULATED_FROM_SPREAD"


def test_live_trading_defaults_off() -> None:
    settings = Settings()
    assert settings.live_trading is False
