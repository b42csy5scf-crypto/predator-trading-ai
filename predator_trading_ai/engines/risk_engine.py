from dataclasses import dataclass
from typing import Optional

from predator_trading_ai.config import Settings, get_settings
from predator_trading_ai.engines.strategy_engine import StrategySetup
from predator_trading_ai.utils.watchlist import CORRELATION_GROUP_BY_TICKER, SECTOR_BY_TICKER
from predator_trading_ai.utils.validators import spread_pct


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    position_size: float
    risk_reward: float
    liquidity_score: Optional[float]
    liquidity_status: str
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
        liquidity_score: Optional[float],
        market_is_safe: bool,
        ticker: Optional[str] = None,
        active_positions: Optional[dict] = None,
        liquidity_status: str = "MEASURED",
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
        if liquidity_score is not None and liquidity_score < self.settings.min_liquidity_score:
            reasons.append(f"liquidity score too low: {liquidity_score:.0f}")
        min_confidence = min(self.settings.min_confidence, self.settings.min_score_a)
        if setup.score < min_confidence:
            reasons.append(f"confidence below minimum: {setup.score:.0f}%")
        if risk_reward < self.settings.min_risk_reward:
            reasons.append(f"risk/reward too low: {risk_reward:.2f}")
        if not market_is_safe:
            reasons.append("market regime is unsafe")
        reasons.extend(self._correlation_rejections(ticker or setup.ticker, active_positions or {}))

        risk_budget = account_equity * (self.settings.max_risk_per_trade_pct / 100)
        position_size = risk_budget / risk_per_share if risk_per_share > 0 else 0
        return RiskDecision(
            approved=len(reasons) == 0,
            position_size=round(max(position_size, 0), 4),
            risk_reward=round(risk_reward, 2),
            liquidity_score=round(liquidity_score, 2) if liquidity_score is not None else None,
            liquidity_status=liquidity_status,
            reasons=reasons,
        )

    def _correlation_rejections(self, ticker: str, active_positions: dict) -> list[str]:
        ticker = ticker.upper()
        sector = SECTOR_BY_TICKER.get(ticker)
        group = CORRELATION_GROUP_BY_TICKER.get(ticker)
        if not sector and not group:
            return []

        sector_count = 0
        group_count = 0
        for payload in active_positions.values():
            existing = str(payload.get("ticker", "")).upper()
            existing_sector = payload.get("sector") or SECTOR_BY_TICKER.get(existing)
            existing_group = payload.get("correlation_group") or CORRELATION_GROUP_BY_TICKER.get(existing)
            if sector and existing_sector == sector:
                sector_count += 1
            if group and existing_group == group:
                group_count += 1

        rejections = []
        if sector and sector_count >= self.settings.max_sector_positions:
            rejections.append(f"sector exposure cap reached for {sector}")
        if group and group_count >= self.settings.max_correlation_group_positions:
            rejections.append(f"correlation group cap reached for {group}")
        return rejections
