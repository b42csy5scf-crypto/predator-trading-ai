from __future__ import annotations

from datetime import datetime, timedelta, timezone

from predator_trading_ai.config import Settings
from predator_trading_ai.database.db import Database
from predator_trading_ai.reports.rejection_insights import RejectionInsightsReport


NOW = datetime(2026, 7, 20, 15, 0, tzinfo=timezone.utc)


def make_settings(tmp_path, **overrides) -> Settings:
    settings = Settings()
    settings.database_url = f"sqlite:///{tmp_path / 'rejection_insights.db'}"
    settings.min_score_a_plus_plus = 75
    settings.min_score_a_plus = 65
    settings.min_score_a = 58
    settings.min_score_b = 58
    for key, value in overrides.items():
        setattr(settings, key, value)
    return settings


def make_db(tmp_path) -> tuple[Settings, Database]:
    settings = make_settings(tmp_path)
    db = Database(settings)
    db.initialize()
    return settings, db


def iso_at(days: int = 0, minutes: int = 0) -> str:
    return (NOW + timedelta(days=days, minutes=minutes)).isoformat()


def passed_conditions() -> list[dict[str, object]]:
    return [
        {
            "condition_key": "price_above_ema50",
            "display_name": "Price above EMA50",
            "lhs_value": 212.4,
            "operator": ">",
            "rhs_value": 209.8,
            "result": "PASS",
            "is_blocking": False,
        },
        {
            "condition_key": "ema50_above_ema200",
            "display_name": "EMA50 above EMA200",
            "lhs_value": 209.8,
            "operator": ">",
            "rhs_value": 190.2,
            "result": "PASS",
            "is_blocking": False,
        },
        {
            "condition_key": "rsi_between_45_65",
            "display_name": "RSI between 45 and 65",
            "lhs_value": 55,
            "operator": "between",
            "rhs_value": "45-65",
            "result": "PASS",
            "is_blocking": False,
        },
    ]


def failed_conditions(blocking: bool = True) -> list[dict[str, object]]:
    return [
        {
            "condition_key": "relative_volume_confirmed",
            "display_name": "Relative volume confirmed",
            "lhs_value": 0.74,
            "operator": ">=",
            "rhs_value": 0.8,
            "result": "FAIL",
            "is_blocking": blocking,
        },
        {
            "condition_key": "macd_momentum_improving",
            "display_name": "MACD momentum improving",
            "lhs_value": -0.12,
            "operator": ">",
            "rhs_value": 0.0,
            "result": "FAIL",
            "is_blocking": False,
        },
    ]


def insert_rejected(
    db: Database,
    ticker: str,
    score: float,
    created_at: str,
    version: int = 2,
    gate: str = "relative_volume_confirmed",
) -> None:
    passed = passed_conditions() if version >= 2 else []
    failed = failed_conditions() if version >= 2 else []
    blocking = [failed[0]] if failed else []
    db.insert_dict(
        "rejected_candidate_diagnostics",
        {
            "created_at": created_at,
            "ticker": ticker,
            "final_score": score,
            "computed_grade": "B Watch Alert",
            "first_rejection_gate": "risk_filter",
            "rejection_reasons_json": ["Relative volume below threshold"],
            "conditions_passed_json": ["price above EMA50"],
            "conditions_failed_json": ["relative volume below threshold"],
            "diagnostics_format_version": version,
            "evaluated_conditions_json": [*passed, *failed],
            "passed_conditions_v2_json": passed,
            "failed_conditions_v2_json": failed,
            "blocking_conditions_json": blocking,
            "actual_first_blocking_gate": gate,
            "why_not_trade": "blocked by policy",
            "breakout_distance_atr": 0.6,
            "distance_from_ema21": 1.2,
            "distance_from_ema50": 2.0,
            "distance_from_recent_swing_low": 3.1,
            "stop_to_swing_low_distance": 0.4,
            "bars_since_breakout": 2,
            "entry_open": 210,
            "entry_high": 213,
            "entry_low": 209,
            "entry_close": 212,
            "entry_volume": 1_000_000,
            "previous_open": 208,
            "previous_high": 211,
            "previous_low": 207,
            "previous_close": 210,
            "previous_volume": 900_000,
            "gap_flag": 0,
            "raw_metrics_json": {"relative_volume": 0.74},
        },
    )


def insert_accepted(db: Database, ticker: str, grade: str, alert_type: str, score: float, created_at: str) -> None:
    db.insert_dict(
        "signal_diagnostics",
        {
            "created_at": created_at,
            "ticker": ticker,
            "grade": grade,
            "alert_type": alert_type,
            "score": score,
            "entry_zone_low": 100,
            "entry_zone_high": 101,
            "stop_loss": 98,
            "tp1": 103,
            "tp2": 105,
            "tp3": 107,
            "scoring_components_json": [],
            "raw_metrics_json": {},
        },
    )


def test_rejected_examples_empty_database(tmp_path) -> None:
    settings, db = make_db(tmp_path)
    report = RejectionInsightsReport(settings, db, now=NOW).rejected_examples()

    assert "No verified diagnostics v2 rows" in report


def test_rejected_examples_defaults_to_latest_10_verified_v2_only(tmp_path) -> None:
    settings, db = make_db(tmp_path)
    insert_rejected(db, "LEGACY", 77, iso_at(minutes=-30), version=1)
    for idx in range(12):
        insert_rejected(db, f"T{idx}", 50 + idx, iso_at(minutes=idx), version=2)

    report = RejectionInsightsReport(settings, db, now=NOW).rejected_examples()

    assert "latest 10" in report
    assert "LEGACY" not in report
    assert "T11" in report
    assert "T0" not in report


def test_rejected_examples_custom_limit_and_max_25(tmp_path) -> None:
    settings, db = make_db(tmp_path)
    for idx in range(30):
        insert_rejected(db, f"X{idx}", 50 + idx, iso_at(minutes=idx), version=2)

    report_20 = RejectionInsightsReport(settings, db, now=NOW).rejected_examples(limit=20)
    report_max = RejectionInsightsReport(settings, db, now=NOW).rejected_examples(limit=100)

    assert "latest 20" in report_20
    assert "latest 25" in report_max


def test_rejected_examples_shows_pass_fail_raw_values_and_first_gate(tmp_path) -> None:
    settings, db = make_db(tmp_path)
    insert_rejected(db, "NVDA", 57, iso_at(), version=2)

    report = RejectionInsightsReport(settings, db, now=NOW).rejected_examples()

    assert "First blocking gate: Relative volume not confirmed" in report
    assert "✅ Price above EMA50: 212.40 > 209.80" in report
    assert "❌ Relative volume not confirmed: 0.74 >= 0.80 [BLOCKING]" in report
    assert "Counts: pass=3 fail=2 block=1" in report


def test_score_distribution_today_counts_buckets_and_accepted_groups(tmp_path) -> None:
    settings, db = make_db(tmp_path)
    for idx, (ticker, score) in enumerate([("A", 49), ("B", 52), ("C", 56), ("D", 59), ("E", 63), ("F", 68), ("G", 75), ("H", 82)]):
        insert_rejected(db, ticker, score, iso_at(minutes=-idx), version=2)
    insert_accepted(db, "MSFT", "A++ Signal", "trade_candidate", 80, iso_at())
    insert_accepted(db, "AAPL", "A Signal", "trade_candidate", 60, iso_at())
    insert_accepted(db, "HD", "B Watch Alert", "experimental_watch", 59, iso_at())

    report = RejectionInsightsReport(settings, db, now=NOW).score_distribution("today")

    assert "Score Distribution (today)" in report
    assert "Rejected candidates: 8" in report
    assert "Accepted A/A+/A++: 2" in report
    assert "Strong B: 1" in report
    assert "- below 50: 1" in report
    assert "- 50.00-54.99: 1" in report
    assert "- 55.00-57.99: 1" in report
    assert "- 80+: 1" in report
    assert "90th percentile:" in report


def test_score_distribution_periods_and_today_baseline(tmp_path) -> None:
    settings, db = make_db(tmp_path)
    insert_rejected(db, "TODAY", 61, iso_at(), version=2)
    insert_rejected(db, "BASE", 54, (NOW - timedelta(days=2)).isoformat(), version=2)
    insert_rejected(db, "OLD", 90, (NOW - timedelta(days=20)).isoformat(), version=2)

    today = RejectionInsightsReport(settings, db, now=NOW).score_distribution("today")
    seven = RejectionInsightsReport(settings, db, now=NOW).score_distribution("7d")
    thirty = RejectionInsightsReport(settings, db, now=NOW).score_distribution("30d")

    assert "Today vs baseline:" in today
    assert "Today's scores are" in today
    assert "Score Distribution (7d)" in seven
    assert "Rejected candidates: 2" in seven
    assert "Score Distribution (30d)" in thirty
    assert "Rejected candidates: 3" in thirty


def test_score_distribution_near_min_b_and_top_almost_trades(tmp_path) -> None:
    settings, db = make_db(tmp_path)
    for idx, (ticker, score) in enumerate([("QCOM", 57.5), ("AVGO", 56.1), ("META", 58.9)]):
        insert_rejected(db, ticker, score, iso_at(minutes=-idx), version=2)

    report = RejectionInsightsReport(settings, db, now=NOW).score_distribution("today")

    assert "Within 1 point(s) of MIN_SCORE_B: 1" in report
    assert "Top almost-trades" in report
    assert "- META 58.9" in report
    assert "Score grade: A | Setup: A | Displayed: B Watch" in report
    assert "Blocked by: Relative volume not confirmed" in report


def test_score_distribution_preserves_legacy_counts(tmp_path) -> None:
    settings, db = make_db(tmp_path)
    insert_rejected(db, "V2", 62, iso_at(), version=2)
    insert_rejected(db, "LEGACY", 61, iso_at(minutes=-1), version=1)

    report = RejectionInsightsReport(settings, db, now=NOW).score_distribution("today")

    assert "Verified v2 rows: 1" in report
    assert "Legacy score-only rows: 1" in report


def test_rejection_insights_report_is_select_only() -> None:
    class SelectOnlyDB:
        def __init__(self) -> None:
            self.sql: list[str] = []

        def fetch_all(self, sql, params=()):
            self.sql.append(sql.strip())
            assert sql.strip().upper().startswith("SELECT")
            return []

    db = SelectOnlyDB()
    settings = Settings()
    report = RejectionInsightsReport(settings, db, now=NOW)

    report.rejected_examples()
    report.score_distribution("today")

    assert len(db.sql) == 4
