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
        self.last_signal_id: Optional[int] = None

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
            self.last_signal_id = self.db.insert_dict("signals", asdict(signal))
        else:
            self.last_signal_id = None
        return signal

    @staticmethod
    def format_alert(signal: TradingSignal, label: str = "Signal") -> str:
        reason = SignalEngine.short_note(signal.reason, observe_only=False)
        return (
            f"Predator Signal: {label}\n"
            f"Ticker: {signal.ticker}\n"
            f"Score: {signal.confidence:.0f}%\n"
            f"Risk: {SignalEngine.risk_label(label, signal.confidence)}\n"
            f"Action: Trade candidate — manual review required\n"
            f"Entry: {signal.entry_zone_low:.2f} - {signal.entry_zone_high:.2f}\n"
            f"Stop: {signal.stop_loss:.2f}\n"
            f"TP: {signal.target_1:.2f} / {signal.target_2:.2f} / {signal.target_3:.2f}\n"
            f"Note: {reason}"
        )

    @staticmethod
    def format_watch_alert(setup: StrategySetup, bear_regime: bool = False) -> str:
        note = SignalEngine.short_note(setup.reason, observe_only=True, bear_regime=bear_regime)
        return (
            f"Predator Signal: {setup.signal_tier}\n"
            f"Ticker: {setup.ticker}\n"
            f"Score: {setup.score:.0f}%\n"
            f"Risk: {SignalEngine.risk_label(setup.signal_tier, setup.score)}\n"
            f"Action: Observe only — not a trade entry\n"
            f"Entry: {setup.entry_zone_low:.2f} - {setup.entry_zone_high:.2f}\n"
            f"Stop: {setup.stop_loss:.2f}\n"
            f"TP: {setup.targets[0]:.2f} / {setup.targets[1]:.2f} / {setup.targets[2]:.2f}\n"
            f"Note: {note}"
        )

    @staticmethod
    def risk_label(label: str, score: float) -> str:
        if label in {"B Watch Alert", "C Risky/Early Alert"}:
            return "Medium / Watch only" if label == "B Watch Alert" else "High / Watch only"
        if label == "A++ Signal" or score >= 75:
            return "Low"
        if label == "A+ Signal" or score >= 65:
            return "Medium"
        return "High"

    @staticmethod
    def short_note(reason: str, observe_only: bool, bear_regime: bool = False) -> str:
        first = (reason or "Setup detected").split(";")[0].split(",")[0].strip()
        if not first:
            first = "Setup detected"
        suffix = "observe only" if observe_only else "manual review required"
        if bear_regime:
            first = "Bear regime active — reduced confidence"
        return f"{first[:90]} — {suffix}."
