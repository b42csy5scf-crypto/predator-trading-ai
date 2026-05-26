from dataclasses import asdict, dataclass
from typing import Optional

from predator_trading_ai.database.db import Database
from predator_trading_ai.engines.regime_detector import MarketRegime
from predator_trading_ai.engines.risk_engine import RiskDecision
from predator_trading_ai.engines.strategy_engine import StrategySetup


@dataclass(frozen=True)
class TradingSignal:
    ticker: str
    direction: str
    setup_type: str
    entry_zone_low: float
    entry_zone_high: float
    target_1: float
    target_2: float
    target_3: float
    stop_loss: float
    risk_reward: float
    confidence: float
    expected_win_rate: Optional[float]
    position_size: float
    liquidity_score: float
    market_regime: str
    reason: str
    do_not_enter_conditions: list[str]
    gpt_explanation: Optional[str] = None
    status: str = "new"


class SignalEngine:
    def __init__(self, db: Optional[Database] = None) -> None:
        self.db = db

    def build_signal(
        self,
        setup: StrategySetup,
        risk: RiskDecision,
        regime: MarketRegime,
        expected_win_rate: Optional[float],
        explanation: Optional[str] = None,
    ) -> Optional[TradingSignal]:
        if not risk.approved:
            return None
        signal = TradingSignal(
            ticker=setup.ticker,
            direction=setup.direction,
            setup_type=setup.setup_type,
            entry_zone_low=setup.entry_zone_low,
            entry_zone_high=setup.entry_zone_high,
            target_1=setup.targets[0],
            target_2=setup.targets[1],
            target_3=setup.targets[2],
            stop_loss=setup.stop_loss,
            risk_reward=risk.risk_reward,
            confidence=setup.score,
            expected_win_rate=expected_win_rate,
            position_size=risk.position_size,
            liquidity_score=risk.liquidity_score,
            market_regime=regime.regime,
            reason=setup.reason,
            do_not_enter_conditions=setup.do_not_enter_conditions,
            gpt_explanation=explanation,
        )
        if self.db:
            self.db.insert_dict("signals", asdict(signal))
        return signal

    @staticmethod
    def format_alert(signal: TradingSignal, label: str = "Signal") -> str:
        expected = "n/a" if signal.expected_win_rate is None else f"{signal.expected_win_rate:.1f}%"
        conditions = "; ".join(signal.do_not_enter_conditions)
        return (
            f"Predator Trading AI {label}\n"
            f"Ticker: {signal.ticker}\n"
            f"Grade: {label}\n"
            f"Direction: {signal.direction}\n"
            f"Setup: {signal.setup_type}\n"
            f"Entry Zone: {signal.entry_zone_low:.2f} - {signal.entry_zone_high:.2f}\n"
            f"Targets: {signal.target_1:.2f}, {signal.target_2:.2f}, {signal.target_3:.2f}\n"
            f"Stop / Invalidation: {signal.stop_loss:.2f}\n"
            f"Risk/Reward: {signal.risk_reward:.2f}\n"
            f"Confidence: {signal.confidence:.0f}%\n"
            f"Expected Win Rate: {expected}\n"
            f"Position Size: {signal.position_size:.2f}\n"
            f"Liquidity Score: {signal.liquidity_score:.0f}\n"
            f"Market Regime: {signal.market_regime}\n"
            f"Reason: {signal.reason}\n"
            f"Do Not Enter If: {conditions}"
        )
