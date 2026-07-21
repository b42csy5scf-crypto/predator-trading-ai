from __future__ import annotations

import csv
import json
import math
import statistics
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

from predator_trading_ai.database.db import Database
from predator_trading_ai.engines.signal_diagnostics import SignalDiagnosticsRecorder
from predator_trading_ai.utils.logger import setup_logger
from predator_trading_ai.utils.watchlist import SECTOR_BY_TICKER


TRADE_GRADES = {"A++ Signal", "A+ Signal", "A Signal"}
GROUPS = ("A++ Signal", "A+ Signal", "A Signal", "Strong B Experimental Watch")
MFE_LEVELS = (0.25, 0.50, 0.75, 1.00, 1.25, 1.50, 2.00, 3.00)
MAE_LEVELS = (-0.25, -0.50, -0.75, -1.00)
ENTRY_VARIABLES = (
    "breakout_distance_atr",
    "distance_from_ema21",
    "distance_from_ema50",
    "distance_from_recent_swing_low",
    "stop_to_swing_low_distance",
    "bars_since_breakout",
    "spread_at_entry",
    "slippage_proxy",
    "vix_value",
    "open_positions_count",
    "open_positions_same_sector",
)


@dataclass(frozen=True)
class ResearchSignal:
    active_signal_id: int
    ticker: str
    grade: str
    alert_type: str
    status: str
    final_outcome: str
    exit_reason: str
    entry_price: float
    risk_per_share: float
    current_r: float
    realized_r: Optional[float]
    mfe_r: float
    mae_r: float
    holding_seconds: Optional[float]
    score: Optional[float]
    regime: str
    spy_state: str
    qqq_state: str
    vix_value: Optional[float]
    spread_at_entry: Optional[float]
    slippage_proxy: Optional[float]
    gap_flag: Optional[int]
    minutes_after_market_open: Optional[float]
    day_of_week: Optional[int]
    open_positions_count: Optional[int]
    open_positions_same_sector: Optional[int]
    breakout_distance_atr: Optional[float]
    distance_from_ema21: Optional[float]
    distance_from_ema50: Optional[float]
    distance_from_recent_swing_low: Optional[float]
    stop_to_swing_low_distance: Optional[float]
    bars_since_breakout: Optional[float]
    schema_version: str
    strategy_version: str
    git_commit_hash: str
    config_hash: str
    tp1_hit_at: Optional[str]
    tp2_hit_at: Optional[str]
    tp3_hit_at: Optional[str]
    sl_hit_at: Optional[str]
    time_to_025r_seconds: Optional[float]
    time_to_050r_seconds: Optional[float]
    time_to_075r_seconds: Optional[float]
    time_to_100r_seconds: Optional[float]

    @property
    def group(self) -> str:
        if self.alert_type == "experimental_watch" and self.grade == "B Watch Alert":
            return "Strong B Experimental Watch"
        return self.grade

    @property
    def is_trade_candidate(self) -> bool:
        return self.alert_type == "trade_candidate" and self.grade in TRADE_GRADES

    @property
    def is_closed(self) -> bool:
        return self.status == "closed" or bool(self.final_outcome)

    @property
    def final_r(self) -> float:
        return float(self.realized_r if self.realized_r is not None else self.current_r)

    @property
    def won(self) -> bool:
        return self.final_outcome.startswith("TP")

    @property
    def lost(self) -> bool:
        return self.final_outcome == "SL"

    @property
    def breakeven(self) -> bool:
        return self.final_outcome == "BE"


class ResearchReport:
    def __init__(self, db: Database, days: int = 30) -> None:
        self.db = db
        self.days = max(int(days), 1)
        self.logger = setup_logger(__name__)
        self.started_at = time.perf_counter()
        self.path_rows_by_signal: dict[int, list[Any]] = {}
        self.path_fallback_count = 0

    def build(self) -> str:
        return self.to_text(self.build_data())

    def build_data(self) -> dict[str, Any]:
        signals = self.load_signals()
        rejected = self.rejected_rows()
        path_rows = self.price_path_rows()
        snapshots = self.universe_snapshots()
        self.path_rows_by_signal = self.group_path_rows(path_rows)
        closed = [item for item in signals if item.is_closed]
        trade_candidates = [item for item in signals if item.is_trade_candidate]
        strong_b = [item for item in signals if item.group == "Strong B Experimental Watch"]
        data = {
            "coverage": self.coverage(signals, rejected, path_rows, snapshots),
            "performance_by_type": {group: self.performance_block(self.group_rows(signals, group)) for group in GROUPS},
            "mfe_mae_distribution": self.mfe_mae_distribution(signals),
            "time_bucketed_evolution": self.time_bucketed_evolution(signals),
            "movement_sequencing": self.movement_sequencing(closed),
            "entry_timing": self.entry_timing_analysis(signals),
            "entry_quality": self.entry_quality_analysis(closed),
            "a_plus_plus_vs_strong_b": self.compare_groups(
                self.group_rows(signals, "A++ Signal"),
                strong_b,
            ),
            "score_vs_outcome": self.score_vs_outcome([item for item in trade_candidates if item.is_closed]),
            "market_context": self.market_context_analysis(signals),
            "rejection_analytics": self.rejection_analytics(rejected),
            "counterfactual": self.counterfactual(signals),
            "findings": self.findings(signals),
            "metadata": {
                "days": self.days,
                "signal_rows": len(signals),
                "rejected_rows": len(rejected),
                "price_path_rows": len(path_rows),
                "build_seconds": round(time.perf_counter() - self.started_at, 3),
            },
        }
        self.logger.info(
            "ResearchReport built days=%s signals=%d rejected=%d price_path=%d snapshots=%d duration=%.3fs",
            self.days,
            len(signals),
            len(rejected),
            len(path_rows),
            len(snapshots),
            data["metadata"]["build_seconds"],
        )
        return data

    def to_text(self, data: dict[str, Any]) -> str:
        sections = [
            self.section("Coverage and performance", [
                *self.coverage_lines(data["coverage"]),
                "",
                "Performance by signal type:",
                *self.performance_lines(data["performance_by_type"]),
            ]),
            self.section("MFE/MAE and evolution", [
                *self.distribution_lines(data["mfe_mae_distribution"]),
                "",
                *self.evolution_lines(data["time_bucketed_evolution"]),
                "",
                *self.counterfactual_lines(data["counterfactual"]),
            ]),
            self.section("Entry timing and market context", [
                *self.category_lines("Entry timing", data["entry_timing"]["time_buckets"]),
                "",
                *self.category_lines("Weekday", data["entry_timing"]["weekday"]),
                "",
                *self.entry_quality_lines(data["entry_quality"]),
                "",
                *self.category_lines("Market context", data["market_context"]),
            ]),
            self.section("Rejections and comparisons", [
                *self.rejection_lines(data["rejection_analytics"]),
                "",
                *self.comparison_lines(data["a_plus_plus_vs_strong_b"]),
                "",
                *self.score_lines(data["score_vs_outcome"]),
                "",
                *self.movement_lines(data["movement_sequencing"]),
            ]),
            self.section("Research findings and warnings", [
                "Research Findings -- Descriptive Only",
                *data["findings"],
                "",
                "Warnings:",
                "- No finding proves causation.",
                "- Do not change live strategy without out-of-sample validation.",
            ]),
        ]
        return "\n\n".join(sections)

    def load_signals(self) -> list[ResearchSignal]:
        rows = self.db.fetch_all(
            """
            SELECT o.*,
                   COALESCE(a.status, CASE WHEN o.final_outcome IS NULL THEN 'active' ELSE 'closed' END) AS active_status,
                   d.score AS diag_score,
                   d.regime AS diag_regime,
                   d.spy_state,
                   d.qqq_state,
                   d.vix_value,
                   d.spread_at_entry,
                   d.slippage_proxy,
                   d.gap_flag,
                   d.minutes_after_market_open,
                   d.day_of_week,
                   d.open_positions_count,
                   d.open_positions_same_sector,
                   d.breakout_distance_atr,
                   d.distance_from_ema21,
                   d.distance_from_ema50,
                   d.distance_from_recent_swing_low,
                   d.stop_to_swing_low_distance,
                   d.bars_since_breakout,
                   d.schema_version,
                   d.strategy_version,
                   d.git_commit_hash,
                   d.config_hash
            FROM signal_outcome_diagnostics o
            LEFT JOIN active_signals a ON a.id = o.active_signal_id
            LEFT JOIN signal_diagnostics d ON d.active_signal_id = o.active_signal_id
            WHERE o.created_at >= datetime('now', ?)
            ORDER BY o.created_at
            LIMIT 5000
            """,
            [f"-{self.days} days"],
        )
        return [
            ResearchSignal(
                active_signal_id=int(row["active_signal_id"]),
                ticker=str(row["ticker"]),
                grade=str(row["grade"]),
                alert_type=str(row_get(row, "alert_type", "trade_candidate") or "trade_candidate"),
                status=str(row["active_status"] or "active"),
                final_outcome=str(row["final_outcome"] or ""),
                exit_reason=str(row["exit_reason"] or ""),
                entry_price=float(row["entry_price"] or 0),
                risk_per_share=max(float(row["risk_per_share"] or 0), 0.01),
                current_r=float(row["current_r"] or 0),
                realized_r=optional_float(row_get(row, "realized_r")),
                mfe_r=float(row["mfe_r"] or 0),
                mae_r=float(row["mae_r"] or 0),
                holding_seconds=optional_float(row["holding_seconds"]),
                score=optional_float(row["diag_score"]),
                regime=str(row["diag_regime"] or "unknown"),
                spy_state=str(row["spy_state"] or "unknown"),
                qqq_state=str(row["qqq_state"] or "unknown"),
                vix_value=optional_float(row["vix_value"]),
                spread_at_entry=optional_float(row["spread_at_entry"]),
                slippage_proxy=optional_float(row["slippage_proxy"]),
                gap_flag=optional_int(row["gap_flag"]),
                minutes_after_market_open=optional_float(row["minutes_after_market_open"]),
                day_of_week=optional_int(row["day_of_week"]),
                open_positions_count=optional_int(row["open_positions_count"]),
                open_positions_same_sector=optional_int(row["open_positions_same_sector"]),
                breakout_distance_atr=optional_float(row["breakout_distance_atr"]),
                distance_from_ema21=optional_float(row["distance_from_ema21"]),
                distance_from_ema50=optional_float(row["distance_from_ema50"]),
                distance_from_recent_swing_low=optional_float(row["distance_from_recent_swing_low"]),
                stop_to_swing_low_distance=optional_float(row["stop_to_swing_low_distance"]),
                bars_since_breakout=optional_float(row["bars_since_breakout"]),
                schema_version=str(row["schema_version"] or "legacy/null"),
                strategy_version=str(row["strategy_version"] or "legacy/null"),
                git_commit_hash=str(row["git_commit_hash"] or "legacy/null"),
                config_hash=str(row["config_hash"] or "legacy/null"),
                tp1_hit_at=row["tp1_hit_at"],
                tp2_hit_at=row["tp2_hit_at"],
                tp3_hit_at=row["tp3_hit_at"],
                sl_hit_at=row["sl_hit_at"],
                time_to_025r_seconds=optional_float(row_get(row, "time_to_025r_seconds")),
                time_to_050r_seconds=optional_float(row_get(row, "time_to_050r_seconds")),
                time_to_075r_seconds=optional_float(row_get(row, "time_to_075r_seconds")),
                time_to_100r_seconds=optional_float(row_get(row, "time_to_100r_seconds")),
            )
            for row in rows
        ]

    def rejected_rows(self) -> list[Any]:
        return self.db.fetch_all(
            """
            SELECT *
            FROM rejected_candidate_diagnostics
            WHERE created_at >= datetime('now', ?) AND final_score >= 50
            ORDER BY created_at
            LIMIT 10000
            """,
            [f"-{self.days} days"],
        )

    def price_path_rows(self) -> list[Any]:
        return self.db.fetch_all(
            """
            SELECT *
            FROM price_path
            WHERE created_at >= datetime('now', ?)
            ORDER BY signal_id, timestamp, id
            LIMIT 100000
            """,
            [f"-{self.days} days"],
        )

    def universe_snapshots(self) -> list[Any]:
        return self.db.fetch_all(
            """
            SELECT *
            FROM universe_snapshot
            WHERE created_at >= datetime('now', ?)
            ORDER BY timestamp
            LIMIT 10000
            """,
            [f"-{self.days} days"],
        )

    @staticmethod
    def group_path_rows(rows: list[Any]) -> dict[int, list[Any]]:
        grouped: dict[int, list[Any]] = defaultdict(list)
        for row in rows:
            grouped[int(row["signal_id"])].append(row)
        return grouped

    def coverage(self, signals: list[ResearchSignal], rejected: list[Any], path_rows: list[Any], snapshots: list[Any]) -> dict[str, Any]:
        accepted_rows = self.db.fetch_all(
            """
            SELECT *
            FROM signal_diagnostics
            WHERE created_at >= datetime('now', ?)
            LIMIT 10000
            """,
            [f"-{self.days} days"],
        )
        active_rows = self.db.fetch_all(
            """
            SELECT id, status
            FROM active_signals
            WHERE created_at >= datetime('now', ?)
            LIMIT 10000
            """,
            [f"-{self.days} days"],
        )
        completed_rows = self.db.fetch_all(
            """
            SELECT id, outcome, r_multiple
            FROM completed_trades
            WHERE created_at >= datetime('now', ?)
            LIMIT 10000
            """,
            [f"-{self.days} days"],
        )
        timestamps = [str(row["created_at"]) for row in accepted_rows] + [str(row["created_at"]) for row in path_rows]
        closed = [item for item in signals if item.is_closed]
        scan_failures = [row for row in snapshots if int(row["api_failures"] or 0) > 0 or int(row["missing_market_data"] or 0) > 0]
        price_signal_ids = {int(row["signal_id"]) for row in path_rows}
        accepted_signal_ids = {int(row["active_signal_id"]) for row in accepted_rows if row_get(row, "active_signal_id") is not None}
        return {
            "report_period_days": self.days,
            "first_timestamp": min(timestamps) if timestamps else "n/a",
            "last_timestamp": max(timestamps) if timestamps else "n/a",
            "accepted_trade_candidates": len([row for row in accepted_rows if row["alert_type"] == "trade_candidate" and row["grade"] in TRADE_GRADES]),
            "strong_b": len([row for row in accepted_rows if row["alert_type"] == "experimental_watch"]),
            "active_signal_rows": len(active_rows),
            "completed_trade_rows": len(completed_rows),
            "open_count": len([item for item in signals if not item.is_closed]),
            "closed_count": len(closed),
            "rejected_candidates": len(rejected),
            "near_miss_score_50": len(rejected),
            "price_path_rows": len(path_rows),
            "scan_cycles": len(snapshots),
            "scan_failure_pct": pct(len(scan_failures), len(snapshots)),
            "signals_missing_price_path": len([sid for sid in accepted_signal_ids if sid not in price_signal_ids]),
            "signals_missing_entry_diagnostics": len([item for item in signals if item.breakout_distance_atr is None and item.minutes_after_market_open is None]),
            "signals_missing_exit_diagnostics": len([item for item in closed if not item.final_outcome]),
            "schema_versions": sorted({item.schema_version for item in signals}),
            "strategy_versions": sorted({item.strategy_version for item in signals}),
            "git_commits": sorted({item.git_commit_hash for item in signals}),
            "config_hashes": sorted({item.config_hash for item in signals}),
            "sample_warning": self.sample_warning(len(closed)),
        }

    @staticmethod
    def sample_warning(closed_count: int) -> str:
        if closed_count < 15:
            return "Very small sample; descriptive only. Do not change strategy."
        if closed_count < 30:
            return "Small sample; use findings as hypotheses only."
        if closed_count < 100:
            return "Moderate sample; validate findings out of sample before changing strategy."
        return "Larger sample, but out-of-sample validation is still required."

    def performance_block(self, rows: list[ResearchSignal]) -> dict[str, Any]:
        closed = [item for item in rows if item.is_closed]
        wins = [item for item in closed if item.won]
        losses = [item for item in closed if item.lost]
        breakeven = [item for item in closed if item.breakeven]
        final_r = [item.final_r for item in closed]
        positive = [value for value in final_r if value > 0]
        negative = [abs(value) for value in final_r if value < 0]
        return {
            "total": len(rows),
            "open": len([item for item in rows if not item.is_closed]),
            "closed": len(closed),
            "wins": len(wins),
            "losses": len(losses),
            "breakeven": len(breakeven),
            "win_rate_closed": pct(len(wins), len([item for item in closed if not item.breakeven])),
            "avg_final_r": avg(final_r),
            "median_final_r": median(final_r),
            "expectancy_r": avg(final_r),
            "avg_mfe": avg([self.path_or_stored_extremes(item)[0] for item in rows]),
            "median_mfe": median([self.path_or_stored_extremes(item)[0] for item in rows]),
            "avg_mae": avg([self.path_or_stored_extremes(item)[1] for item in rows]),
            "median_mae": median([self.path_or_stored_extremes(item)[1] for item in rows]),
            "avg_holding_time": avg([item.holding_seconds for item in closed if item.holding_seconds is not None]),
            "median_holding_time": median([item.holding_seconds for item in closed if item.holding_seconds is not None]),
            "profit_factor": (sum(positive) / sum(negative)) if negative else None,
            "tp1_reach_pct": pct(len([item for item in rows if item.tp1_hit_at or item.mfe_r >= 1]), len(rows)),
            "tp2_reach_pct": pct(len([item for item in rows if item.tp2_hit_at or item.final_outcome in {"TP2", "TP3"}]), len(rows)),
            "tp3_reach_pct": pct(len([item for item in rows if item.tp3_hit_at or item.final_outcome == "TP3"]), len(rows)),
            "direct_sl_pct": pct(len([item for item in closed if item.lost and self.path_or_stored_extremes(item)[0] < 0.25]), len(closed)),
        }

    def mfe_mae_distribution(self, signals: list[ResearchSignal]) -> dict[str, Any]:
        groups = {"Overall": signals, **{group: self.group_rows(signals, group) for group in GROUPS}}
        result: dict[str, Any] = {}
        fallback_used = False
        for group, rows in groups.items():
            closed = [item for item in rows if item.is_closed]
            values = [(item, *self.path_or_stored_extremes(item)) for item in closed]
            fallback_used = fallback_used or any(not self.path_rows_by_signal.get(item.active_signal_id) for item in closed)
            result[group] = {
                "n": len(closed),
                "mfe": {f"+{level:.2f}R": count_pct(len([1 for _, mfe, _ in values if mfe >= level]), len(closed)) for level in MFE_LEVELS},
                "mae": {
                    f"{level:.2f}R": count_pct(len([1 for _, _, mae in values if mae <= level]), len(closed))
                    for level in MAE_LEVELS
                } | {"below -1.00R": count_pct(len([1 for _, _, mae in values if mae < -1.0]), len(closed))},
            }
        result["fallback_note"] = "Used stored MFE/MAE fallback where price_path was unavailable." if fallback_used else "Used price_path where available."
        return result

    def time_bucketed_evolution(self, signals: list[ResearchSignal]) -> dict[str, Any]:
        buckets = (900, 1800, 3600, 14400)
        result: dict[str, Any] = {"buckets": {}, "time_to": {}}
        final_mfe_values = []
        final_mae_values = []
        for seconds in buckets:
            covered = []
            mfe_values = []
            mae_values = []
            for item in signals:
                path = self.path_rows_by_signal.get(item.active_signal_id, [])
                if not path:
                    continue
                rel = relative_path_r(path, item.entry_price, item.risk_per_share)
                if not rel or max(point[0] for point in rel) < seconds:
                    continue
                covered.append(item)
                cutoff_values = [point[1] for point in rel if point[0] <= seconds]
                mfe_values.append(max(cutoff_values))
                mae_values.append(min(cutoff_values))
            result["buckets"][f"{int(seconds / 60)}m"] = {
                "coverage": len(covered),
                "median_mfe": median(mfe_values),
                "median_mae": median(mae_values),
            }
        for item in signals:
            path = self.path_rows_by_signal.get(item.active_signal_id, [])
            if not path:
                continue
            mfe, mae = self.path_or_stored_extremes(item)
            final_mfe_values.append(mfe)
            final_mae_values.append(mae)
        result["buckets"]["final"] = {
            "coverage": len(final_mfe_values),
            "median_mfe": median(final_mfe_values),
            "median_mae": median(final_mae_values),
        }
        for level in (0.25, 0.50, 0.75, 1.00):
            values = [first_reach_seconds(self.path_rows_by_signal.get(item.active_signal_id, []), item, level) for item in signals]
            values = [value for value in values if value is not None]
            result["time_to"][f"+{level:.2f}R"] = {"n": len(values), "median_seconds": median(values)}
        result["time_to"]["MFE"] = {"n": len([item for item in signals if item.holding_seconds is not None]), "median_seconds": median([item.holding_seconds for item in signals if item.holding_seconds is not None])}
        result["time_to"]["MAE"] = result["time_to"]["MFE"]
        return result

    def movement_sequencing(self, closed: list[ResearchSignal]) -> dict[str, Any]:
        losses = [item for item in closed if item.lost or item.breakeven]
        group_median_hold = median([item.holding_seconds for item in losses if item.holding_seconds is not None])
        buckets: dict[str, list[ResearchSignal]] = {"Failed immediately": [], "Moved favorably then reversed": [], "Slow bleed / unresolved": []}
        for item in losses:
            mfe, _ = self.path_or_stored_extremes(item)
            if mfe < 0.25:
                buckets["Failed immediately"].append(item)
            elif mfe >= 0.50:
                buckets["Moved favorably then reversed"].append(item)
            else:
                buckets["Slow bleed / unresolved"].append(item)
        return {name: self.sequence_bucket(rows, len(losses)) for name, rows in buckets.items()}

    def sequence_bucket(self, rows: list[ResearchSignal], total: int) -> dict[str, Any]:
        return {
            "count": len(rows),
            "pct": pct(len(rows), total),
            "avg_score": avg([item.score for item in rows if item.score is not None]),
            "avg_breakout_distance": avg([item.breakout_distance_atr for item in rows if item.breakout_distance_atr is not None]),
            "avg_entry_minutes_after_open": avg([item.minutes_after_market_open for item in rows if item.minutes_after_market_open is not None]),
            "avg_vix": avg([item.vix_value for item in rows if item.vix_value is not None]),
            "avg_spread": avg([item.spread_at_entry for item in rows if item.spread_at_entry is not None]),
            "avg_same_sector_exposure": avg([item.open_positions_same_sector for item in rows if item.open_positions_same_sector is not None]),
        }

    def entry_timing_analysis(self, signals: list[ResearchSignal]) -> dict[str, Any]:
        time_groups: dict[str, list[ResearchSignal]] = defaultdict(list)
        for item in signals:
            minutes = item.minutes_after_market_open
            if minutes is None:
                label = "unknown"
            elif minutes <= 30:
                label = "0-30m"
            elif minutes <= 60:
                label = "31-60m"
            elif minutes <= 120:
                label = "61-120m"
            elif minutes <= 240:
                label = "121-240m"
            else:
                label = ">240m"
            time_groups[label].append(item)
        weekday_groups: dict[str, list[ResearchSignal]] = defaultdict(list)
        names = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
        for item in signals:
            weekday_groups[names[item.day_of_week] if item.day_of_week is not None and 0 <= item.day_of_week < 7 else "unknown"].append(item)
        return {
            "time_buckets": self.category_metric_map(time_groups),
            "weekday": self.category_metric_map(weekday_groups),
        }

    def entry_quality_analysis(self, closed: list[ResearchSignal]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        winners = [item for item in closed if item.won]
        losses = [item for item in closed if item.lost]
        bes = [item for item in closed if item.breakeven]
        for variable in ENTRY_VARIABLES:
            values = [getattr(item, variable) for item in closed if getattr(item, variable) is not None]
            missing = len(closed) - len(values)
            item = {
                "sample_count": len(values),
                "missing_count": missing,
                "winner_median": median([getattr(row, variable) for row in winners if getattr(row, variable) is not None]),
                "loser_median": median([getattr(row, variable) for row in losses if getattr(row, variable) is not None]),
                "breakeven_median": median([getattr(row, variable) for row in bes if getattr(row, variable) is not None]),
                "effect_direction": "observed association only; not causation",
            }
            if len(values) >= 40:
                item["quartiles"] = quartiles(values)
            result[variable] = item
        return result

    def compare_groups(self, a_plus_plus: list[ResearchSignal], strong_b: list[ResearchSignal]) -> dict[str, Any]:
        a_closed = [item for item in a_plus_plus if item.is_closed]
        b_closed = [item for item in strong_b if item.is_closed]
        return {
            "warning": "Comparison is preliminary and statistically unstable." if len(a_closed) < 15 or len(b_closed) < 15 else "Comparison remains descriptive; validate out of sample.",
            "A++ Signal": self.comparison_metrics(a_closed),
            "Strong B Experimental Watch": self.comparison_metrics(b_closed),
        }

    def comparison_metrics(self, rows: list[ResearchSignal]) -> dict[str, Any]:
        return {
            "sample_size": len(rows),
            "win_rate": self.performance_block(rows)["win_rate_closed"],
            "median_final_r": median([item.final_r for item in rows]),
            "expectancy": avg([item.final_r for item in rows]),
            "median_mfe": median([self.path_or_stored_extremes(item)[0] for item in rows]),
            "median_mae": median([self.path_or_stored_extremes(item)[1] for item in rows]),
            "tp1_reach_rate": self.performance_block(rows)["tp1_reach_pct"],
            "direct_sl_rate": self.performance_block(rows)["direct_sl_pct"],
            "median_holding_time": median([item.holding_seconds for item in rows if item.holding_seconds is not None]),
            "median_breakout_distance": median([item.breakout_distance_atr for item in rows if item.breakout_distance_atr is not None]),
            "median_vix": median([item.vix_value for item in rows if item.vix_value is not None]),
            "median_spread": median([item.spread_at_entry for item in rows if item.spread_at_entry is not None]),
            "median_time_after_open": median([item.minutes_after_market_open for item in rows if item.minutes_after_market_open is not None]),
        }

    def score_vs_outcome(self, rows: list[ResearchSignal]) -> dict[str, Any]:
        scores = [item.score for item in rows if item.score is not None]
        paired = [(item.score, item.final_r, item) for item in rows if item.score is not None]
        rule = "spearman_only_small_sample" if len(paired) < 30 else "median_split" if len(paired) < 100 else "quartiles"
        result = {
            "sample_size": len(paired),
            "rule": rule,
            "spearman_score_final_r": spearman([float(score) for score, _, _ in paired], [float(final_r) for _, final_r, _ in paired]),
            "warning": "Exploratory only; small sample." if len(paired) < 30 else "Exploratory association; not predictive proof.",
            "groups": {},
        }
        if not paired:
            return result
        if rule == "median_split":
            cut = median(scores)
            result["groups"] = {
                f"<= median {cut:.1f}": self.score_group([item for score, _, item in paired if score <= cut]),
                f"> median {cut:.1f}": self.score_group([item for score, _, item in paired if score > cut]),
            }
        elif rule == "quartiles":
            qs = quartiles(scores)
            result["groups"] = {"quartiles": qs}
        return result

    def score_group(self, rows: list[ResearchSignal]) -> dict[str, Any]:
        return {
            "n": len(rows),
            "median_final_r": median([item.final_r for item in rows]),
            "median_mfe": median([self.path_or_stored_extremes(item)[0] for item in rows]),
            "median_mae": median([self.path_or_stored_extremes(item)[1] for item in rows]),
        }

    def market_context_analysis(self, signals: list[ResearchSignal]) -> dict[str, Any]:
        categories: dict[str, list[ResearchSignal]] = defaultdict(list)
        for item in signals:
            categories[f"SPY={item.spy_state}"].append(item)
            categories[f"QQQ={item.qqq_state}"].append(item)
            categories[f"Regime={item.regime}"].append(item)
            categories[f"VIX={vix_bucket(item.vix_value)}"].append(item)
            categories[f"Gap={item.gap_flag}"].append(item)
            categories[f"OpenPos={position_bucket(item.open_positions_count)}"].append(item)
            categories[f"SameSector={position_bucket(item.open_positions_same_sector)}"].append(item)
        return self.category_metric_map(categories, suppress_under=10)

    def rejection_analytics(self, rejected: list[Any]) -> dict[str, Any]:
        verified = [row for row in rejected if row_version(row) >= 2]
        legacy = [row for row in rejected if row_version(row) < 2]
        failed_conditions = Counter()
        passed_conditions = Counter()
        legacy_labels = Counter()
        eligibility_status = Counter()
        setup_grades = Counter()
        final_status = Counter()
        for row in verified:
            eligibility_status[str(row_get(row, "eligibility_status") or "legacy/unavailable")] += 1
            setup_grades[str(row_get(row, "setup_grade") or row_get(row, "computed_grade") or "unknown")] += 1
            final_status[str(row_get(row, "final_acceptance_status") or "REJECTED")] += 1
            for condition in decode_json_list(row_get(row, "failed_conditions_v2_json")):
                failed_conditions[condition_failure_display(condition)] += 1
            for condition in decode_json_list(row_get(row, "passed_conditions_v2_json")):
                passed_conditions[str(condition.get("display_name") if isinstance(condition, dict) else condition)] += 1
        for row in legacy:
            for reason in decode_json_list(row["rejection_reasons_json"]):
                legacy_labels[str(reason)] += 1
        scores = [float(row["final_score"] or 0) for row in rejected]
        threshold = 58.0
        time_groups = Counter(time_bucket(optional_float(row_get(row, "minutes_after_market_open"))) for row in rejected)
        return {
            "verified_v2_count": len(verified),
            "legacy_count": len(legacy),
            "top_actual_blocking_gates": Counter(str(row_get(row, "actual_first_blocking_gate") or row["first_rejection_gate"] or "unknown") for row in verified).most_common(10),
            "top_first_rejection_gates": Counter(str(row_get(row, "actual_first_blocking_gate") or row["first_rejection_gate"] or "unknown") for row in verified).most_common(10),
            "top_failed_conditions": failed_conditions.most_common(10),
            "top_passed_conditions": passed_conditions.most_common(10),
            "top_rejection_reasons": failed_conditions.most_common(10),
            "eligibility_status_counts": eligibility_status.most_common(10),
            "setup_grade_counts": setup_grades.most_common(10),
            "final_acceptance_status_counts": final_status.most_common(10),
            "legacy_rejection_labels": legacy_labels.most_common(10),
            "score_distribution": {"count": len(scores), "median": median(scores), "min": min(scores) if scores else None, "max": max(scores) if scores else None},
            "near_miss": {f"within_{points}": len([score for score in scores if 0 <= threshold - score <= points]) for points in (1, 2, 3, 4, 5)},
            "by_ticker": Counter(str(row["ticker"]) for row in rejected).most_common(10),
            "by_sector": Counter(SECTOR_BY_TICKER.get(str(row["ticker"]).upper(), "Unknown") for row in rejected).most_common(10),
            "by_market_regime": Counter(str(load_json_dict(row["raw_metrics_json"]).get("regime", "unknown")) for row in rejected).most_common(10),
            "by_time_bucket": time_groups.most_common(),
            "note": "Verified v2 rows use actual blocking gates. Legacy labels are ambiguous and not used for research conclusions.",
        }

    def counterfactual(self, signals: list[ResearchSignal]) -> dict[str, Any]:
        covered = [item for item in signals if self.path_rows_by_signal.get(item.active_signal_id)]
        levels = (0.50, 0.75, 1.00, 1.25, 1.50)
        reach = {}
        for level in levels:
            reach[f"+{level:.2f}R"] = count_pct(
                len([item for item in covered if self.path_or_stored_extremes(item)[0] >= level]),
                len(covered),
            )
        returns = {}
        for level in (0.50, 0.75, 1.00):
            rows = [item for item in covered if self.path_or_stored_extremes(item)[0] >= level]
            returns[f"after_+{level:.2f}R"] = {
                "returned_to_entry": count_pct(
                    len([
                        item
                        for item in rows
                        if path_after_reach_touched(
                            item,
                            self.path_rows_by_signal.get(item.active_signal_id, []),
                            level,
                            0.0,
                            "below_or_equal",
                        )
                    ]),
                    len(rows),
                ),
                "returned_to_original_sl": count_pct(
                    len([
                        item
                        for item in rows
                        if path_after_reach_touched(
                            item,
                            self.path_rows_by_signal.get(item.active_signal_id, []),
                            level,
                            -1.0,
                            "below_or_equal",
                        )
                    ]),
                    len(rows),
                ),
                "reached_tp1_after": count_pct(
                    len([
                        item
                        for item in rows
                        if path_after_reach_touched(
                            item,
                            self.path_rows_by_signal.get(item.active_signal_id, []),
                            level,
                            1.0,
                            "above_or_equal",
                        )
                    ]),
                    len(rows),
                ),
            }
        return {
            "coverage": len(covered),
            "reach_rates": reach,
            "post_reach_returns": returns,
            "warning": "Counterfactual results are exploratory. Multiple comparisons and small samples can produce false winners. Validate any hypothesis on new out-of-sample trades before changing the strategy.",
        }

    def findings(self, signals: list[ResearchSignal]) -> list[str]:
        closed = [item for item in signals if item.is_closed]
        if not closed:
            return ["- No closed signals available yet. Confidence: insufficient. This is not proof of causation."]
        findings = []
        reached_050 = len([item for item in closed if self.path_or_stored_extremes(item)[0] >= 0.50])
        findings.append(self.finding_line(
            f"Among {len(closed)} closed signals, {pct(reached_050, len(closed)):.1f}% reached +0.50R before exit.",
            len(closed),
            "This measures favorable movement before closure",
        ))
        direct_sl = len([item for item in closed if item.lost and self.path_or_stored_extremes(item)[0] < 0.25])
        findings.append(self.finding_line(
            f"Among {len(closed)} closed signals, {pct(direct_sl, len(closed)):.1f}% went directly to SL by the defined rule.",
            len(closed),
            "This separates immediate failures from reversals",
        ))
        first_30 = [item for item in closed if item.minutes_after_market_open is not None and item.minutes_after_market_open <= 30]
        if first_30:
            findings.append(self.finding_line(
                f"Signals entered in the first 30 minutes had median final R {median([item.final_r for item in first_30]):.2f} across {len(first_30)} closed observations.",
                len(first_30),
                "This describes entry-time grouping",
            ))
        return findings[:5]

    @staticmethod
    def finding_line(observation: str, sample_size: int, why: str) -> str:
        confidence = confidence_label(sample_size)
        return f"- {observation} Sample={sample_size}. Confidence={confidence}. {why}; it does not prove causation."

    def group_rows(self, signals: list[ResearchSignal], group: str) -> list[ResearchSignal]:
        return [item for item in signals if item.group == group]

    def path_or_stored_extremes(self, item: ResearchSignal) -> tuple[float, float]:
        path = self.path_rows_by_signal.get(item.active_signal_id, [])
        if not path:
            self.path_fallback_count += 1
            return item.mfe_r, item.mae_r
        r_values = [path_r(row, item.entry_price, item.risk_per_share) for row in path]
        return max(r_values), min(r_values)

    def category_metric_map(self, groups: dict[str, list[ResearchSignal]], suppress_under: int = 0) -> dict[str, Any]:
        return {label: self.category_metrics(rows, suppress_under) for label, rows in sorted(groups.items())}

    def category_metrics(self, rows: list[ResearchSignal], suppress_under: int = 0) -> dict[str, Any]:
        closed = [item for item in rows if item.is_closed]
        return {
            "count": len(rows),
            "closed_count": len(closed),
            "win_rate": None if len(closed) < suppress_under else self.performance_block(rows)["win_rate_closed"],
            "median_final_r": None if len(closed) < suppress_under else median([item.final_r for item in closed]),
            "avg_final_r": avg([item.final_r for item in closed]),
            "avg_mfe": avg([self.path_or_stored_extremes(item)[0] for item in rows]),
            "avg_mae": avg([self.path_or_stored_extremes(item)[1] for item in rows]),
            "direct_sl_pct": None if len(closed) < suppress_under else self.performance_block(rows)["direct_sl_pct"],
        }

    def export_json(self, path: Optional[Path] = None) -> str:
        payload = json.dumps(self.build_data(), indent=2, sort_keys=True, default=str)
        if path:
            path.write_text(payload, encoding="utf-8")
        return payload

    def export_csv(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        data = self.build_data()
        signals = self.load_signals()
        write_csv(directory / "per_signal_audit.csv", [asdict(item) for item in signals])
        write_csv(directory / "price_path_summary.csv", [
            {"signal_id": signal_id, "rows": len(rows)}
            for signal_id, rows in self.path_rows_by_signal.items()
        ])
        write_csv(directory / "rejection_summary.csv", [
            {"label": label, "count": count}
            for label, count in data["rejection_analytics"]["top_first_rejection_gates"]
        ])
        write_csv(directory / "category_analysis.csv", [
            {"category": category, **metrics}
            for category, metrics in data["market_context"].items()
        ])

    # Formatting helpers
    @staticmethod
    def section(title: str, lines: list[str]) -> str:
        return "\n".join([title, *lines])

    @staticmethod
    def coverage_lines(data: dict[str, Any]) -> list[str]:
        return [
            f"Period: {data['report_period_days']}d | first={data['first_timestamp']} | last={data['last_timestamp']}",
            f"Accepted A/A+/A++: {data['accepted_trade_candidates']} | Strong B: {data['strong_b']}",
            f"Active signal rows: {data['active_signal_rows']} | Completed trade rows: {data['completed_trade_rows']}",
            f"Open: {data['open_count']} | Closed: {data['closed_count']} | Rejected>=50: {data['near_miss_score_50']}",
            f"Price path rows: {data['price_path_rows']} | Scan cycles: {data['scan_cycles']} | Failure cycles: {data['scan_failure_pct']:.1f}%",
            f"Missing path/entry/exit: {data['signals_missing_price_path']}/{data['signals_missing_entry_diagnostics']}/{data['signals_missing_exit_diagnostics']}",
            f"Schemas: {', '.join(data['schema_versions']) or 'n/a'}",
            f"Strategies: {', '.join(data['strategy_versions']) or 'n/a'}",
            f"Commits: {', '.join(data['git_commits']) or 'n/a'}",
            f"Config hashes: {len(data['config_hashes'])}",
            data["sample_warning"],
        ]

    @staticmethod
    def performance_lines(data: dict[str, Any]) -> list[str]:
        lines = []
        for group, item in data.items():
            pf = "n/a" if item["profit_factor"] is None else f"{item['profit_factor']:.2f}"
            lines.append(
                f"- {group}: total={item['total']} open={item['open']} closed={item['closed']} W/L/BE={item['wins']}/{item['losses']}/{item['breakeven']} "
                f"WR={item['win_rate_closed']:.1f}% avgR={item['avg_final_r']:.2f} medR={item['median_final_r']:.2f} exp={item['expectancy_r']:.2f} "
                f"MFE/MAE={item['avg_mfe']:.2f}/{item['avg_mae']:.2f} hold={format_seconds(item['median_holding_time'])} PF={pf} "
                f"TP1/2/3={item['tp1_reach_pct']:.0f}/{item['tp2_reach_pct']:.0f}/{item['tp3_reach_pct']:.0f}% directSL={item['direct_sl_pct']:.0f}%"
            )
        return lines

    @staticmethod
    def distribution_lines(data: dict[str, Any]) -> list[str]:
        lines = [data.get("fallback_note", "")]
        for group in ("Overall", "A++ Signal", "A+ Signal", "A Signal", "Strong B Experimental Watch"):
            item = data.get(group, {})
            if not item:
                continue
            mfe = item["mfe"]
            mae = item["mae"]
            lines.append(f"- {group} n={item['n']} MFE +0.5/+1/+2R: {mfe['+0.50R']} | {mfe['+1.00R']} | {mfe['+2.00R']}")
            lines.append(f"  MAE -0.5/-1/below-1R: {mae['-0.50R']} | {mae['-1.00R']} | {mae['below -1.00R']}")
        return lines

    @staticmethod
    def evolution_lines(data: dict[str, Any]) -> list[str]:
        lines = ["Time-bucketed evolution:"]
        for label, item in data["buckets"].items():
            lines.append(f"- {label}: n={item['coverage']} med MFE/MAE={item['median_mfe']:.2f}/{item['median_mae']:.2f}")
        lines.append("Median time-to levels:")
        for label, item in data["time_to"].items():
            lines.append(f"- {label}: n={item['n']} median={format_seconds(item['median_seconds'])}")
        return lines

    @staticmethod
    def category_lines(title: str, data: dict[str, Any]) -> list[str]:
        lines = [f"{title}:"]
        for label, item in list(data.items())[:12]:
            win = "suppressed" if item.get("win_rate") is None else f"{item['win_rate']:.1f}%"
            med = "suppressed" if item.get("median_final_r") is None else f"{item['median_final_r']:.2f}"
            direct = "suppressed" if item.get("direct_sl_pct") is None else f"{item['direct_sl_pct']:.1f}%"
            lines.append(f"- {label}: n={item['count']} closed={item['closed_count']} WR={win} medR={med} directSL={direct}")
        return lines

    @staticmethod
    def entry_quality_lines(data: dict[str, Any]) -> list[str]:
        lines = ["Entry quality medians (observed association, not causation):"]
        for label, item in list(data.items())[:8]:
            lines.append(
                f"- {label}: n={item['sample_count']} miss={item['missing_count']} win/loss/BE med="
                f"{fmt(item['winner_median'])}/{fmt(item['loser_median'])}/{fmt(item['breakeven_median'])}"
            )
        return lines

    @staticmethod
    def rejection_lines(data: dict[str, Any]) -> list[str]:
        lines = ["Rejection analytics:"]
        lines.append(f"- Verified v2 rows: {data['verified_v2_count']} legacy/ambiguous: {data['legacy_count']}")
        lines.append(f"- Score distribution: n={data['score_distribution']['count']} med={fmt(data['score_distribution']['median'])}")
        lines.append(f"- Near-miss: {data['near_miss']}")
        lines.append(f"- Actual blocking gates: {data['top_actual_blocking_gates'][:5]}")
        lines.append(f"- Eligibility status: {data.get('eligibility_status_counts', [])[:5]}")
        lines.append(f"- Setup grades before policy: {data.get('setup_grade_counts', [])[:5]}")
        lines.append(f"- Final acceptance status: {data.get('final_acceptance_status_counts', [])[:5]}")
        lines.append(f"- Failed conditions: {data['top_failed_conditions'][:5]}")
        lines.append(f"- Passed conditions: {data['top_passed_conditions'][:5]}")
        if data["legacy_rejection_labels"]:
            lines.append(f"- Legacy rejection labels — condition result unavailable: {data['legacy_rejection_labels'][:5]}")
        lines.append(f"- Tickers: {data['by_ticker'][:5]}")
        lines.append(f"- Sectors: {data['by_sector'][:5]}")
        lines.append(f"- Regimes: {data['by_market_regime'][:5]}")
        lines.append(f"- {data['note']}")
        return lines

    @staticmethod
    def comparison_lines(data: dict[str, Any]) -> list[str]:
        lines = ["A++ vs Strong B comparison:", f"- {data['warning']}"]
        for group in ("A++ Signal", "Strong B Experimental Watch"):
            item = data[group]
            lines.append(
                f"- {group}: n={item['sample_size']} WR={item['win_rate']:.1f}% medR={item['median_final_r']:.2f} "
                f"exp={item['expectancy']:.2f} MFE/MAE={item['median_mfe']:.2f}/{item['median_mae']:.2f} "
                f"TP1={item['tp1_reach_rate']:.1f}% directSL={item['direct_sl_rate']:.1f}% hold={format_seconds(item['median_holding_time'])}"
            )
        return lines

    @staticmethod
    def score_lines(data: dict[str, Any]) -> list[str]:
        return [
            "Score vs outcome:",
            f"- n={data['sample_size']} rule={data['rule']} spearman={fmt(data['spearman_score_final_r'])}",
            f"- {data['warning']}",
            f"- groups={data['groups']}",
        ]

    @staticmethod
    def movement_lines(data: dict[str, Any]) -> list[str]:
        lines = ["Movement sequencing (descriptive buckets):"]
        for label, item in data.items():
            lines.append(
                f"- {label}: n={item['count']} pct={item['pct']:.1f}% avg score={fmt(item['avg_score'])} "
                f"breakout={fmt(item['avg_breakout_distance'])} minOpen={fmt(item['avg_entry_minutes_after_open'])} "
                f"VIX={fmt(item['avg_vix'])} spread={fmt(item['avg_spread'])} sameSector={fmt(item['avg_same_sector_exposure'])}"
            )
        return lines

    @staticmethod
    def counterfactual_lines(data: dict[str, Any]) -> list[str]:
        return [
            "Counterfactual -- strictly descriptive:",
            f"- Coverage: {data['coverage']} signals with price_path",
            f"- Reach rates: {data['reach_rates']}",
            f"- Post-reach returns: {data['post_reach_returns']}",
            f"- {data['warning']}",
        ]


def row_get(row: Any, key: str, default: Any = None) -> Any:
    try:
        return row[key]
    except (KeyError, IndexError):
        return default


def optional_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def optional_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def avg(values: Iterable[Optional[float]]) -> float:
    items = [float(value) for value in values if value is not None and not math.isnan(float(value))]
    return sum(items) / len(items) if items else 0.0


def median(values: Iterable[Optional[float]]) -> float:
    items = [float(value) for value in values if value is not None and not math.isnan(float(value))]
    return float(statistics.median(items)) if items else 0.0


def pct(count: int, total: int) -> float:
    return count / total * 100 if total else 0.0


def count_pct(count: int, total: int) -> str:
    return f"{count}/{total} ({pct(count, total):.1f}%)"


def fmt(value: Any) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return str(value)


def format_seconds(value: Optional[float]) -> str:
    if value is None:
        return "n/a"
    minutes = float(value) / 60
    if minutes < 90:
        return f"{minutes:.1f}m"
    return f"{minutes / 60:.1f}h"


def path_r(row: Any, entry: float, risk: float) -> float:
    price = float(row["price"] or entry)
    return (price - entry) / max(risk, 0.01)


def relative_path_r(path: list[Any], entry: float, risk: float) -> list[tuple[float, float]]:
    if not path:
        return []
    start = parse_time(row_get(path[0], "timestamp"))
    if start is None:
        return []
    result = []
    for row in path:
        timestamp = parse_time(row_get(row, "timestamp"))
        if timestamp is None:
            continue
        result.append(((timestamp - start), path_r(row, entry, risk)))
    return result


def parse_time(value: Any) -> Optional[float]:
    if not value:
        return None
    from datetime import datetime

    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def first_reach_seconds(path: list[Any], item: ResearchSignal, level: float) -> Optional[float]:
    rel = relative_path_r(path, item.entry_price, item.risk_per_share)
    for seconds, r_value in rel:
        if r_value >= level:
            return seconds
    return None


def path_after_reach_touched(
    item: ResearchSignal,
    path: list[Any],
    reach_level: float,
    target_level: float,
    direction: str,
) -> bool:
    rel = relative_path_r(path, item.entry_price, item.risk_per_share)
    reached_at_index: Optional[int] = None
    for idx, (_, r_value) in enumerate(rel):
        if r_value >= reach_level:
            reached_at_index = idx
            break
    if reached_at_index is None:
        return False
    for _, r_value in rel[reached_at_index + 1:]:
        if direction == "below_or_equal" and r_value <= target_level:
            return True
        if direction == "above_or_equal" and r_value >= target_level:
            return True
    return False


def path_returned_to(item: ResearchSignal, path: list[Any], level: float) -> bool:
    rel = relative_path_r(path, item.entry_price, item.risk_per_share)
    for _, r_value in rel:
        if r_value <= level:
            return True
    return False


def quartiles(values: Iterable[float]) -> dict[str, float]:
    items = sorted(float(value) for value in values)
    if not items:
        return {}
    return {
        "q1": items[int((len(items) - 1) * 0.25)],
        "q2": median(items),
        "q3": items[int((len(items) - 1) * 0.75)],
    }


def spearman(xs: list[float], ys: list[float]) -> Optional[float]:
    if len(xs) < 3 or len(xs) != len(ys):
        return None
    rx = rank(xs)
    ry = rank(ys)
    mean_x = avg(rx)
    mean_y = avg(ry)
    numerator = sum((x - mean_x) * (y - mean_y) for x, y in zip(rx, ry))
    denominator = math.sqrt(sum((x - mean_x) ** 2 for x in rx) * sum((y - mean_y) ** 2 for y in ry))
    return numerator / denominator if denominator else None


def rank(values: list[float]) -> list[float]:
    order = sorted((value, idx) for idx, value in enumerate(values))
    ranks = [0.0] * len(values)
    for rank_idx, (_, idx) in enumerate(order, start=1):
        ranks[idx] = float(rank_idx)
    return ranks


def vix_bucket(value: Optional[float]) -> str:
    if value is None:
        return "unknown"
    if value < 15:
        return "<15"
    if value < 20:
        return "15-20"
    if value < 25:
        return "20-25"
    return "25+"


def position_bucket(value: Optional[int]) -> str:
    if value is None:
        return "unknown"
    if value == 0:
        return "0"
    if value <= 2:
        return "1-2"
    if value <= 4:
        return "3-4"
    return "5+"


def time_bucket(value: Optional[float]) -> str:
    if value is None:
        return "unknown"
    if value <= 30:
        return "0-30m"
    if value <= 60:
        return "31-60m"
    if value <= 120:
        return "61-120m"
    if value <= 240:
        return "121-240m"
    return ">240m"


def confidence_label(sample_size: int) -> str:
    if sample_size < 15:
        return "insufficient"
    if sample_size < 30:
        return "low"
    if sample_size < 100:
        return "preliminary"
    return "moderate"


def load_json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        decoded = json.loads(str(value))
        return decoded if isinstance(decoded, dict) else {}
    except json.JSONDecodeError:
        return {}


def decode_json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if not value:
        return []
    try:
        decoded = json.loads(str(value))
        return decoded if isinstance(decoded, list) else [decoded]
    except json.JSONDecodeError:
        return [str(value)]


def row_version(row: Any) -> int:
    try:
        return int(row["diagnostics_format_version"] or 1)
    except (KeyError, TypeError, ValueError):
        return 1


def condition_failure_display(condition: Any) -> str:
    if isinstance(condition, dict):
        return SignalDiagnosticsRecorder.failure_display(condition)
    return str(condition)


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
