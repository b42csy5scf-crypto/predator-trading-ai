from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

import pandas as pd

from predator_trading_ai.config import Settings
from predator_trading_ai.database.db import Database
from predator_trading_ai.engines.regime_detector import MarketRegime
from predator_trading_ai.engines.signal_engine import TradingSignal
from predator_trading_ai.engines.strategy_engine import StrategySetup
from predator_trading_ai.utils.validators import spread_pct
from predator_trading_ai.utils.watchlist import SECTOR_BY_TICKER


EASTERN = ZoneInfo("America/New_York")


class SignalDiagnosticsRecorder:
    """Persists raw signal/candidate diagnostics without changing trading decisions."""

    TRADE_GRADES = {"A Signal", "A+ Signal", "A++ Signal"}
    STRATEGY_VERSION = "1.0"
    SCHEMA_VERSION = "research-schema-v1.0"
    RESEARCH_DATASET_VERSION = "v1.0"

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
        settings: Optional[Settings] = None,
        snapshot: Any = None,
        market_context: Optional[dict[str, Any]] = None,
        open_positions_count: Optional[int] = None,
        open_positions_same_sector: Optional[int] = None,
        git_commit_hash: Optional[str] = None,
    ) -> None:
        self.record_accepted_setup(
            signal_id=signal_id,
            active_signal_id=active_signal_id,
            setup=setup,
            bars=bars,
            regime=regime,
            telegram_note=telegram_note,
            alert_type=alert_type,
            settings=settings,
            snapshot=snapshot,
            market_context=market_context,
            open_positions_count=open_positions_count,
            open_positions_same_sector=open_positions_same_sector,
            git_commit_hash=git_commit_hash,
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
        settings: Optional[Settings] = None,
        snapshot: Any = None,
        market_context: Optional[dict[str, Any]] = None,
        open_positions_count: Optional[int] = None,
        open_positions_same_sector: Optional[int] = None,
        git_commit_hash: Optional[str] = None,
    ) -> None:
        if setup.signal_tier not in self.TRADE_GRADES and alert_type != "experimental_watch":
            return
        metrics = self.market_metrics(bars, setup, regime)
        entry_quality = self.entry_quality_metrics(bars, setup)
        market_context_metrics = self.market_context_metrics(
            snapshot=snapshot,
            market_context=market_context,
            regime=regime,
            open_positions_count=open_positions_count,
            open_positions_same_sector=open_positions_same_sector,
        )
        config_hash = self.record_config(settings) if settings is not None else None
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
                "git_commit_hash": git_commit_hash or self.git_commit_hash(),
                "strategy_version": self.STRATEGY_VERSION,
                "schema_version": self.SCHEMA_VERSION,
                "research_dataset_version": self.RESEARCH_DATASET_VERSION,
                "config_hash": config_hash,
                **entry_quality,
                **market_context_metrics,
                "scoring_components_json": list(setup.scoring_components),
                "raw_metrics_json": {
                    **metrics,
                    **entry_quality,
                    **market_context_metrics,
                },
            },
        )
        active_rows = self.db.fetch_all("SELECT * FROM active_signals WHERE id = ?", [active_signal_id])
        if active_rows:
            self.initialize_outcome_from_active_row(active_rows[0])
        self.record_price_path(
            signal_id=active_signal_id,
            price=metrics["close"],
            high=entry_quality.get("entry_high"),
            low=entry_quality.get("entry_low"),
            timestamp=metrics["timestamp"],
            event_type="entry",
        )

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
        entry_quality = self.entry_quality_metrics(bars, setup) if bars is not None and not bars.empty else {}
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
                **entry_quality,
                "raw_metrics_json": {
                    **metrics,
                    **entry_quality,
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
        high: Optional[float] = None,
        low: Optional[float] = None,
        timestamp: Optional[str] = None,
        event: Optional[str] = None,
        final_outcome: Optional[str] = None,
        exit_reason: Optional[str] = None,
        exit_atr: Optional[float] = None,
    ) -> None:
        event_type = event or "scan"
        self.record_price_path(
            signal_id=active_signal_id,
            price=current_price,
            high=high,
            low=low,
            timestamp=timestamp,
            event_type=event_type,
        )
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
        previous_mfe = float(row["mfe_r"] or 0)
        previous_mae = float(row["mae_r"] or 0)
        time_to_mfe_sql = ", time_to_mfe_seconds = CASE WHEN ? > ? THEN (julianday('now') - julianday(created_at)) * 86400 ELSE time_to_mfe_seconds END"
        time_to_mae_sql = ", time_to_mae_seconds = CASE WHEN ? < ? THEN (julianday('now') - julianday(created_at)) * 86400 ELSE time_to_mae_seconds END"
        thresholds_sql = """
                , time_to_025r_seconds = CASE WHEN ? >= 0.25 THEN COALESCE(time_to_025r_seconds, (julianday('now') - julianday(created_at)) * 86400) ELSE time_to_025r_seconds END
                , time_to_050r_seconds = CASE WHEN ? >= 0.50 THEN COALESCE(time_to_050r_seconds, (julianday('now') - julianday(created_at)) * 86400) ELSE time_to_050r_seconds END
                , time_to_075r_seconds = CASE WHEN ? >= 0.75 THEN COALESCE(time_to_075r_seconds, (julianday('now') - julianday(created_at)) * 86400) ELSE time_to_075r_seconds END
                , time_to_100r_seconds = CASE WHEN ? >= 1.00 THEN COALESCE(time_to_100r_seconds, (julianday('now') - julianday(created_at)) * 86400) ELSE time_to_100r_seconds END
        """
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
                exit_reason = COALESCE(?, exit_reason),
                exit_price = COALESCE(?, exit_price),
                exit_timestamp = COALESCE(?, exit_timestamp),
                exit_atr = COALESCE(?, exit_atr),
                realized_r = COALESCE(?, realized_r)
                {time_to_mfe_sql}
                {time_to_mae_sql}
                {thresholds_sql}
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
                current_price if final_outcome else None,
                datetime.now(timezone.utc).isoformat() if final_outcome else None,
                exit_atr,
                current_r if final_outcome else None,
                mfe_r,
                previous_mfe,
                mae_r,
                previous_mae,
                mfe_r,
                mfe_r,
                mfe_r,
                mfe_r,
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

    def record_price_path(
        self,
        *,
        signal_id: int,
        price: float,
        high: Optional[float] = None,
        low: Optional[float] = None,
        timestamp: Optional[str] = None,
        event_type: str = "scan",
    ) -> None:
        self.db.insert_dict(
            "price_path",
            {
                "signal_id": signal_id,
                "timestamp": timestamp or datetime.now(timezone.utc).isoformat(),
                "price": price,
                "high": high if high is not None else price,
                "low": low if low is not None else price,
                "event_type": event_type,
            },
        )

    def record_universe_snapshot(
        self,
        *,
        symbols_scanned: int,
        symbols_skipped: int,
        api_failures: int,
        missing_market_data: int,
        symbols_successfully_evaluated: int,
        timestamp: Optional[str] = None,
    ) -> None:
        self.db.insert_dict(
            "universe_snapshot",
            {
                "timestamp": timestamp or datetime.now(timezone.utc).isoformat(),
                "symbols_scanned": symbols_scanned,
                "symbols_skipped": symbols_skipped,
                "api_failures": api_failures,
                "missing_market_data": missing_market_data,
                "symbols_successfully_evaluated": symbols_successfully_evaluated,
            },
        )

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
    def entry_quality_metrics(bars: pd.DataFrame, setup: Optional[StrategySetup]) -> dict[str, Any]:
        latest = bars.iloc[-1]
        previous = bars.iloc[-2] if len(bars) >= 2 else latest
        close = float(latest["close"])
        entry_open = float(latest.get("open", close) or close)
        previous_close = float(previous.get("close", close) or close)
        atr = float(latest.get("atr_14", close * 0.02) or close * 0.02)
        atr_floor = max(atr, close * 0.005, 0.01)
        ema_21 = float(latest.get("ema_21", close) or close)
        ema_50 = float(latest.get("ema_50", close) or close)
        swing_low = float(bars["low"].tail(20).min()) if "low" in bars else None
        previous_highs = bars["high"].iloc[-21:-1] if len(bars) >= 21 else bars["high"].iloc[:-1]
        recent_high = float(previous_highs.max()) if not previous_highs.empty else None
        breakout_distance_atr = ((close - recent_high) / atr_floor) if recent_high is not None else None
        breakout_indices = bars.index[(bars["close"] > recent_high)] if recent_high is not None else []
        bars_since_breakout = None
        if recent_high is not None and len(breakout_indices) > 0:
            bars_since_breakout = int(len(bars) - 1 - bars.index.get_loc(breakout_indices[-1]))
        stop = setup.stop_loss if setup is not None else None
        return {
            "breakout_distance_atr": breakout_distance_atr,
            "distance_from_ema21": close - ema_21,
            "distance_from_ema50": close - ema_50,
            "distance_from_recent_swing_low": (close - swing_low) if swing_low is not None else None,
            "stop_to_swing_low_distance": (stop - swing_low) if stop is not None and swing_low is not None else None,
            "bars_since_breakout": bars_since_breakout,
            "entry_open": entry_open,
            "entry_high": float(latest.get("high", close) or close),
            "entry_low": float(latest.get("low", close) or close),
            "entry_close": close,
            "entry_volume": float(latest.get("volume", 0) or 0),
            "previous_open": float(previous.get("open", previous.get("close", close)) or close),
            "previous_high": float(previous.get("high", previous.get("close", close)) or close),
            "previous_low": float(previous.get("low", previous.get("close", close)) or close),
            "previous_close": previous_close,
            "previous_volume": float(previous.get("volume", 0) or 0),
            "gap_flag": int(entry_open != previous_close),
        }

    @staticmethod
    def market_context_metrics(
        *,
        snapshot: Any,
        market_context: Optional[dict[str, Any]],
        regime: MarketRegime,
        open_positions_count: Optional[int],
        open_positions_same_sector: Optional[int],
    ) -> dict[str, Any]:
        timestamp = getattr(snapshot, "timestamp", datetime.now(timezone.utc)) if snapshot is not None else datetime.now(timezone.utc)
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        eastern_timestamp = timestamp.astimezone(EASTERN)
        eastern_hour = eastern_timestamp.hour
        eastern_minute = eastern_timestamp.minute
        minutes_after_market_open = max((eastern_hour * 60 + eastern_minute) - (9 * 60 + 30), 0)
        bid = getattr(snapshot, "bid", None) if snapshot is not None else None
        ask = getattr(snapshot, "ask", None) if snapshot is not None else None
        spread = spread_pct(bid, ask)
        price = float(getattr(snapshot, "price", 0) or 0) if snapshot is not None else 0.0
        slippage_proxy = (spread / 2) if spread != float("inf") else None
        return {
            "spy_state": regime.spy_trend,
            "qqq_state": regime.qqq_trend,
            "vix_value": (market_context or {}).get("VIX"),
            "spread_at_entry": None if spread == float("inf") else spread,
            "slippage_proxy": slippage_proxy,
            "minutes_after_market_open": minutes_after_market_open,
            "day_of_week": eastern_timestamp.weekday(),
            "open_positions_count": open_positions_count,
            "open_positions_same_sector": open_positions_same_sector,
        }

    def record_config(self, settings: Settings) -> str:
        payload = self.sanitized_config(settings)
        encoded = json.dumps(payload, sort_keys=True, default=str)
        config_hash = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        self.db.execute(
            """
            INSERT INTO config_snapshots (config_hash, config_json)
            VALUES (?, ?)
            ON CONFLICT(config_hash) DO NOTHING
            """,
            [config_hash, encoded],
        )
        return config_hash

    @staticmethod
    def sanitized_config(settings: Settings) -> dict[str, Any]:
        if hasattr(settings, "model_dump"):
            payload = settings.model_dump()
        elif is_dataclass(settings):
            payload = asdict(settings)
        else:
            payload = dict(vars(settings))
        redacted_tokens = ("key", "secret", "token", "phrase", "password")
        return {
            key: ("<redacted>" if any(token in key.lower() for token in redacted_tokens) else value)
            for key, value in payload.items()
        }

    @staticmethod
    def git_commit_hash() -> str:
        try:
            completed = subprocess.run(
                ["git", "rev-parse", "--short=12", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
                timeout=2,
            )
            return completed.stdout.strip()
        except Exception:
            return "unknown"

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
