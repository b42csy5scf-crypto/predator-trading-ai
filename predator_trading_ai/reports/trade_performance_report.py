from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Optional

from predator_trading_ai.database.db import Database
from predator_trading_ai.utils.watchlist import SECTOR_BY_TICKER


GRADE_ORDER = ["A++ Signal", "A+ Signal", "A Signal", "B Watch Alert"]
SCORE_BUCKETS = [
    ("50-55", 50, 55),
    ("55-60", 55, 60),
    ("60-65", 60, 65),
    ("65-75", 65, 75),
    ("75+", 75, 101),
]
LOSS_REASON_KEYWORDS = {
    "weak volume": ("weak volume", "low volume", "volume not confirmed", "volume too quiet", "relative volume too low"),
    "bear regime": ("bear", "reduced confidence"),
    "late breakout": ("late breakout", "extended", "already probing breakout"),
    "high volatility": ("high volatility", "atr above", "volatility too high", "panic"),
    "low liquidity": ("low liquidity", "liquidity score too low", "spread too wide", "illiquidity"),
}


@dataclass(frozen=True)
class SignalOutcome:
    ticker: str
    grade: str
    score: Optional[float]
    regime: str
    sector: str
    status: str
    r_multiple: float
    won: bool
    lost: bool
    loss_context: str


class TradePerformanceReport:
    def __init__(self, db: Database) -> None:
        self.db = db

    def build(self) -> str:
        outcomes = self.load_outcomes()
        if not outcomes:
            return "Predator Trading AI Performance Analytics\nNo completed signal outcomes found yet."

        sections = [
            "Predator Trading AI Performance Analytics",
            "",
            self._section("By Grade", self._metrics_by_grade(outcomes)),
            self._section("By Score Range", self._metrics_by_score(outcomes)),
            self._section("By Regime", self._metrics_by_regime(outcomes)),
            self._section("Best Tickers", self._ticker_table(outcomes, best=True)),
            self._section("Worst Tickers", self._ticker_table(outcomes, best=False)),
            self._section("By Sector", self._metrics_by_sector(outcomes)),
            self._section("Common Loss Reasons", self._loss_reasons(outcomes)),
            self._section("Recommendations", self._recommendations(outcomes)),
        ]
        return "\n".join(sections)

    def load_outcomes(self) -> list[SignalOutcome]:
        active_rows = self.db.fetch_all(
            """
            SELECT *
            FROM active_signals
            WHERE status = 'closed'
              AND close_reason IN ('tp3_completed', 'invalidated')
            ORDER BY sent_at
            """
        )
        sent_rows = self.db.fetch_all("SELECT * FROM sent_alerts ORDER BY created_at")
        shadow_rows = self.db.fetch_all("SELECT * FROM shadow_signals ORDER BY created_at")
        rejected_rows = self.db.fetch_all("SELECT * FROM rejected_signals ORDER BY created_at")

        outcomes: list[SignalOutcome] = []
        for row in active_rows:
            alert = self._match_sent_alert(row, sent_rows)
            shadow_context = self._nearest_context(row["ticker"], row["sent_at"], shadow_rows, rejected_rows)
            r_multiple = self._r_multiple(row)
            lost = row["close_reason"] == "invalidated"
            outcomes.append(
                SignalOutcome(
                    ticker=row["ticker"],
                    grade=row["grade"],
                    score=float(alert["score"]) if alert and alert["score"] is not None else None,
                    regime=str(alert["regime"] if alert and alert["regime"] else "unknown"),
                    sector=SECTOR_BY_TICKER.get(str(row["ticker"]).upper(), "Unknown"),
                    status=row["close_reason"],
                    r_multiple=r_multiple,
                    won=r_multiple > 0,
                    lost=lost,
                    loss_context=" ".join(
                        part
                        for part in [
                            str(alert["message"] if alert else ""),
                            shadow_context,
                            str(row["close_reason"] or ""),
                        ]
                        if part
                    ).lower(),
                )
            )
        return outcomes

    def _metrics_by_grade(self, outcomes: list[SignalOutcome]) -> list[str]:
        lines = [self._header("Grade")]
        for grade in GRADE_ORDER:
            lines.append(self._metric_line(grade, [item for item in outcomes if item.grade == grade]))
        return lines

    def _metrics_by_score(self, outcomes: list[SignalOutcome]) -> list[str]:
        lines = [self._header("Score")]
        for label, low, high in SCORE_BUCKETS:
            bucket = [
                item
                for item in outcomes
                if item.score is not None and low <= item.score < high
            ]
            lines.append(self._metric_line(label, bucket))
        return lines

    def _metrics_by_regime(self, outcomes: list[SignalOutcome]) -> list[str]:
        categories = {
            "Bull": [],
            "Moderate Bear": [],
            "Choppy": [],
            "Panic": [],
        }
        for item in outcomes:
            categories[self._regime_bucket(item.regime)].append(item)
        return [self._header("Regime"), *[self._metric_line(name, rows) for name, rows in categories.items()]]

    def _metrics_by_sector(self, outcomes: list[SignalOutcome]) -> list[str]:
        groups: dict[str, list[SignalOutcome]] = defaultdict(list)
        for item in outcomes:
            groups[item.sector].append(item)
        lines = [self._header("Sector")]
        for sector in sorted(groups):
            lines.append(self._metric_line(sector, groups[sector]))
        return lines

    def _ticker_table(self, outcomes: list[SignalOutcome], best: bool) -> list[str]:
        groups: dict[str, list[SignalOutcome]] = defaultdict(list)
        for item in outcomes:
            groups[item.ticker].append(item)
        ranked = sorted(
            groups.items(),
            key=lambda pair: (self._win_rate(pair[1]), self._avg_r(pair[1]), len(pair[1])),
            reverse=best,
        )
        lines = [self._header("Ticker")]
        for ticker, rows in ranked[:5]:
            lines.append(self._metric_line(ticker, rows))
        return lines

    def _loss_reasons(self, outcomes: list[SignalOutcome]) -> list[str]:
        counter: Counter[str] = Counter()
        for item in outcomes:
            if not item.lost:
                continue
            matched = False
            for reason, keywords in LOSS_REASON_KEYWORDS.items():
                if any(keyword in item.loss_context for keyword in keywords):
                    counter[reason] += 1
                    matched = True
            if not matched:
                counter["unclear / needs more samples"] += 1
        if not counter:
            return ["No losses recorded."]
        return [f"{reason}: {count}" for reason, count in counter.most_common()]

    def _recommendations(self, outcomes: list[SignalOutcome]) -> list[str]:
        recommendations: list[str] = []
        loss_counts = Counter()
        for line in self._loss_reasons(outcomes):
            if ":" in line:
                key, raw_count = line.split(":", 1)
                try:
                    loss_counts[key] = int(raw_count.strip())
                except ValueError:
                    continue
        if loss_counts.get("weak volume", 0) > 0:
            recommendations.append("Review volume confirmation on losing setups before raising alert frequency.")
        if loss_counts.get("bear regime", 0) > 0:
            recommendations.append("Keep bear-regime alerts observation-only unless A+/A++ results improve.")
        if loss_counts.get("late breakout", 0) > 0:
            recommendations.append("Flag extended breakouts separately and compare their R multiple against early setups.")
        if loss_counts.get("high volatility", 0) > 0:
            recommendations.append("Consider stricter volatility warnings during high ATR/VIX periods.")
        if loss_counts.get("low liquidity", 0) > 0:
            recommendations.append("Keep spread and liquidity filters active; low-liquidity losses are execution-sensitive.")
        if not recommendations:
            recommendations.append("No strategy change recommended yet; collect more closed TP/SL outcomes.")
        recommendations.append("Do not auto-activate improvements without separate backtesting.")
        return recommendations

    @staticmethod
    def _header(label: str) -> str:
        return f"{label:<18} Total  Wins  Losses  WinRate  AvgR"

    def _metric_line(self, label: str, rows: list[SignalOutcome]) -> str:
        total = len(rows)
        wins = len([item for item in rows if item.won])
        losses = len([item for item in rows if item.lost])
        win_rate = self._win_rate(rows)
        avg_r = self._avg_r(rows)
        return f"{label:<18} {total:>5}  {wins:>4}  {losses:>6}  {win_rate:>6.1f}%  {avg_r:>5.2f}"

    @staticmethod
    def _section(title: str, lines: Iterable[str]) -> str:
        return "\n".join([title, *lines])

    @staticmethod
    def _win_rate(rows: list[SignalOutcome]) -> float:
        return (len([item for item in rows if item.won]) / len(rows) * 100) if rows else 0.0

    @staticmethod
    def _avg_r(rows: list[SignalOutcome]) -> float:
        return sum(item.r_multiple for item in rows) / len(rows) if rows else 0.0

    @staticmethod
    def _r_multiple(row) -> float:
        entry = (float(row["entry_zone_low"]) + float(row["entry_zone_high"])) / 2
        risk = abs(entry - float(row["stop_loss"]))
        if risk <= 0:
            return 0.0
        if row["close_reason"] == "invalidated":
            return -1.0
        if row["close_reason"] == "tp3_completed":
            return round(abs(float(row["tp3"]) - entry) / risk, 3)
        if int(row["tp2_hit"] or 0):
            return round(abs(float(row["tp2"]) - entry) / risk, 3)
        if int(row["tp1_hit"] or 0):
            return round(abs(float(row["tp1"]) - entry) / risk, 3)
        return 0.0

    @staticmethod
    def _regime_bucket(regime: str) -> str:
        value = (regime or "").lower()
        if "panic" in value or "high-volatility" in value:
            return "Panic"
        if "bear" in value:
            return "Moderate Bear"
        if "choppy" in value or "low-volume" in value or "weak-breadth" in value:
            return "Choppy"
        return "Bull"

    def _match_sent_alert(self, active_row, sent_rows):
        candidates = [
            row
            for row in sent_rows
            if row["ticker"] == active_row["ticker"] and row["grade"] == active_row["grade"]
        ]
        if not candidates:
            return None
        sent_at = self._parse_dt(active_row["sent_at"])
        if sent_at is None:
            return candidates[-1]
        before = [
            row
            for row in candidates
            if self._sort_key(row["created_at"]) <= self._sort_key(active_row["sent_at"])
        ]
        return before[-1] if before else candidates[-1]

    def _nearest_context(self, ticker: str, sent_at: str, shadow_rows, rejected_rows) -> str:
        contexts: list[str] = []
        for row in shadow_rows:
            if row["ticker"] != ticker:
                continue
            contexts.extend(
                str(row[key] or "")
                for key in (
                    "rejection_reason",
                    "regime",
                    "regime_reason",
                    "volume_condition",
                    "trend_condition",
                    "volatility_condition",
                    "correlation_condition",
                )
            )
        for row in rejected_rows:
            if row["ticker"] == ticker:
                contexts.append(str(row["rejection_reason"] or ""))
                contexts.append(str(row["regime"] or ""))
        return " ".join(contexts)

    @staticmethod
    def _parse_dt(value: str) -> Optional[datetime]:
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None

    @classmethod
    def _sort_key(cls, value: str) -> float:
        parsed = cls._parse_dt(value)
        if parsed is None:
            return 0.0
        if parsed.tzinfo is not None:
            return parsed.timestamp()
        return parsed.replace(tzinfo=None).timestamp()
