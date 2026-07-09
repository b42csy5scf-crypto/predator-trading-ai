from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

import pandas as pd

from predator_trading_ai.database.db import Database
from predator_trading_ai.engines.regime_detector import MarketRegime
from predator_trading_ai.engines.signal_engine import TradingSignal
from predator_trading_ai.engines.strategy_engine import StrategySetup
from predator_trading_ai.utils.watchlist import SECTOR_BY_TICKER


class SignalDiagnosticsRecorder:
    """Persists raw signal/candidate diagnostics without changing trading decisions."""

    TRADE_GRADES = {"A Signal", "A+ Signal", "A++ Signal"}

    def __init__(self, db: Database) -> None:
        self.db = db

    def record_accepted_signal(
        self,
        *,
        signal_id: Optional[int],
        active_signal_id: int,
        setup: StrategySetup,
        signal: TradingSignal,
        bars: pd.DataFrame,
        regime: MarketRegime,
        telegram_note: str,
        alert_type: str = "trade_candidate",
    ) -> None:
        self.record_accepted_setup(
            signal_id=signal_id,
            active_signal_id=active_signal_id,
            setup=setup,
            bars=bars,
            regime=regime,
            telegram_note=telegram_note,
            alert_type=alert_type,
        )
        self.initialize_outcome_from_signal(active_signal_id, signal, setup.signal_tier, alert_type)

    def record_accepted_setup(
        self,
        *,
        signal_id: Optional[int],
        active_signal_id: int,
        setup: StrategySetup,
        bars: pd.DataFrame,
        regime: MarketRegime,
        telegram_note: str,
        alert_type: str,
    ) -> None:
        if setup.signal_tier not in self.TRADE_GRADES and alert_type != "experimental_watch":
            return
        metrics = self.market_metrics(bars, setup, regime)
        self.db.insert_dict(
            "signal_diagnostics",
            {
                "signal_id": signal_id,
                "active_signal_id": active_signal_id,
                "ticker": setup.ticker,
                "grade": setup.signal_tier,
                "alert_type": alert_type,
                "score": setup.score,
                "entry_zone_low": setup.entry_zone_low,
                "entry_zone_high": setup.entry_zone_high,
                "stop_loss": setup.stop_loss,
                "tp1": setup.targets[0],
                "tp2": setup.targets[1],
                "tp3": setup.targets[2],
                "atr": metrics["atr"],
                "stop_distance_pct": metrics["stop_distance_pct"],
                "stop_distance_atr": metrics["stop_distance_atr"],
                "breakout_distance_atr": metrics["breakout_distance_atr"],
                "distance_from_ema21_atr": metrics["distance_from_ema21_atr"],
                "distance_from_ema50_atr": metrics["distance_from_ema50_atr"],
                "relative_volume": metrics["relative_volume"],
                "rsi": metrics["rsi"],
                "macd_minus_signal": metrics["macd_minus_signal"],
                "spy_trend": regime.spy_trend,
                "qqq_trend": regime.qqq_trend,
                "regime": regime.regime,
                "breadth_score": regime.breadth_score,
                "sector": SECTOR_BY_TICKER.get(setup.ticker),
                "telegram_note": telegram_note,
                "scoring_components_json": list(setup.scoring_components),
                "raw_metrics_json": metrics,
            },
        )
        active_rows = self.db.fetch_all("SELECT * FROM active_signals WHERE id = ?", [active_signal_id])
        if active_rows:
            self.initialize_outcome_from_active_row(active_rows[0])

    def record_rejected_candidate(
        self,
        *,
        ticker: str,
        final_score: float,
        computed_grade: str,
        first_rejection_gate: Optional[str],
        rejection_reasons: list[str],
        conditions_passed: list[str],
        conditions_failed: list[str],
        bars: Optional[pd.DataFrame],
        regime: Optional[MarketRegime],
    ) -> None:
        if final_score < 50:
            return
        setup = self.synthetic_setup(ticker, final_score, computed_grade, bars)
        metrics = self.market_metrics(bars, setup, regime) if bars is not None and not bars.empty else {}
        self.db.insert_dict(
            "rejected_candidate_diagnostics",
            {
                "ticker": ticker,
                "final_score": final_score,
                "computed_grade": computed_grade,
                "first_rejection_gate": first_rejection_gate,
                "rejection_reasons_json": rejection_reasons,
                "conditions_passed_json": conditions_passed,
                "conditions_failed_json": conditions_failed,
                "why_not_trade": "; ".join(rejection_reasons) or "candidate was not accepted",
                "raw_metrics_json": {
                    **metrics,
                    "regime": regime.regime if regime else None,
                    "spy_trend": regime.spy_trend if regime else None,
                    "qqq_trend": regime.qqq_trend if regime else None,
                    "breadth_score": regime.breadth_score if regime else None,
                },
            },
        )

    def initialize_outcome_from_signal(
        self,
        active_signal_id: int,
        signal: TradingSignal,
        grade: str,
        alert_type: str = "trade_candidate",
    ) -> None:
        entry = (signal.entry_zone_low + signal.entry_zone_high) / 2
        risk = max(abs(entry - signal.stop_loss), 0.01)
        self.db.execute(
            """
            INSERT INTO signal_outcome_diagnostics (
                active_signal_id, ticker, grade, alert_type, direction, entry_price,
                original_stop_loss, risk_per_share, max_favorable_price,
                max_adverse_price
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(active_signal_id) DO NOTHING
            """,
            [
                active_signal_id,
                signal.ticker,
                grade,
                alert_type,
                signal.direction,
                entry,
                signal.stop_loss,
                risk,
                entry,
                entry,
            ],
        )

    def update_outcome(
        self,
        *,
        active_signal_id: int,
        current_price: float,
        event: Optional[str] = None,
        final_outcome: Optional[str] = None,
        exit_reason: Optional[str] = None,
    ) -> None:
        rows = self.db.fetch_all(
            "SELECT * FROM signal_outcome_diagnostics WHERE active_signal_id = ?",
            [active_signal_id],
        )
        if not rows:
            active_rows = self.db.fetch_all("SELECT * FROM active_signals WHERE id = ?", [active_signal_id])
            if not active_rows:
                return
            self.initialize_outcome_from_active_row(active_rows[0])
            rows = self.db.fetch_all(
                "SELECT * FROM signal_outcome_diagnostics WHERE active_signal_id = ?",
                [active_signal_id],
            )
            if not rows:
                return
        row = rows[0]
        entry = float(row["entry_price"])
        risk = max(float(row["risk_per_share"]), 0.01)
        direction = str(row["direction"])
        max_favorable, max_adverse = self._updated_extremes(row, current_price, entry, direction)
        mfe_r, mae_r, current_r = self._r_metrics(entry, risk, current_price, max_favorable, max_adverse, direction)
        event_columns = {
            "tp1": "tp1_hit_at",
            "tp2": "tp2_hit_at",
            "tp3": "tp3_hit_at",
            "stop_loss": "sl_hit_at",
            "breakeven": "sl_hit_at",
        }
        event_column = event_columns.get(event or "")
        event_sql = f", {event_column} = COALESCE({event_column}, CURRENT_TIMESTAMP)" if event_column else ""
        self.db.execute(
            f"""
            UPDATE signal_outcome_diagnostics
            SET updated_at = CURRENT_TIMESTAMP,
                max_favorable_price = ?,
                max_adverse_price = ?,
                mfe_r = ?,
                mae_r = ?,
                current_r = ?,
                holding_seconds = (julianday('now') - julianday(created_at)) * 86400,
                final_outcome = COALESCE(?, final_outcome),
                exit_reason = COALESCE(?, exit_reason)
                {event_sql}
            WHERE active_signal_id = ?
            """,
            [
                max_favorable,
                max_adverse,
                mfe_r,
                mae_r,
                current_r,
                final_outcome,
                exit_reason,
                active_signal_id,
            ],
        )

    def initialize_outcome_from_active_row(self, row: Any) -> None:
        entry = (float(row["entry_zone_low"]) + float(row["entry_zone_high"])) / 2
        original_stop = float(row["original_stop_loss"] or row["stop_loss"])
        risk = max(abs(entry - original_stop), 0.01)
        self.db.execute(
            """
            INSERT INTO signal_outcome_diagnostics (
                active_signal_id, ticker, grade, alert_type, direction, entry_price,
                original_stop_loss, risk_per_share, max_favorable_price,
                max_adverse_price
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(active_signal_id) DO NOTHING
            """,
            [
                int(row["id"]),
                row["ticker"],
                row["grade"],
                row["alert_type"] if "alert_type" in row.keys() else "trade_candidate",
                row["direction"],
                entry,
                original_stop,
                risk,
                entry,
                entry,
            ],
        )

    def cleanup(self, retention_days: int = 30) -> None:
        self.db.cleanup_signal_diagnostics(retention_days)

    @staticmethod
    def market_metrics(
        bars: pd.DataFrame,
        setup: Optional[StrategySetup],
        regime: Optional[MarketRegime],
    ) -> dict[str, Any]:
        latest = bars.iloc[-1]
        close = float(latest["close"])
        atr = float(latest.get("atr_14", close * 0.02) or close * 0.02)
        atr_floor = max(atr, close * 0.005, 0.01)
        previous_high = float(bars["high"].iloc[-21:-1].max()) if len(bars) >= 21 else None
        ema_21 = float(latest.get("ema_21", close) or close)
        ema_50 = float(latest.get("ema_50", close) or close)
        entry = None
        stop = None
        if setup is not None:
            entry = (setup.entry_zone_low + setup.entry_zone_high) / 2
            stop = setup.stop_loss
        stop_distance = abs(entry - stop) if entry is not None and stop is not None else None
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "close": close,
            "atr": atr,
            "stop_distance_pct": (stop_distance / entry * 100) if entry and stop_distance is not None else None,
            "stop_distance_atr": (stop_distance / atr_floor) if stop_distance is not None else None,
            "breakout_distance_atr": ((close - previous_high) / atr_floor) if previous_high is not None else None,
            "distance_from_ema21_atr": (abs(close - ema_21) / atr_floor),
            "distance_from_ema50_atr": (abs(close - ema_50) / atr_floor),
            "relative_volume": float(latest.get("relative_volume", 0) or 0),
            "rsi": float(latest.get("rsi_14", 50) or 50),
            "macd_minus_signal": float(latest.get("macd", 0) or 0) - float(latest.get("macd_signal", 0) or 0),
            "spy_trend": regime.spy_trend if regime else None,
            "qqq_trend": regime.qqq_trend if regime else None,
            "regime": regime.regime if regime else None,
            "breadth_score": regime.breadth_score if regime else None,
        }

    @staticmethod
    def synthetic_setup(
        ticker: str,
        score: float,
        grade: str,
        bars: Optional[pd.DataFrame],
    ) -> Optional[StrategySetup]:
        if bars is None or bars.empty:
            return None
        close = float(bars.iloc[-1]["close"])
        atr = float(bars.iloc[-1].get("atr_14", close * 0.02) or close * 0.02)
        return StrategySetup(
            ticker=ticker,
            direction="long",
            setup_type="diagnostic candidate",
            score=score,
            entry_zone_low=close,
            entry_zone_high=close,
            stop_loss=close - atr,
            targets=(close + atr, close + atr * 2, close + atr * 3),
            reason="diagnostic only",
            do_not_enter_conditions=[],
            signal_tier=grade,
        )

    @staticmethod
    def _updated_extremes(row: Any, current_price: float, entry: float, direction: str) -> tuple[float, float]:
        current_favorable = float(row["max_favorable_price"] if row["max_favorable_price"] is not None else entry)
        current_adverse = float(row["max_adverse_price"] if row["max_adverse_price"] is not None else entry)
        if direction == "short":
            return min(current_favorable, current_price), max(current_adverse, current_price)
        return max(current_favorable, current_price), min(current_adverse, current_price)

    @staticmethod
    def _r_metrics(
        entry: float,
        risk: float,
        current_price: float,
        max_favorable: float,
        max_adverse: float,
        direction: str,
    ) -> tuple[float, float, float]:
        if direction == "short":
            mfe = (entry - max_favorable) / risk
            mae = (entry - max_adverse) / risk
            current = (entry - current_price) / risk
        else:
            mfe = (max_favorable - entry) / risk
            mae = (max_adverse - entry) / risk
            current = (current_price - entry) / risk
        return round(mfe, 4), round(mae, 4), round(current, 4)
