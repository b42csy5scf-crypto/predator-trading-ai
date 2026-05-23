from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import pandas as pd

from predator_trading_ai.database.db import Database
from predator_trading_ai.engines.regime_detector import MarketRegime
from predator_trading_ai.engines.risk_engine import RiskDecision
from predator_trading_ai.engines.strategy_engine import StrategySetup
from predator_trading_ai.utils.watchlist import CORRELATION_GROUP_BY_TICKER, SECTOR_BY_TICKER


@dataclass(frozen=True)
class ShadowDiagnostics:
    volume_condition: str
    trend_condition: str
    volatility_condition: str
    correlation_condition: str
    score: Optional[float]
    price: Optional[float]


class ShadowModeLogger:
    def __init__(self, db: Database) -> None:
        self.db = db

    def diagnostics(
        self,
        ticker: str,
        bars: pd.DataFrame,
        active_positions: Optional[dict] = None,
        score: Optional[float] = None,
    ) -> ShadowDiagnostics:
        if bars.empty:
            return ShadowDiagnostics("missing data", "missing data", "missing data", "unknown", score, None)

        latest = bars.iloc[-1]
        price = float(latest["close"])
        volume_ratio = float(latest.get("relative_volume", 0) or 0)
        atr_pct = float(latest.get("atr_pct", 0) or 0)
        ema_50 = float(latest.get("ema_50", price))
        ema_200 = float(latest.get("ema_200", price))

        volume_condition = "pass" if volume_ratio >= 1.15 else f"fail: relative volume {volume_ratio:.2f}"
        trend_condition = "pass" if price > ema_50 >= ema_200 else "fail: EMA50/EMA200 bull alignment missing"
        volatility_condition = "pass" if atr_pct <= 5.0 else f"fail: ATR {atr_pct:.2f}%"
        correlation_condition = self._correlation_condition(ticker, active_positions or {})
        return ShadowDiagnostics(volume_condition, trend_condition, volatility_condition, correlation_condition, score, price)

    def log(
        self,
        ticker: str,
        status: str,
        regime: MarketRegime,
        diagnostics: ShadowDiagnostics,
        setup: Optional[StrategySetup] = None,
        risk: Optional[RiskDecision] = None,
        rejection_stage: Optional[str] = None,
        rejection_reason: Optional[str] = None,
    ) -> int:
        entry_price = None
        target_price = None
        stop_loss = None
        direction = None
        setup_type = None
        if setup is not None:
            direction = setup.direction
            setup_type = setup.setup_type
            entry_price = (setup.entry_zone_low + setup.entry_zone_high) / 2
            target_price = setup.targets[0]
            stop_loss = setup.stop_loss

        shadow_id = self.db.insert_dict(
            "shadow_signals",
            {
                "ticker": ticker,
                "status": status,
                "direction": direction,
                "setup_type": setup_type,
                "rejection_stage": rejection_stage,
                "rejection_reason": rejection_reason,
                "regime": regime.regime,
                "regime_reason": regime.reason,
                "score": diagnostics.score if diagnostics.score is not None else (setup.score if setup else None),
                "price": diagnostics.price,
                "entry_price": entry_price,
                "target_price": target_price,
                "stop_loss": stop_loss,
                "volume_condition": diagnostics.volume_condition,
                "trend_condition": diagnostics.trend_condition,
                "volatility_condition": diagnostics.volatility_condition,
                "correlation_condition": diagnostics.correlation_condition,
                "liquidity_score": risk.liquidity_score if risk else None,
                "risk_reward": risk.risk_reward if risk else None,
            },
        )
        if status == "rejected":
            self.db.insert_dict(
                "rejected_signals",
                {
                    "shadow_signal_id": shadow_id,
                    "ticker": ticker,
                    "rejection_stage": rejection_stage or "unknown",
                    "rejection_reason": rejection_reason or "unknown",
                    "regime": regime.regime,
                    "score": diagnostics.score if diagnostics.score is not None else (setup.score if setup else None),
                    "price": diagnostics.price,
                },
            )
        return shadow_id

    def update_outcomes(self, ticker: str, bars: pd.DataFrame) -> None:
        if bars.empty:
            return
        rows = self.db.fetch_all(
            """
            SELECT id, status, direction, entry_price, target_price, stop_loss, created_at
            FROM shadow_signals
            WHERE ticker = ? AND outcome = 'pending' AND direction IS NOT NULL
            ORDER BY created_at ASC
            """,
            [ticker],
        )
        if not rows:
            return

        frame = bars.copy()
        timestamp_column = "timestamp" if "timestamp" in frame else None
        if timestamp_column:
            frame[timestamp_column] = pd.to_datetime(frame[timestamp_column], utc=True)

        for row in rows:
            future = frame
            if timestamp_column:
                created_at = pd.to_datetime(row["created_at"], utc=True)
                future = frame[frame[timestamp_column] > created_at]
            if future.empty:
                continue
            outcome, outcome_r = self._resolve_outcome(row, future)
            if outcome == "pending":
                continue
            checked_at = datetime.now(timezone.utc).isoformat()
            self.db.execute(
                "UPDATE shadow_signals SET outcome = ?, outcome_checked_at = ?, outcome_r = ? WHERE id = ?",
                [outcome, checked_at, outcome_r, row["id"]],
            )
            if row["status"] == "rejected":
                self.db.execute(
                    """
                    UPDATE rejected_signals
                    SET outcome = ?, outcome_checked_at = ?, would_have_won = ?
                    WHERE shadow_signal_id = ?
                    """,
                    [outcome, checked_at, 1 if outcome == "target_hit" else 0, row["id"]],
                )

    @staticmethod
    def _resolve_outcome(row, future: pd.DataFrame) -> tuple[str, Optional[float]]:
        direction = row["direction"]
        entry = float(row["entry_price"])
        target = float(row["target_price"])
        stop = float(row["stop_loss"])
        risk = abs(entry - stop)
        if risk <= 0:
            return "invalid", None

        for _, bar in future.iterrows():
            high = float(bar["high"])
            low = float(bar["low"])
            if direction == "long":
                if low <= stop:
                    return "stop_hit", -1.0
                if high >= target:
                    return "target_hit", abs(target - entry) / risk
            else:
                if high >= stop:
                    return "stop_hit", -1.0
                if low <= target:
                    return "target_hit", abs(entry - target) / risk
        return "pending", None

    @staticmethod
    def _correlation_condition(ticker: str, active_positions: dict) -> str:
        ticker = ticker.upper()
        sector = SECTOR_BY_TICKER.get(ticker)
        group = CORRELATION_GROUP_BY_TICKER.get(ticker)
        sector_count = 0
        group_count = 0
        for payload in active_positions.values():
            existing = str(payload.get("ticker", "")).upper()
            if sector and (payload.get("sector") or SECTOR_BY_TICKER.get(existing)) == sector:
                sector_count += 1
            if group and (payload.get("correlation_group") or CORRELATION_GROUP_BY_TICKER.get(existing)) == group:
                group_count += 1
        if group_count >= 2:
            return f"fail: crowded correlation group {group}"
        if sector_count >= 3:
            return f"fail: crowded sector {sector}"
        return "pass"
