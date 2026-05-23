from dataclasses import dataclass
from typing import Optional

from predator_trading_ai.config import Settings, get_settings
from predator_trading_ai.engines.strategy_engine import StrategySetup
from predator_trading_ai.utils.validators import spread_pct


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    position_size: float
    risk_reward: float
    liquidity_score: float
    reasons: list[str]


class RiskEngine:
    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings = settings or get_settings()

    def evaluate(
        self,
        setup: StrategySetup,
        account_equity: float,
        bid: Optional[float],
        ask: Optional[float],
        open_trades: int,
        daily_loss_pct: float,
        liquidity_score: float,
        market_is_safe: bool,
    ) -> RiskDecision:
        reasons: list[str] = []
        entry = (setup.entry_zone_low + setup.entry_zone_high) / 2
        risk_per_share = abs(entry - setup.stop_loss)
        reward = abs(setup.targets[0] - entry)
        risk_reward = reward / risk_per_share if risk_per_share > 0 else 0
        spread = spread_pct(bid, ask)

        if account_equity <= 0:
            reasons.append("account equity is missing or invalid")
        if risk_per_share <= 0:
            reasons.append("stop loss does not define valid risk")
        if open_trades >= self.settings.max_open_trades:
            reasons.append("max open trades reached")
        if daily_loss_pct >= self.settings.max_daily_loss_pct:
            reasons.append("max daily loss reached")
        if spread > self.settings.max_spread_pct:
            reasons.append(f"spread too wide: {spread:.2f}%")
        if liquidity_score < self.settings.min_liquidity_score:
            reasons.append(f"liquidity score too low: {liquidity_score:.0f}")
        if setup.score < self.settings.min_confidence:
            reasons.append(f"confidence below minimum: {setup.score:.0f}%")
        if risk_reward < self.settings.min_risk_reward:
            reasons.append(f"risk/reward too low: {risk_reward:.2f}")
        if not market_is_safe:
            reasons.append("market regime is unsafe")

        risk_budget = account_equity * (self.settings.max_risk_per_trade_pct / 100)
        position_size = risk_budget / risk_per_share if risk_per_share > 0 else 0
        return RiskDecision(
            approved=len(reasons) == 0,
            position_size=round(max(position_size, 0), 4),
            risk_reward=round(risk_reward, 2),
            liquidity_score=round(liquidity_score, 2),
            reasons=reasons,
        )

