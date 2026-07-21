from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import asdict, dataclass, is_dataclass
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
from predator_trading_ai.utils.logger import setup_logger


EASTERN = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class ConditionRecord:
    condition_key: str
    display_name: str
    lhs_value: Any = None
    rhs_value: Any = None
    operator: str = ""
    result: str = "UNKNOWN"
    is_blocking: bool = False
    evaluation_order: int = 0
    reason_code: Optional[str] = None


class SignalDiagnosticsRecorder:
    """Persists raw signal/candidate diagnostics without changing trading decisions."""

    TRADE_GRADES = {"A Signal", "A+ Signal", "A++ Signal"}
    STRATEGY_VERSION = "1.0"
    SCHEMA_VERSION = "research-schema-v1.0"
    RESEARCH_DATASET_VERSION = "v1.0"
    CLASSIFICATION_FORMAT_VERSION = 2
    FORENSICS_FORMAT_VERSION = 1
    SPREAD_FORMULA_VERSION = "midpoint_v1"

    def __init__(self, db: Database) -> None:
        self.db = db
        self.logger = setup_logger(__name__)

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
        liquidity_score: Optional[float] = None,
        liquidity_score_status: Optional[str] = None,
    ) -> None:
        effective_liquidity_score = liquidity_score if liquidity_score is not None else signal.liquidity_score
        effective_liquidity_status = liquidity_score_status or ("MEASURED" if effective_liquidity_score is not None else "UNAVAILABLE")
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
            liquidity_score=effective_liquidity_score,
            liquidity_score_status=effective_liquidity_status,
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
        liquidity_score: Optional[float] = None,
        liquidity_score_status: Optional[str] = None,
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
        classification = self.classification_payload(
            raw_score=setup.score,
            setup_grade=setup.signal_tier,
            displayed_grade=setup.signal_tier,
            eligibility_status="ELIGIBLE",
            eligibility_stage="accepted",
            block_reason_display=None,
            final_acceptance_status=(
                "ACCEPTED_STRONG_B_EXPERIMENTAL"
                if alert_type == "experimental_watch"
                else "ACCEPTED_TRADE_CANDIDATE"
            ),
        )
        quote = self.quote_forensics_payload(
            snapshot=snapshot,
            settings=settings,
            latest=bars.iloc[-1] if bars is not None and not bars.empty else None,
            liquidity_score=liquidity_score,
            liquidity_score_status=liquidity_score_status,
            force=True,
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
                **classification,
                **quote,
                "scoring_components_json": list(setup.scoring_components),
                "raw_metrics_json": {
                    **metrics,
                    **entry_quality,
                    **market_context_metrics,
                    **self.raw_quote_metrics(quote),
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
        settings: Optional[Settings] = None,
        evaluated_conditions: Optional[list[dict[str, Any]]] = None,
        snapshot: Any = None,
        risk_decision: Any = None,
        risk_engine_reached: bool = False,
    ) -> None:
        if final_score < 50:
            return
        setup = self.synthetic_setup(ticker, final_score, computed_grade, bars)
        metrics = self.market_metrics(bars, setup, regime) if bars is not None and not bars.empty else {}
        entry_quality = self.entry_quality_metrics(bars, setup) if bars is not None and not bars.empty else {}
        canonical = self.canonical_rejected_conditions(
            final_score=final_score,
            computed_grade=computed_grade,
            first_rejection_gate=first_rejection_gate,
            rejection_reasons=rejection_reasons,
            conditions_passed=conditions_passed,
            conditions_failed=conditions_failed,
            bars=bars,
            regime=regime,
            evaluated_conditions=evaluated_conditions,
        )
        validation_errors = self.validate_rejected_diagnostics(canonical)
        if validation_errors:
            self.logger.warning(
                "Rejected candidate diagnostics validation failed ticker=%s errors=%s",
                ticker,
                "; ".join(validation_errors),
            )
        if settings is not None and getattr(settings, "enable_gate_audit_logs", False):
            self.log_gate_audit(ticker, final_score, computed_grade, canonical)
        setup_grade = self.score_based_grade(final_score, settings, computed_grade)
        eligibility = self.rejected_classification_payload(
            raw_score=final_score,
            setup_grade=setup_grade,
            displayed_grade=computed_grade,
            canonical=canonical,
            first_rejection_gate=first_rejection_gate,
            rejection_reasons=rejection_reasons,
            risk_decision=risk_decision,
        )
        quote = self.quote_forensics_payload(
            snapshot=snapshot,
            settings=settings,
            latest=bars.iloc[-1] if bars is not None and not bars.empty else None,
            liquidity_score=getattr(risk_decision, "liquidity_score", None),
            liquidity_score_status=getattr(risk_decision, "liquidity_status", None),
            force=risk_engine_reached,
        )
        self.db.insert_dict(
            "rejected_candidate_diagnostics",
            {
                "ticker": ticker,
                "final_score": final_score,
                "computed_grade": computed_grade,
                "first_rejection_gate": canonical["actual_first_blocking_gate"],
                "rejection_reasons_json": canonical["legacy_rejection_reasons"],
                "conditions_passed_json": canonical["legacy_passed_conditions"],
                "conditions_failed_json": canonical["legacy_failed_conditions"],
                "diagnostics_format_version": 2,
                "evaluated_conditions_json": canonical["evaluated_conditions"],
                "passed_conditions_v2_json": canonical["passed_conditions"],
                "failed_conditions_v2_json": canonical["failed_conditions"],
                "blocking_conditions_json": canonical["blocking_conditions"],
                "actual_first_blocking_gate": canonical["actual_first_blocking_gate"],
                "why_not_trade": "; ".join(canonical["legacy_rejection_reasons"]) or "candidate was not accepted",
                **entry_quality,
                **eligibility,
                **quote,
                "raw_metrics_json": {
                    **metrics,
                    **entry_quality,
                    "regime": regime.regime if regime else None,
                    "spy_trend": regime.spy_trend if regime else None,
                    "qqq_trend": regime.qqq_trend if regime else None,
                    "breadth_score": regime.breadth_score if regime else None,
                    **self.raw_quote_metrics(quote),
                },
            },
        )

    def canonical_rejected_conditions(
        self,
        *,
        final_score: float,
        computed_grade: str,
        first_rejection_gate: Optional[str],
        rejection_reasons: list[str],
        conditions_passed: list[str],
        conditions_failed: list[str],
        bars: Optional[pd.DataFrame],
        regime: Optional[MarketRegime],
        evaluated_conditions: Optional[list[dict[str, Any]]] = None,
    ) -> dict[str, Any]:
        if evaluated_conditions:
            records = [dict(item) for item in evaluated_conditions]
        else:
            records = self.build_condition_records(
                final_score=final_score,
                computed_grade=computed_grade,
                first_rejection_gate=first_rejection_gate,
                rejection_reasons=rejection_reasons,
                conditions_passed=conditions_passed,
                conditions_failed=conditions_failed,
                bars=bars,
                regime=regime,
            )
        passed_keys = {item["condition_key"] for item in records if item.get("result") == "PASS"}
        failed = [item for item in records if item.get("result") == "FAIL"]
        blocking = [item for item in failed if item.get("is_blocking")]
        legacy_rejections = [
            self.failure_display(item)
            for item in blocking
            if item.get("condition_key") not in passed_keys
        ]
        actual_first = blocking[0]["condition_key"] if blocking else None
        return {
            "evaluated_conditions": records,
            "passed_conditions": [item for item in records if item.get("result") == "PASS"],
            "failed_conditions": failed,
            "blocking_conditions": blocking,
            "actual_first_blocking_gate": actual_first,
            "legacy_rejection_reasons": list(dict.fromkeys(legacy_rejections)),
            "legacy_passed_conditions": [item["display_name"] for item in records if item.get("result") == "PASS"],
            "legacy_failed_conditions": [self.failure_display(item) for item in failed],
        }

    def build_condition_records(
        self,
        *,
        final_score: float,
        computed_grade: str,
        first_rejection_gate: Optional[str],
        rejection_reasons: list[str],
        conditions_passed: list[str],
        conditions_failed: list[str],
        bars: Optional[pd.DataFrame],
        regime: Optional[MarketRegime],
    ) -> list[dict[str, Any]]:
        latest = bars.iloc[-1] if bars is not None and not bars.empty else {}
        values = self.condition_values(latest, regime)
        records: list[ConditionRecord] = []
        seen: set[tuple[str, str]] = set()

        def append(label: str, result: str, blocking: bool = False, reason_code: Optional[str] = None) -> None:
            condition = self.condition_from_label(label, values)
            key = condition.condition_key
            identity = (key, result)
            if identity in seen:
                return
            seen.add(identity)
            records.append(
                ConditionRecord(
                    condition_key=key,
                    display_name=condition.display_name,
                    lhs_value=condition.lhs_value,
                    rhs_value=condition.rhs_value,
                    operator=condition.operator,
                    result=result,
                    is_blocking=blocking,
                    evaluation_order=len(records) + 1,
                    reason_code=reason_code,
                )
            )

        for label in conditions_passed:
            append(label, "PASS", blocking=False)

        passed_keys = {self.condition_from_label(label, values).condition_key for label in conditions_passed}
        if computed_grade in {"B Watch Alert", "C Risky/Early Alert"} and "grade below" in (first_rejection_gate or "").lower():
            append("Grade below A", "FAIL", blocking=True, reason_code="grade_below_trade_candidate_threshold")
        for label in [*conditions_failed, *rejection_reasons]:
            condition = self.condition_from_label(label, values)
            if condition.condition_key in passed_keys:
                continue
            append(label, "FAIL", blocking=True, reason_code="blocking_failure")

        if computed_grade in {"B Watch Alert", "C Risky/Early Alert"}:
            append("Grade below A", "FAIL", blocking=True, reason_code="grade_below_trade_candidate_threshold")
        elif not records and first_rejection_gate:
            append(first_rejection_gate, "FAIL", blocking=True, reason_code="first_rejection_gate")
        return [asdict(item) for item in records]

    @staticmethod
    def condition_values(latest: Any, regime: Optional[MarketRegime]) -> dict[str, Any]:
        def value(name: str) -> Any:
            try:
                return float(latest.get(name)) if latest.get(name) is not None else None
            except Exception:
                return None

        close = value("close")
        ema21 = value("ema_21")
        ema50 = value("ema_50")
        ema200 = value("ema_200")
        ema9 = value("ema_9")
        atr = value("atr_14")
        distance_ema21 = abs(close - ema21) / max(atr or 0, (close or 0) * 0.005, 0.01) if close and ema21 else None
        return {
            "close": close,
            "ema9": ema9,
            "ema21": ema21,
            "ema50": ema50,
            "ema200": ema200,
            "relative_volume": value("relative_volume"),
            "return_20": value("return_20"),
            "macd": value("macd"),
            "macd_signal": value("macd_signal"),
            "distance_ema21": distance_ema21,
            "spy_trend": regime.spy_trend if regime else None,
            "qqq_trend": regime.qqq_trend if regime else None,
        }

    @classmethod
    def condition_from_label(cls, label: str, values: dict[str, Any]) -> ConditionRecord:
        normalized = (label or "").strip().lower()
        if "price above ema50" in normalized or "price below ema50" in normalized:
            return ConditionRecord("price_above_ema50", "Price above EMA50", values.get("close"), values.get("ema50"), ">")
        if "ema50 above ema200" in normalized or "ema50 below ema200" in normalized:
            return ConditionRecord("ema50_above_ema200", "EMA50 above EMA200", values.get("ema50"), values.get("ema200"), ">=")
        if "short-term momentum" in normalized:
            return ConditionRecord("short_term_momentum_improving", "Short-term momentum improving", values.get("ema9"), values.get("ema21"), ">")
        if "not too extended from ema21" in normalized or "extended from ema21" in normalized:
            return ConditionRecord("not_too_extended_from_ema21", "Not too extended from EMA21", values.get("distance_ema21"), 2.75, "<=")
        if "positive 20-bar strength" in normalized or "negative 20-bar strength" in normalized:
            return ConditionRecord("positive_20_bar_strength", "Positive 20-bar strength", values.get("return_20"), 0, ">")
        if "macd momentum" in normalized:
            return ConditionRecord("macd_momentum_improving", "MACD momentum improving", values.get("macd"), values.get("macd_signal"), ">")
        if "spy/qqq" in normalized or "spy or qqq" in normalized:
            return ConditionRecord("spy_or_qqq_healthy", "SPY or QQQ healthy", values.get("spy_trend"), values.get("qqq_trend"), "OR")
        if "relative volume" in normalized or "volume" in normalized:
            return ConditionRecord("relative_volume_confirmed", "Relative volume confirmed", values.get("relative_volume"), None, ">=")
        if "grade below" in normalized:
            return ConditionRecord("grade_below_trade_candidate_threshold", "Grade below trade threshold", None, None, "<")
        key = "".join(ch if ch.isalnum() else "_" for ch in normalized).strip("_") or "unknown"
        return ConditionRecord(key, (label or "Unknown").strip())

    @staticmethod
    def failure_display(condition: dict[str, Any]) -> str:
        display = str(condition.get("display_name") or condition.get("condition_key") or "Unknown")
        negative = {
            "Price above EMA50": "Price not above EMA50",
            "EMA50 above EMA200": "EMA50 not above EMA200",
            "Short-term momentum improving": "Short-term momentum not improving",
            "Not too extended from EMA21": "Extended from EMA21",
            "Positive 20-bar strength": "20-bar strength not positive",
            "MACD momentum improving": "MACD momentum not improving",
            "SPY or QQQ healthy": "SPY/QQQ not healthy",
            "Relative volume confirmed": "Relative volume not confirmed",
        }
        return negative.get(display, display)

    def validate_rejected_diagnostics(self, canonical: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        passed_keys = {item["condition_key"] for item in canonical["passed_conditions"]}
        failed_keys = {item["condition_key"] for item in canonical["failed_conditions"]}
        if passed_keys & failed_keys:
            errors.append(f"conditions in both passed and failed: {sorted(passed_keys & failed_keys)}")
        blocking = canonical["blocking_conditions"]
        blocking_keys = {item["condition_key"] for item in blocking}
        if not blocking_keys <= failed_keys:
            errors.append("blocking condition not present in failed conditions")
        for item in blocking:
            if not item.get("result"):
                errors.append(f"blocking condition missing result: {item.get('condition_key')}")
            if item.get("result") == "PASS":
                errors.append(f"blocking condition marked PASS: {item.get('condition_key')}")
        if canonical["actual_first_blocking_gate"] and canonical["actual_first_blocking_gate"] not in blocking_keys:
            errors.append("first blocking gate not present in blocking conditions")
        rejection_keys = {self.condition_from_label(reason, {}).condition_key for reason in canonical["legacy_rejection_reasons"]}
        if rejection_keys & passed_keys:
            errors.append("passed condition appears in rejection reasons")
        return errors

    def log_gate_audit(self, ticker: str, final_score: float, computed_grade: str, canonical: dict[str, Any]) -> None:
        for condition in canonical["evaluated_conditions"][:8]:
            self.logger.info(
                "CANDIDATE_GATE_AUDIT ticker=%s score=%.1f grade=%s condition=%s lhs=%s operator=%s rhs=%s "
                "result=%s is_blocking=%s first_blocking_gate=%s",
                ticker,
                final_score,
                computed_grade,
                condition.get("condition_key"),
                condition.get("lhs_value"),
                condition.get("operator"),
                condition.get("rhs_value"),
                condition.get("result"),
                condition.get("is_blocking"),
                canonical.get("actual_first_blocking_gate"),
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

    def score_based_grade(self, score: float, settings: Optional[Settings], fallback: str) -> str:
        if settings is None:
            return fallback
        min_b = max(float(getattr(settings, "min_score_b", 58)), 58.0)
        if score >= float(settings.min_score_a_plus_plus):
            return "A++ Signal"
        if score >= float(settings.min_score_a_plus):
            return "A+ Signal"
        if score >= float(settings.min_score_a):
            return "A Signal"
        if score >= min_b:
            return "B Watch Alert"
        return "C Risky/Early Alert"

    def classification_payload(
        self,
        *,
        raw_score: float,
        setup_grade: str,
        displayed_grade: str,
        eligibility_status: str,
        eligibility_stage: str,
        block_reason_display: Optional[str],
        final_acceptance_status: str,
    ) -> dict[str, Any]:
        return {
            "raw_score": raw_score,
            "setup_grade": setup_grade,
            "eligibility_status": eligibility_status,
            "eligibility_stage": eligibility_stage,
            "block_reason_code": self.reason_code(block_reason_display),
            "block_reason_display": block_reason_display,
            "final_acceptance_status": final_acceptance_status,
            "displayed_grade_legacy": displayed_grade,
            "classification_format_version": self.CLASSIFICATION_FORMAT_VERSION,
        }

    def rejected_classification_payload(
        self,
        *,
        raw_score: float,
        setup_grade: str,
        displayed_grade: str,
        canonical: dict[str, Any],
        first_rejection_gate: Optional[str],
        rejection_reasons: list[str],
        risk_decision: Any,
    ) -> dict[str, Any]:
        reason = "; ".join(canonical.get("legacy_rejection_reasons") or rejection_reasons) or first_rejection_gate
        gate = canonical.get("actual_first_blocking_gate") or first_rejection_gate or ""
        lowered = " ".join([gate, reason or ""]).lower()
        if risk_decision is not None or any(marker in lowered for marker in ("spread too wide", "liquidity score", "risk/reward", "risk engine")):
            status = "BLOCKED_BY_RISK"
            stage = "risk_engine"
        elif "spy/qqq" in lowered or "market" in lowered or "regime" in lowered:
            status = "BLOCKED_BY_POLICY"
            stage = "market_policy"
        elif "grade below" in lowered or setup_grade in {"B Watch Alert", "C Risky/Early Alert"}:
            status = "REJECTED_BY_SCORE"
            stage = "score"
        elif "alert" in lowered or "cooldown" in lowered or "maximum" in lowered:
            status = "BLOCKED_BY_ALERT_POLICY"
            stage = "alert_policy"
        elif reason:
            status = "BLOCKED_BY_POLICY"
            stage = "strategy_policy"
        else:
            status = "UNKNOWN"
            stage = "unknown"
        return self.classification_payload(
            raw_score=raw_score,
            setup_grade=setup_grade,
            displayed_grade=displayed_grade,
            eligibility_status=status,
            eligibility_stage=stage,
            block_reason_display=reason,
            final_acceptance_status="REJECTED",
        )

    @staticmethod
    def reason_code(reason: Optional[str]) -> Optional[str]:
        if not reason:
            return None
        normalized = reason.lower()
        mappings = (
            ("spy/qqq", "SPY_QQQ_UNHEALTHY"),
            ("spread too wide", "SPREAD_TOO_WIDE"),
            ("liquidity score too low", "LOW_LIQUIDITY_SCORE"),
            ("risk/reward", "RISK_REWARD_TOO_LOW"),
            ("grade below", "GRADE_BELOW_TRADE_THRESHOLD"),
            ("cooldown", "COOLDOWN_ACTIVE"),
            ("maximum", "DAILY_OR_TICKER_LIMIT"),
            ("market regime", "MARKET_REGIME_UNSAFE"),
        )
        for marker, code in mappings:
            if marker in normalized:
                return code
        return "".join(ch if ch.isalnum() else "_" for ch in normalized.upper()).strip("_")[:80] or "UNKNOWN"

    def quote_forensics_payload(
        self,
        *,
        snapshot: Any,
        settings: Optional[Settings],
        latest: Any,
        liquidity_score: Optional[float],
        liquidity_score_status: Optional[str],
        force: bool,
    ) -> dict[str, Any]:
        if snapshot is None:
            return {}
        bid = getattr(snapshot, "bid", None)
        ask = getattr(snapshot, "ask", None)
        spread = spread_pct(bid, ask)
        should_store = force
        if spread != float("inf") and spread > 2:
            should_store = True
        if liquidity_score is not None and settings is not None and liquidity_score < settings.min_liquidity_score:
            should_store = True
        if not should_store:
            return {}
        evaluation = datetime.now(timezone.utc)
        quote_timestamp = self.normalize_datetime(getattr(snapshot, "quote_timestamp", None) or getattr(snapshot, "timestamp", None))
        quote_age = None
        if quote_timestamp is not None:
            quote_age = max((evaluation - quote_timestamp).total_seconds(), 0)
        midpoint = ((bid + ask) / 2) if bid is not None and ask is not None and bid > 0 and ask > 0 else None
        validity, validity_reasons = self.quote_validity(bid, ask, quote_age)
        session_state = self.market_session_state(getattr(snapshot, "timestamp", evaluation))
        relative_volume = self.safe_float(latest.get("relative_volume")) if latest is not None and hasattr(latest, "get") else None
        return {
            "raw_bid": bid,
            "raw_ask": ask,
            "bid_size": getattr(snapshot, "bid_size", None),
            "ask_size": getattr(snapshot, "ask_size", None),
            "last_trade_price": getattr(snapshot, "price", None),
            "midpoint": midpoint,
            "quote_timestamp": quote_timestamp.isoformat() if quote_timestamp is not None else None,
            "evaluation_timestamp": evaluation.isoformat(),
            "quote_age_seconds": quote_age,
            "quote_source": getattr(snapshot, "data_source", None),
            "feed_name": getattr(snapshot, "feed_name", None),
            "feed_type": getattr(snapshot, "feed_type", None),
            "nbbo_flag": 0,
            "feed_native_flag": int(getattr(snapshot, "feed_type", "") == "feed_native"),
            "spread_absolute": (ask - bid) if bid is not None and ask is not None else None,
            "spread_percentage": None if spread == float("inf") else spread,
            "spread_formula_version": self.SPREAD_FORMULA_VERSION,
            "liquidity_score_at_evaluation": liquidity_score,
            "liquidity_score_status": liquidity_score_status or ("MEASURED" if liquidity_score is not None else "UNAVAILABLE"),
            "raw_volume": getattr(snapshot, "volume", None),
            "quote_relative_volume": relative_volume,
            "market_session_state": session_state,
            "market_status": session_state,
            "retry_used": None,
            "stale_quote_flag": int("STALE" in validity_reasons),
            "missing_bid_flag": int(bid is None),
            "missing_ask_flag": int(ask is None),
            "nonpositive_bid_flag": int(bid is not None and bid <= 0),
            "nonpositive_ask_flag": int(ask is not None and ask <= 0),
            "crossed_market_flag": int(bid is not None and ask is not None and ask < bid),
            "raw_quote_payload_version": "quote_forensics_v1",
            "forensics_format_version": self.FORENSICS_FORMAT_VERSION,
            "quote_validity_status": validity,
            "quote_validity_reasons": json.dumps(validity_reasons),
        }

    @staticmethod
    def quote_validity(bid: Any, ask: Any, quote_age_seconds: Optional[float]) -> tuple[str, list[str]]:
        reasons: list[str] = []
        if bid is None:
            reasons.append("MISSING_BID")
        if ask is None:
            reasons.append("MISSING_ASK")
        if bid is not None and bid <= 0:
            reasons.append("NONPOSITIVE_BID")
        if ask is not None and ask <= 0:
            reasons.append("NONPOSITIVE_ASK")
        if bid is not None and ask is not None and ask < bid:
            reasons.append("CROSSED_MARKET")
        if quote_age_seconds is None:
            reasons.append("UNKNOWN")
        elif quote_age_seconds > 900:
            reasons.append("STALE")
        if not reasons:
            return "VALID", []
        priority = ("MISSING_BID", "MISSING_ASK", "NONPOSITIVE_BID", "NONPOSITIVE_ASK", "CROSSED_MARKET", "STALE", "UNKNOWN")
        for item in priority:
            if item in reasons:
                return item, reasons
        return "UNKNOWN", reasons

    @staticmethod
    def market_session_state(timestamp: Any) -> str:
        current = SignalDiagnosticsRecorder.normalize_datetime(timestamp) or datetime.now(timezone.utc)
        eastern = current.astimezone(EASTERN)
        minutes = eastern.hour * 60 + eastern.minute
        is_open = eastern.weekday() < 5 and (9 * 60 + 30) <= minutes <= (16 * 60)
        return "OPEN" if is_open else "CLOSED"

    @staticmethod
    def normalize_datetime(value: Any) -> Optional[datetime]:
        if value is None:
            return None
        if isinstance(value, datetime):
            dt = value
        else:
            try:
                dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            except ValueError:
                return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    @staticmethod
    def safe_float(value: Any) -> Optional[float]:
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def raw_quote_metrics(quote: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in quote.items() if value is not None}

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
