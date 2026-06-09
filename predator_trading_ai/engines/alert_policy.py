from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from predator_trading_ai.config import Settings
from predator_trading_ai.database.db import Database
from predator_trading_ai.engines.regime_detector import MarketRegime


EASTERN = ZoneInfo("America/New_York")
TOTAL_KEY = "__TOTAL__"
GRADE_RANK = {
    "C Risky/Early Alert": 0,
    "B Watch Alert": 1,
    "A Signal": 2,
    "A+ Signal": 3,
    "A++ Signal": 4,
}


@dataclass(frozen=True)
class AlertDecision:
    allowed: bool
    reason: str


class AlertPolicy:
    def __init__(self, settings: Settings, db: Database) -> None:
        self.settings = settings
        self.db = db

    def evaluate(
        self,
        ticker: str,
        grade: str,
        score: float,
        regime: MarketRegime,
        now: Optional[datetime] = None,
    ) -> AlertDecision:
        rank = GRADE_RANK.get(grade, -1)
        if rank < GRADE_RANK["B Watch Alert"]:
            return AlertDecision(False, "C-grade and unrecognized alerts are Telegram-disabled")
        if regime.regime in {"panic", "high-volatility"} or regime.regime_severity in {"severe", "panic"}:
            return AlertDecision(False, f"{regime.regime_severity} {regime.regime} regime is blocked")
        if grade == "B Watch Alert" and score < self.settings.min_score_b:
            return AlertDecision(False, f"B score below strong-watch threshold: {score:.0f}")
        strong_b_exception = (
            grade == "B Watch Alert"
            and score >= self.settings.min_score_b
            and (regime.regime == "choppy" or regime.regime_severity == "moderate")
        )
        if self.is_weak_market(regime) and rank < GRADE_RANK["A+ Signal"] and not strong_b_exception:
            return AlertDecision(False, f"{regime.regime} market requires A+ or A++")

        alert_date = self.alert_date(now)
        total = self._row(alert_date, TOTAL_KEY)
        ticker_row = self._row(alert_date, ticker)
        previous_rank = int(ticker_row["highest_grade_rank"]) if ticker_row else -1
        is_upgrade = ticker_row is not None and rank > previous_rank

        if ticker_row and not is_upgrade:
            previous_grade = ticker_row["highest_grade"] or "unknown"
            return AlertDecision(False, f"ticker already alerted at {previous_grade}; grade did not improve")
        if total and int(total["alert_count"]) >= self.settings.max_alerts_per_day:
            return AlertDecision(False, "maximum daily alert count reached")
        if (
            ticker_row
            and int(ticker_row["alert_count"]) >= self.settings.max_alerts_per_ticker_per_day
            and not is_upgrade
        ):
            return AlertDecision(False, "maximum ticker alerts reached")
        return AlertDecision(True, "grade upgrade" if is_upgrade else "alert policy passed")

    def record(self, ticker: str, grade: str, now: Optional[datetime] = None) -> None:
        alert_date = self.alert_date(now)
        rank = GRADE_RANK[grade]
        timestamp = (now or datetime.now(EASTERN)).isoformat()
        self._upsert(alert_date, TOTAL_KEY, grade, rank, timestamp)
        self._upsert(alert_date, ticker, grade, rank, timestamp)

    def _upsert(self, alert_date: str, ticker: str, grade: str, rank: int, timestamp: str) -> None:
        self.db.execute(
            """
            INSERT INTO alert_daily_limits (
                alert_date, ticker, alert_count, highest_grade, highest_grade_rank, last_alert_at
            )
            VALUES (?, ?, 1, ?, ?, ?)
            ON CONFLICT(alert_date, ticker) DO UPDATE SET
                alert_count = alert_count + 1,
                highest_grade = CASE
                    WHEN excluded.highest_grade_rank > highest_grade_rank THEN excluded.highest_grade
                    ELSE highest_grade
                END,
                highest_grade_rank = MAX(highest_grade_rank, excluded.highest_grade_rank),
                last_alert_at = excluded.last_alert_at
            """,
            [alert_date, ticker, grade, rank, timestamp],
        )

    def _row(self, alert_date: str, ticker: str):
        rows = self.db.fetch_all(
            "SELECT * FROM alert_daily_limits WHERE alert_date = ? AND ticker = ?",
            [alert_date, ticker],
        )
        return rows[0] if rows else None

    @staticmethod
    def is_weak_market(regime: MarketRegime) -> bool:
        return regime.regime in {"choppy", "low-volume", "weak-breadth", "bear", "bear-trend"}

    @staticmethod
    def alert_date(now: Optional[datetime] = None) -> str:
        current = now or datetime.now(EASTERN)
        if current.tzinfo is None:
            current = current.replace(tzinfo=EASTERN)
        return current.astimezone(EASTERN).date().isoformat()
