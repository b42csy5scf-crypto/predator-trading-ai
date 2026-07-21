from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any, Iterable, Optional

from predator_trading_ai.database.db import Database
from predator_trading_ai.engines.signal_diagnostics import SignalDiagnosticsRecorder


TRADE_GRADES = {"A++ Signal", "A+ Signal", "A Signal"}
TRADE_GRADE_ORDER = ("A++ Signal", "A+ Signal", "A Signal")


@dataclass(frozen=True)
class DiagnosticOutcome:
    ticker: str
    grade: str
    alert_type: str
    status: str
    final_outcome: str
    exit_reason: str
    mfe_r: float
    mae_r: float
    current_r: float
    holding_seconds: Optional[float]
    tp1_hit_at: Optional[str]
    tp2_hit_at: Optional[str]
    tp3_hit_at: Optional[str]
    sl_hit_at: Optional[str]


class DiagnosticsReport:
    def __init__(self, db: Database, days: int = 7) -> None:
        self.db = db
        self.days = max(int(days), 1)

    def build(self) -> str:
        accepted = self.accepted_rows()
        outcomes = self.outcomes()
        rejected = self.rejected_rows()
        trade_outcomes = [
            item
            for item in outcomes
            if item.alert_type == "trade_candidate" and item.grade in TRADE_GRADES
        ]
        strong_b = [
            item
            for item in outcomes
            if item.alert_type == "experimental_watch" and item.grade == "B Watch Alert"
        ]
        closed_trade_candidates = [item for item in trade_outcomes if item.status == "closed"]

        sections = [
            "Predator Diagnostics Report",
            "",
            self._section("Data Coverage", self.data_coverage(accepted, outcomes, rejected, closed_trade_candidates)),
            self._section("Trade Candidates Summary", self.metric_block(trade_outcomes)),
            self._section("By Grade Performance", self.by_grade_performance(trade_outcomes)),
            self._section("Strong B Experimental Watch", self.metric_block(strong_b)),
            self._section("Rejection Analytics", self.rejection_analytics(rejected)),
            self._section("Outcome Behavior", self.outcome_behavior(trade_outcomes, strong_b)),
            self._section("Best / Worst Tickers", self.ticker_extremes(outcomes)),
        ]
        return "\n".join(sections)

    def accepted_rows(self) -> list[Any]:
        return self.db.fetch_all(
            """
            SELECT *
            FROM signal_diagnostics
            WHERE created_at >= datetime('now', ?)
            ORDER BY created_at
            """,
            [f"-{self.days} days"],
        )

    def outcomes(self) -> list[DiagnosticOutcome]:
        rows = self.db.fetch_all(
            """
            SELECT o.*,
                   COALESCE(a.status, CASE WHEN o.final_outcome IS NULL THEN 'active' ELSE 'closed' END) AS active_status
            FROM signal_outcome_diagnostics o
            LEFT JOIN active_signals a ON a.id = o.active_signal_id
            WHERE o.created_at >= datetime('now', ?)
            ORDER BY o.created_at
            """,
            [f"-{self.days} days"],
        )
        return [
            DiagnosticOutcome(
                ticker=row["ticker"],
                grade=row["grade"],
                alert_type=row["alert_type"] if "alert_type" in row.keys() else "trade_candidate",
                status=str(row["active_status"] or "active"),
                final_outcome=str(row["final_outcome"] or ""),
                exit_reason=str(row["exit_reason"] or ""),
                mfe_r=float(row["mfe_r"] or 0),
                mae_r=float(row["mae_r"] or 0),
                current_r=float(row["current_r"] or 0),
                holding_seconds=float(row["holding_seconds"]) if row["holding_seconds"] is not None else None,
                tp1_hit_at=row["tp1_hit_at"],
                tp2_hit_at=row["tp2_hit_at"],
                tp3_hit_at=row["tp3_hit_at"],
                sl_hit_at=row["sl_hit_at"],
            )
            for row in rows
        ]

    def rejected_rows(self) -> list[Any]:
        return self.db.fetch_all(
            """
            SELECT *
            FROM rejected_candidate_diagnostics
            WHERE created_at >= datetime('now', ?)
              AND final_score >= 50
            ORDER BY created_at
            """,
            [f"-{self.days} days"],
        )

    def data_coverage(
        self,
        accepted: list[Any],
        outcomes: list[DiagnosticOutcome],
        rejected: list[Any],
        closed_trade_candidates: list[DiagnosticOutcome],
    ) -> list[str]:
        trade_accepted = [
            row
            for row in accepted
            if row["alert_type"] == "trade_candidate" and row["grade"] in TRADE_GRADES
        ]
        strong_b_accepted = [
            row
            for row in accepted
            if row["alert_type"] == "experimental_watch" and row["grade"] == "B Watch Alert"
        ]
        open_signals = len([item for item in outcomes if item.status == "active"])
        closed_signals = len([item for item in outcomes if item.status == "closed"])
        lines = [
            f"Days included: {self.days}",
            f"Accepted A/A+/A++ signals: {len(trade_accepted)}",
            f"Strong B Experimental Watch signals: {len(strong_b_accepted)}",
            f"Open signals: {open_signals}",
            f"Closed signals: {closed_signals}",
            f"Rejected candidates stored: {len(rejected)}",
        ]
        if len(closed_trade_candidates) < 15:
            lines.append("Sample size is small; do not change strategy yet.")
        return lines

    def metric_block(
        self,
        rows: list[DiagnosticOutcome],
        *,
        include_final_r: bool = True,
        include_holding: bool = True,
    ) -> list[str]:
        wins = len([item for item in rows if self.is_win(item)])
        losses = len([item for item in rows if item.final_outcome == "SL"])
        breakeven = len([item for item in rows if item.final_outcome == "BE"])
        closed = len([item for item in rows if item.status == "closed"])
        lines = [
            f"Total: {len(rows)}",
            f"Wins: {wins}",
            f"Losses: {losses}",
            f"Breakeven: {breakeven}",
            f"Win rate: {self.win_rate(rows):.1f}%",
            f"Avg MFE (R): {self.avg([item.mfe_r for item in rows]):.2f}",
            f"Avg MAE (R): {self.avg([item.mae_r for item in rows]):.2f}",
        ]
        if include_final_r:
            lines.append(f"Avg final R: {self.avg([item.current_r for item in rows if item.status == 'closed']):.2f}")
        if include_holding:
            lines.append(f"Avg holding time: {self.format_seconds(self.avg_holding(rows))}")
        if not closed:
            lines.append("Closed sample: 0")
        return lines

    def by_grade_performance(self, rows: list[DiagnosticOutcome]) -> list[str]:
        lines = [self.grade_header()]
        for grade in TRADE_GRADE_ORDER:
            grade_rows = [item for item in rows if item.grade == grade]
            lines.append(self.grade_metric_line(grade, grade_rows))
        return lines

    def grade_metric_line(self, label: str, rows: list[DiagnosticOutcome]) -> str:
        wins = len([item for item in rows if self.is_win(item)])
        losses = len([item for item in rows if item.final_outcome == "SL"])
        breakeven = len([item for item in rows if item.final_outcome == "BE"])
        return (
            f"{label:<10} "
            f"{len(rows):>3} "
            f"{wins:>2} "
            f"{losses:>2} "
            f"{breakeven:>2} "
            f"{self.win_rate(rows):>5.1f}% "
            f"{self.avg([item.mfe_r for item in rows]):>5.2f} "
            f"{self.avg([item.mae_r for item in rows]):>5.2f} "
            f"{self.avg([item.current_r for item in rows if item.status == 'closed']):>5.2f} "
            f"{self.format_seconds(self.avg_holding(rows)):>8}"
        )

    @staticmethod
    def grade_header() -> str:
        return "Grade      Tot  W  L BE   Win%   MFE   MAE  FinalR  Hold"

    def rejection_analytics(self, rejected: list[Any]) -> list[str]:
        verified = [row for row in rejected if self.row_version(row) >= 2]
        legacy = [row for row in rejected if self.row_version(row) < 2]
        blocking_gates = Counter()
        failed_conditions = Counter()
        passed_conditions = Counter()
        legacy_labels = Counter()
        eligibility_status = Counter()
        setup_grades = Counter()
        final_status = Counter()
        for row in verified:
            gate = self.row_get(row, "actual_first_blocking_gate") or row["first_rejection_gate"] or "unknown"
            blocking_gates[str(gate)] += 1
            eligibility_status[str(self.row_get(row, "eligibility_status") or "legacy/unavailable")] += 1
            setup_grades[str(self.row_get(row, "setup_grade") or row["computed_grade"] or "unknown")] += 1
            final_status[str(self.row_get(row, "final_acceptance_status") or "REJECTED")] += 1
            for condition in self.decode_json_list(self.row_get(row, "failed_conditions_v2_json")):
                failed_conditions[self.failure_display(condition)] += 1
            for condition in self.decode_json_list(self.row_get(row, "passed_conditions_v2_json")):
                passed_conditions[str(condition.get("display_name") if isinstance(condition, dict) else condition)] += 1
        for row in legacy:
            for reason in self.decode_json_list(row["rejection_reasons_json"]):
                legacy_labels[str(reason)] += 1
        lines = [
            f"Candidates rejected with score >= 50: {len(rejected)}",
            f"Verified diagnostics v2 rows: {len(verified)}",
            f"Legacy/ambiguous rows: {len(legacy)}",
            "Top actual blocking gates:",
        ]
        lines.extend(self.counter_lines(blocking_gates, 5))
        lines.append("Eligibility status:")
        lines.extend(self.counter_lines(eligibility_status, 5))
        lines.append("Setup grades before policy:")
        lines.extend(self.counter_lines(setup_grades, 5))
        lines.append("Final acceptance status:")
        lines.extend(self.counter_lines(final_status, 5))
        lines.append("Top failed conditions:")
        lines.extend(self.counter_lines(failed_conditions, 5))
        lines.append("Most common passed conditions:")
        lines.extend(self.counter_lines(passed_conditions, 5))
        if legacy:
            lines.append("Legacy rejection labels — condition result unavailable:")
            lines.extend(self.counter_lines(legacy_labels, 5))
        return lines

    @staticmethod
    def row_version(row: Any) -> int:
        try:
            return int(row["diagnostics_format_version"] or 1)
        except (KeyError, TypeError, ValueError):
            return 1

    @staticmethod
    def row_get(row: Any, key: str, default: Any = None) -> Any:
        try:
            return row[key]
        except (KeyError, IndexError):
            return default

    @staticmethod
    def failure_display(condition: Any) -> str:
        if isinstance(condition, dict):
            return SignalDiagnosticsRecorder.failure_display(condition)
        return str(condition)

    def outcome_behavior(
        self,
        trade_outcomes: list[DiagnosticOutcome],
        strong_b: list[DiagnosticOutcome],
    ) -> list[str]:
        rows = [*trade_outcomes, *strong_b]
        reached_1r_before_sl = len([item for item in rows if item.mfe_r >= 1 and item.final_outcome == "SL"])
        direct_sl = len([item for item in rows if item.final_outcome == "SL" and item.mfe_r < 1])
        fastest_sl = self.fastest(rows, lambda item: item.sl_hit_at is not None)
        fastest_tp = self.fastest(rows, lambda item: item.tp1_hit_at is not None or item.tp2_hit_at is not None or item.tp3_hit_at is not None)
        return [
            f"Signals reached +1R before SL: {reached_1r_before_sl}",
            f"Signals went directly to SL: {direct_sl}",
            f"Fastest SL: {fastest_sl}",
            f"Fastest TP: {fastest_tp}",
        ]

    def ticker_extremes(self, rows: list[DiagnosticOutcome]) -> list[str]:
        if not rows:
            return ["No outcome diagnostics yet."]
        by_ticker: dict[str, list[DiagnosticOutcome]] = defaultdict(list)
        for item in rows:
            by_ticker[item.ticker].append(item)
        final_rank = sorted(by_ticker.items(), key=lambda pair: self.avg([item.current_r for item in pair[1]]), reverse=True)
        mfe_rank = sorted(by_ticker.items(), key=lambda pair: max(item.mfe_r for item in pair[1]), reverse=True)
        mae_rank = sorted(by_ticker.items(), key=lambda pair: min(item.mae_r for item in pair[1]))
        return [
            f"Best by final R: {self.ticker_metric(final_rank, final=True)}",
            f"Worst by final R: {self.ticker_metric(list(reversed(final_rank)), final=True)}",
            f"Highest MFE: {self.ticker_metric(mfe_rank, metric='mfe')}",
            f"Highest MAE: {self.ticker_metric(mae_rank, metric='mae')}",
        ]

    @staticmethod
    def is_win(item: DiagnosticOutcome) -> bool:
        return item.final_outcome.startswith("TP")

    def win_rate(self, rows: list[DiagnosticOutcome]) -> float:
        closed = [item for item in rows if item.status == "closed" and item.final_outcome != "BE"]
        return len([item for item in closed if self.is_win(item)]) / len(closed) * 100 if closed else 0.0

    @staticmethod
    def avg(values: Iterable[float]) -> float:
        items = list(values)
        return sum(items) / len(items) if items else 0.0

    @staticmethod
    def avg_holding(rows: list[DiagnosticOutcome]) -> Optional[float]:
        values = [item.holding_seconds for item in rows if item.holding_seconds is not None]
        return sum(values) / len(values) if values else None

    @staticmethod
    def format_seconds(value: Optional[float]) -> str:
        if value is None:
            return "n/a"
        minutes = value / 60
        if minutes < 90:
            return f"{minutes:.1f} min"
        return f"{minutes / 60:.1f} h"

    def fastest(self, rows: list[DiagnosticOutcome], predicate) -> str:
        matches = [item for item in rows if predicate(item) and item.holding_seconds is not None]
        if not matches:
            return "n/a"
        fastest = min(matches, key=lambda item: item.holding_seconds or 0)
        return f"{fastest.ticker} {self.format_seconds(fastest.holding_seconds)}"

    def ticker_metric(
        self,
        ranked: list[tuple[str, list[DiagnosticOutcome]]],
        *,
        final: bool = False,
        metric: str = "final",
    ) -> str:
        if not ranked:
            return "n/a"
        ticker, rows = ranked[0]
        if final:
            value = self.avg([item.current_r for item in rows])
        elif metric == "mfe":
            value = max(item.mfe_r for item in rows)
        else:
            value = min(item.mae_r for item in rows)
        return f"{ticker} {value:.2f}R"

    @staticmethod
    def counter_lines(counter: Counter[str], limit: int) -> list[str]:
        if not counter:
            return ["- n/a"]
        return [f"- {label}: {count}" for label, count in counter.most_common(limit)]

    @staticmethod
    def decode_json_list(value: Any) -> list[Any]:
        if value is None:
            return []
        if isinstance(value, list):
            return value
        try:
            decoded = json.loads(str(value))
        except json.JSONDecodeError:
            return [str(value)] if str(value) else []
        return decoded if isinstance(decoded, list) else [decoded]

    @staticmethod
    def _section(title: str, lines: Iterable[str]) -> str:
        return "\n".join([title, *lines, ""])
