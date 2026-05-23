from predator_trading_ai.config import Settings
from predator_trading_ai.engines.risk_engine import RiskEngine
from predator_trading_ai.engines.strategy_engine import StrategySetup


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


def test_live_trading_defaults_off() -> None:
    settings = Settings()
    assert settings.live_trading is False

