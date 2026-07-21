from __future__ import annotations

import json
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from predator_trading_ai.config import Settings, get_settings
from predator_trading_ai.database.db import Database
from predator_trading_ai.engines.alert_policy import MIN_B_ALERT_SCORE_FLOOR
from predator_trading_ai.engines.signal_diagnostics import SignalDiagnosticsRecorder


SCORE_BUCKETS = (
    ("below 50", None, 50.0),
    ("50.00-54.99", 50.0, 55.0),
    ("55.00-57.99", 55.0, 58.0),
    ("58.00-59.99", 58.0, 60.0),
    ("60.00-64.99", 60.0, 65.0),
    ("65.00-71.99", 65.0, 72.0),
    ("72.00-79.99", 72.0, 80.0),
    ("80+", 80.0, None),
)


@dataclass(frozen=True)
class Period:
    label: str
    start: datetime
    end: datetime


class RejectionInsightsReport:
    """Read-only reports for recent rejected-candidate diagnostics."""

    def __init__(self, settings: Optional[Settings] = None, db: Optional[Database] = None, now: Optional[datetime] = None) -> None:
        self.settings = settings or get_settings()
        self.db = db or Database(self.settings)
        self.now = now or datetime.now(timezone.utc)
        if self.now.tzinfo is None:
            self.now = self.now.replace(tzinfo=timezone.utc)
        self.now = self.now.astimezone(timezone.utc)

    def rejected_examples(self, limit: int = 10) -> str:
        safe_limit = max(1, min(int(limit or 10), 25))
        rows = self.db.fetch_all(
            """
            SELECT created_at, ticker, final_score, computed_grade, actual_first_blocking_gate,
                   passed_conditions_v2_json, failed_conditions_v2_json, blocking_conditions_json,
                   setup_grade, eligibility_status, eligibility_stage, block_reason_display,
                   final_acceptance_status, displayed_grade_legacy, classification_format_version
            FROM rejected_candidate_diagnostics
            WHERE final_score >= 50
              AND diagnostics_format_version = 2
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            [safe_limit],
        )
        if not rows:
            return "No verified diagnostics v2 rows are available yet."

        lines = [f"Rejected Examples (verified v2, latest {len(rows)})"]
        for row in rows:
            passed = decode_json_list(row["passed_conditions_v2_json"])
            failed = decode_json_list(row["failed_conditions_v2_json"])
            blocking = decode_json_list(row["blocking_conditions_json"])
            lines.extend(
                [
                    "",
                    f"{row['ticker']} | Score {float(row['final_score']):.1f} | {short_grade(row['computed_grade'])}",
                    f"Time: {row['created_at']}",
                    f"Setup grade: {short_grade(row_get(row, 'setup_grade') or self.score_grade(float(row['final_score'])))}",
                    f"Eligibility: {row_get(row, 'eligibility_status') or 'legacy/unavailable'} @ {row_get(row, 'eligibility_stage') or 'legacy/unavailable'}",
                    f"Final: {row_get(row, 'final_acceptance_status') or 'REJECTED'}",
                    f"Reason: {row_get(row, 'block_reason_display') or display_gate(row['actual_first_blocking_gate'], blocking)}",
                    f"Classification: v{row_get(row, 'classification_format_version') or 'legacy'}",
                    f"First blocking gate: {display_gate(row['actual_first_blocking_gate'], blocking)}",
                    f"Counts: pass={len(passed)} fail={len(failed)} block={len(blocking)}",
                    "Passed:",
                    *[f"✅ {condition_line(item)}" for item in passed[:3]],
                    "Failed:",
                    *[f"❌ {failure_condition_line(item)}{blocking_suffix(item)}" for item in failed[:3]],
                ]
            )
        return "\n".join(lines).strip()

    def score_distribution(self, period_arg: str = "today") -> str:
        period = self.period_for(period_arg)
        rejected = self.rejected_rows(period)
        accepted = self.accepted_rows(period)
        trade_candidates = [row for row in accepted if row["alert_type"] == "trade_candidate" and row["grade"] in {"A++ Signal", "A+ Signal", "A Signal"}]
        strong_b = [row for row in accepted if row["alert_type"] == "experimental_watch" and row["grade"] == "B Watch Alert"]
        scores = [float(row["final_score"]) for row in rejected if row["final_score"] is not None]
        verified = [row for row in rejected if row_version(row) >= 2]
        legacy = [row for row in rejected if row_version(row) < 2]
        lines = [
            f"Score Distribution ({period.label})",
            f"Start: {period.start.isoformat()}",
            f"End: {period.end.isoformat()}",
            f"Rejected candidates: {len(rejected)}",
            f"Accepted A/A+/A++: {len(trade_candidates)}",
            f"Strong B: {len(strong_b)}",
            f"Verified v2 rows: {len(verified)}",
            f"Legacy score-only rows: {len(legacy)}",
            "",
            "Rejected score buckets:",
            *self.bucket_lines(scores),
            "",
            f"Average: {fmt_number(avg(scores))}",
            f"Median: {fmt_number(median(scores))}",
            f"Min/Max: {fmt_number(min(scores) if scores else None)} / {fmt_number(max(scores) if scores else None)}",
            f"90th percentile: {fmt_number(percentile(scores, 90))}",
            *self.near_min_b_lines(scores),
            "",
            "Top almost-trades",
            *self.almost_trade_lines(verified[:10]),
        ]
        if period.label == "today":
            lines.extend(["", *self.today_baseline_lines(scores, period)])
        return "\n".join(lines).strip()

    def rejected_rows(self, period: Period) -> list[Any]:
        return self.db.fetch_all(
            """
            SELECT *
            FROM rejected_candidate_diagnostics
            WHERE created_at >= ?
              AND created_at <= ?
            ORDER BY final_score DESC, created_at DESC, id DESC
            """,
            [period.start.isoformat(), period.end.isoformat()],
        )

    def accepted_rows(self, period: Period) -> list[Any]:
        return self.db.fetch_all(
            """
            SELECT created_at, grade, alert_type, score
            FROM signal_diagnostics
            WHERE created_at >= ?
              AND created_at <= ?
            ORDER BY created_at DESC
            """,
            [period.start.isoformat(), period.end.isoformat()],
        )

    def bucket_lines(self, scores: list[float]) -> list[str]:
        lines = []
        for label, low, high in SCORE_BUCKETS:
            count = len([score for score in scores if (low is None or score >= low) and (high is None or score < high)])
            lines.append(f"- {label}: {count}")
        return lines

    def near_min_b_lines(self, scores: list[float]) -> list[str]:
        threshold = max(float(getattr(self.settings, "min_score_b", MIN_B_ALERT_SCORE_FLOOR)), MIN_B_ALERT_SCORE_FLOOR)
        lines = []
        for distance in (1, 2, 3, 5):
            count = len([score for score in scores if 0 <= threshold - score <= distance])
            lines.append(f"Within {distance} point(s) of MIN_SCORE_B: {count}")
        return lines

    def almost_trade_lines(self, rows: list[Any]) -> list[str]:
        if not rows:
            return ["- none"]
        lines = []
        for row in rows[:10]:
            blocking = decode_json_list(row["blocking_conditions_json"])
            gate = display_gate(row["actual_first_blocking_gate"], blocking)
            score_grade = self.score_grade(float(row["final_score"]))
            displayed_grade = str(row["computed_grade"] or "unknown")
            setup_grade = row_get(row, "setup_grade") or score_grade
            eligibility = row_get(row, "eligibility_status") or "legacy/unavailable"
            lines.append(f"- {row['ticker']} {float(row['final_score']):.1f}")
            lines.append(
                f"  Score grade: {short_grade(score_grade)} | Setup: {short_grade(setup_grade)} | "
                f"Displayed: {short_grade(displayed_grade)}"
            )
            lines.append(f"  Eligibility: {eligibility} @ {row_get(row, 'eligibility_stage') or 'legacy/unavailable'}")
            lines.append(f"  Blocked by: {gate} ({len(blocking)} blocking)")
        return lines

    def score_grade(self, score: float) -> str:
        if score >= float(self.settings.min_score_a_plus_plus):
            return "A++ Signal"
        if score >= float(self.settings.min_score_a_plus):
            return "A+ Signal"
        if score >= float(self.settings.min_score_a):
            return "A Signal"
        threshold_b = max(float(getattr(self.settings, "min_score_b", MIN_B_ALERT_SCORE_FLOOR)), MIN_B_ALERT_SCORE_FLOOR)
        if score >= threshold_b:
            return "B Watch Alert"
        return "C Risky/Early Alert"

    def today_baseline_lines(self, today_scores: list[float], period: Period) -> list[str]:
        baseline_start = period.start - timedelta(days=7)
        rows = self.db.fetch_all(
            """
            SELECT created_at, final_score
            FROM rejected_candidate_diagnostics
            WHERE created_at >= ?
              AND created_at < ?
            ORDER BY created_at
            """,
            [baseline_start.isoformat(), period.start.isoformat()],
        )
        baseline_scores = [float(row["final_score"]) for row in rows if row["final_score"] is not None]
        daily_counts: dict[str, int] = {}
        for row in rows:
            day = str(row["created_at"])[:10]
            daily_counts[day] = daily_counts.get(day, 0) + 1
        if not baseline_scores or not today_scores:
            return ["Today vs baseline:", "- Insufficient baseline data."]
        today_avg = avg(today_scores)
        baseline_avg = avg(baseline_scores)
        direction = "similar to"
        if today_avg is not None and baseline_avg is not None:
            if today_avg > baseline_avg + 1:
                direction = "higher than"
            elif today_avg < baseline_avg - 1:
                direction = "lower than"
        return [
            "Today vs baseline:",
            f"- Today avg: {fmt_number(today_avg)} | Previous-7 avg: {fmt_number(baseline_avg)}",
            f"- Today median: {fmt_number(median(today_scores))} | Previous-7 median: {fmt_number(median(baseline_scores))}",
            f"- Today p90: {fmt_number(percentile(today_scores, 90))} | Previous-7 p90: {fmt_number(percentile(baseline_scores, 90))}",
            f"- Today rejected: {len(today_scores)} | Previous daily avg rejected: {fmt_number(avg(list(daily_counts.values())))}",
            f"- Today's scores are {direction} the available 7-day baseline.",
        ]

    def period_for(self, value: str) -> Period:
        arg = (value or "today").strip().lower()
        if arg == "7d":
            return Period("7d", self.now - timedelta(days=7), self.now)
        if arg == "30d":
            return Period("30d", self.now - timedelta(days=30), self.now)
        start = self.now.replace(hour=0, minute=0, second=0, microsecond=0)
        return Period("today", start, self.now)


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


def row_get(row: Any, key: str, default: Any = None) -> Any:
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        return row[key]
    except Exception:
        return default


def condition_line(condition: Any) -> str:
    if not isinstance(condition, dict):
        return str(condition)
    name = condition.get("display_name") or condition.get("condition_key") or "Unknown"
    lhs = fmt_value(condition.get("lhs_value"))
    rhs = fmt_value(condition.get("rhs_value"))
    operator = condition.get("operator") or ""
    if lhs == "n/a" and rhs == "n/a":
        return str(name)
    if rhs == "n/a":
        return f"{name}: {lhs} {operator}".strip()
    return f"{name}: {lhs} {operator} {rhs}".strip()


def blocking_suffix(condition: Any) -> str:
    return " [BLOCKING]" if isinstance(condition, dict) and condition.get("is_blocking") else ""


def display_gate(gate: Any, blocking_conditions: list[Any]) -> str:
    for condition in blocking_conditions:
        if isinstance(condition, dict) and condition.get("condition_key") == gate:
            return SignalDiagnosticsRecorder.failure_display(condition)
    return str(gate or "unknown")


def failure_condition_line(condition: Any) -> str:
    if not isinstance(condition, dict):
        return str(condition)
    display_condition = dict(condition)
    display_condition["display_name"] = SignalDiagnosticsRecorder.failure_display(condition)
    return condition_line(display_condition)


def short_grade(value: Any) -> str:
    return str(value or "unknown").replace(" Alert", "").replace(" Signal", "")


def fmt_value(value: Any) -> str:
    if value is None or value == "":
        return "n/a"
    if isinstance(value, (int, float)):
        return f"{float(value):.2f}"
    return str(value)


def fmt_number(value: Optional[float]) -> str:
    return "n/a" if value is None else f"{float(value):.2f}"


def avg(values: list[float]) -> Optional[float]:
    return sum(values) / len(values) if values else None


def median(values: list[float]) -> Optional[float]:
    return statistics.median(values) if values else None


def percentile(values: list[float], pct: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * pct / 100
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = index - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction
