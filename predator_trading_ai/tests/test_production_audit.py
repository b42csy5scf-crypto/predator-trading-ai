from __future__ import annotations

from datetime import datetime, timedelta, timezone

from predator_trading_ai.config import Settings
from predator_trading_ai.database.db import Database
from predator_trading_ai.reports.production_audit import ProductionAuditReport


NOW = datetime(2026, 7, 21, 15, 0, tzinfo=timezone.utc)


def make_settings(tmp_path, **overrides) -> Settings:
    settings = Settings()
    settings.database_url = f"sqlite:///{tmp_path / 'production_audit.db'}"
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


def iso_at(minutes: int = 0) -> str:
    return (NOW + timedelta(minutes=minutes)).isoformat()


def insert_rejected(
    db: Database,
    *,
    ticker: str,
    score: float,
    computed_grade: str,
    reason: str,
    gate: str,
    raw_metrics: dict | None = None,
    created_at: str | None = None,
    extra: dict | None = None,
) -> None:
    blocking = [
        {
            "condition_key": gate,
            "display_name": "SPY or QQQ healthy" if gate == "spy_or_qqq_healthy" else "Relative volume confirmed",
            "result": "FAIL",
            "is_blocking": True,
        }
    ]
    payload = {
            "created_at": created_at or iso_at(),
            "ticker": ticker,
            "final_score": score,
            "computed_grade": computed_grade,
            "first_rejection_gate": gate,
            "rejection_reasons_json": [reason],
            "conditions_passed_json": [],
            "conditions_failed_json": [reason],
            "diagnostics_format_version": 2,
            "evaluated_conditions_json": blocking,
            "passed_conditions_v2_json": [],
            "failed_conditions_v2_json": blocking,
            "blocking_conditions_json": blocking,
            "actual_first_blocking_gate": gate,
            "why_not_trade": reason,
            "raw_metrics_json": raw_metrics or {},
    }
    payload.update(extra or {})
    db.insert_dict("rejected_candidate_diagnostics", payload)


def insert_accepted_with_quote(db: Database, *, ticker: str, created_at: str, raw_metrics: dict) -> None:
    db.insert_dict(
        "signal_diagnostics",
        {
            "created_at": created_at,
            "ticker": ticker,
            "grade": "A+ Signal",
            "alert_type": "trade_candidate",
            "score": 70,
            "entry_zone_low": 100,
            "entry_zone_high": 101,
            "stop_loss": 98,
            "tp1": 103,
            "tp2": 105,
            "tp3": 107,
            "relative_volume": raw_metrics.get("relative_volume"),
            "spread_at_entry": raw_metrics.get("spread_pct"),
            "entry_volume": raw_metrics.get("volume"),
            "telegram_note": "test",
            "scoring_components_json": [],
            "raw_metrics_json": raw_metrics,
        },
    )


def test_grade_trace_score_mapping_and_no_invented_market_grade(tmp_path) -> None:
    settings, db = make_db(tmp_path)
    insert_rejected(
        db,
        ticker="UNH",
        score=90.6,
        computed_grade="A++ Signal",
        reason="spread too wide: 5.73%; liquidity score too low: 0",
        gate="risk_engine",
        raw_metrics={"spy_trend": "bull", "qqq_trend": "bear", "regime": "bull"},
        extra={
            "raw_score": 90.6,
            "setup_grade": "A++ Signal",
            "eligibility_status": "BLOCKED_BY_RISK",
            "eligibility_stage": "risk_engine",
            "block_reason_code": "SPREAD_TOO_WIDE",
            "block_reason_display": "spread too wide: 5.73%; liquidity score too low: 0",
            "final_acceptance_status": "REJECTED",
            "displayed_grade_legacy": "A++ Signal",
            "classification_format_version": 2,
        },
    )
    insert_rejected(
        db,
        ticker="COP",
        score=74.7,
        computed_grade="B Watch Alert",
        reason="SPY/QQQ not healthy for B alert",
        gate="spy_or_qqq_healthy",
        raw_metrics={"spy_trend": "bear", "qqq_trend": "bear", "regime": "bear"},
        created_at=iso_at(-1),
    )
    insert_rejected(
        db,
        ticker="CVX",
        score=70.5,
        computed_grade="B Watch Alert",
        reason="MACD momentum not improving",
        gate="macd_momentum_improving",
        raw_metrics={"spy_trend": "bull", "qqq_trend": "bear", "regime": "choppy"},
        created_at=iso_at(-2),
    )

    report = ProductionAuditReport(settings, db).grade_trace(limit=3)

    assert "No separate market-adjusted grade exists" in report
    assert "UNH | score 90.6 | SCORE_GRADE_MATCH" in report
    assert "Score grade: A++ Signal" in report
    assert "COP | score 74.7 | INTENTIONAL_POLICY_DOWNGRADE" in report
    assert "CVX | score 70.5 | REPORTING_STAGE_MISMATCH" in report
    assert "Thresholds: B>=58 A>=58 A+>=65 A++>=75" in report
    assert "Note: B and A share the same numeric threshold" in report
    assert "Setup grade: A++ Signal" in report
    assert "Eligibility: BLOCKED_BY_RISK @ risk_engine" in report
    assert "Block code: SPREAD_TOO_WIDE" in report
    assert "Classification version: 2" in report
    assert "Telegram reached: NO" in report
    assert "Tracker add reached: NO" in report


def test_grade_trace_read_only() -> None:
    class SelectOnlyDB:
        def fetch_all(self, sql, params=()):
            assert sql.strip().upper().startswith("SELECT")
            return []

    report = ProductionAuditReport(Settings(), SelectOnlyDB()).grade_trace()

    assert "No verified diagnostics" in report


def test_spread_forensics_valid_bid_ask_and_quote_age(tmp_path) -> None:
    settings, db = make_db(tmp_path)
    insert_rejected(
        db,
        ticker="AAPL",
        score=68,
        computed_grade="A+ Signal",
        reason="spread too wide: 0.50%",
        gate="risk_engine",
        raw_metrics={
            "bid": 100,
            "ask": 101,
            "last_price": 100.5,
            "quote_timestamp": (NOW - timedelta(seconds=45)).isoformat(),
            "data_source": "alpaca",
            "data_feed": "IEX",
            "market_status": "OPEN",
            "liquidity_score": 80,
            "volume": 1_500_000,
            "relative_volume": 1.2,
            "alpaca_api_key": "SECRET",
        },
        extra={
            "raw_bid": 100,
            "raw_ask": 101,
            "last_trade_price": 100.5,
            "quote_timestamp": (NOW - timedelta(seconds=45)).isoformat(),
            "evaluation_timestamp": NOW.isoformat(),
            "quote_age_seconds": 45,
            "quote_source": "alpaca",
            "feed_name": "IEX",
            "feed_type": "feed_native",
            "spread_percentage": 1.0,
            "liquidity_score_at_evaluation": 80,
            "liquidity_score_status": "MEASURED",
            "quote_validity_status": "VALID",
            "quote_validity_reasons": [],
        },
    )

    report = ProductionAuditReport(settings, db).spread_forensics("AAPL")

    assert "Formula: ((ask - bid) / ((bid + ask) / 2)) * 100" in report
    assert "Bid/Ask/Last: 100.00 / 101.00 / 100.50" in report
    assert "Spread: 1.00 (1.00%)" in report
    assert "Source/feed: alpaca / IEX" in report
    assert "Quote validity: VALID reasons=none" in report
    assert "Quote anomaly: none" in report
    assert "Status: MEASURED" in report
    assert "Quote ts/age:" in report
    assert "45s" in report
    assert "Raw quote forensics available." in report
    assert "SECRET" not in report


def test_spread_forensics_stale_missing_invalid_quotes_and_closed_market(tmp_path) -> None:
    settings, db = make_db(tmp_path)
    insert_rejected(
        db,
        ticker="UNH",
        score=90.6,
        computed_grade="A++ Signal",
        reason="spread too wide: 5.73%; liquidity score too low: 0",
        gate="risk_engine",
        raw_metrics={"market_status": "CLOSED", "quote_timestamp": (NOW - timedelta(minutes=20)).isoformat()},
    )
    insert_rejected(
        db,
        ticker="UNH",
        score=72,
        computed_grade="A+ Signal",
        reason="spread too wide: 2.00%",
        gate="risk_engine",
        raw_metrics={"bid": 0, "ask": -1, "last_price": 300, "quote_timestamp": NOW.isoformat()},
        created_at=iso_at(-1),
    )
    insert_rejected(
        db,
        ticker="UNH",
        score=71,
        computed_grade="A+ Signal",
        reason="spread too wide: 2.00%",
        gate="risk_engine",
        raw_metrics={"bid": 101, "ask": 100, "last_price": 100, "quote_timestamp": NOW.isoformat()},
        created_at=iso_at(-2),
    )

    report = ProductionAuditReport(settings, db).spread_forensics("UNH", limit=3)

    assert "Market: CLOSED" in report
    assert "stale=True" in report
    assert "missing_bid=True" in report
    assert "bid<=0=True" in report
    assert "ask<=0=True" in report
    assert "ask<bid=True" in report
    assert "Historical row — raw quote forensics unavailable." in report


def test_spread_forensics_historical_row_without_raw_quote_data(tmp_path) -> None:
    settings, db = make_db(tmp_path)
    insert_rejected(
        db,
        ticker="UNH",
        score=90.6,
        computed_grade="A++ Signal",
        reason="spread too wide: 5.73%; liquidity score too low: 0",
        gate="risk_engine",
        raw_metrics={"relative_volume": 1.0},
    )

    report = ProductionAuditReport(settings, db).spread_forensics("UNH")

    assert "Bid/Ask/Last: n/a / n/a / n/a" in report
    assert "Spread: n/a (5.73%)" in report
    assert "Historical row — raw quote forensics unavailable." in report


def test_spread_forensics_reads_accepted_signal_rows(tmp_path) -> None:
    settings, db = make_db(tmp_path)
    insert_accepted_with_quote(
        db,
        ticker="MSFT",
        created_at=iso_at(),
        raw_metrics={
            "bid": 200,
            "ask": 200.4,
            "last_price": 200.2,
            "quote_timestamp": NOW.isoformat(),
            "spread_pct": 0.2,
            "relative_volume": 1.1,
            "volume": 900_000,
        },
    )

    report = ProductionAuditReport(settings, db).spread_forensics("MSFT")

    assert "accepted_signal" in report
    assert "Bid/Ask/Last: 200.00 / 200.40 / 200.20" in report


def test_spread_forensics_read_only() -> None:
    class SelectOnlyDB:
        def __init__(self) -> None:
            self.calls = 0

        def fetch_all(self, sql, params=()):
            self.calls += 1
            assert sql.strip().upper().startswith("SELECT")
            return []

    db = SelectOnlyDB()
    report = ProductionAuditReport(Settings(), db).spread_forensics("UNH")

    assert "No diagnostics rows found" in report
    assert db.calls == 2
