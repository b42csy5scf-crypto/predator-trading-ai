import json
from pathlib import Path

from predator_trading_ai.config import Settings
from predator_trading_ai.database.db import Database
from predator_trading_ai.reports.research_report import ResearchReport
from predator_trading_ai.reports.research_report_runner import ResearchReportRunner


BASE_TIME = "2026-07-16T14:30:00+00:00"


def make_db(tmp_path) -> Database:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'research.db'}")
    db = Database(settings)
    db.initialize()
    return db


def table_counts(db: Database) -> dict[str, int]:
    tables = [
        "active_signals",
        "signal_diagnostics",
        "signal_outcome_diagnostics",
        "price_path",
        "universe_snapshot",
        "rejected_candidate_diagnostics",
        "config_snapshots",
    ]
    return {table: db.fetch_all(f"SELECT COUNT(*) AS count FROM {table}")[0]["count"] for table in tables}


def insert_signal(
    db: Database,
    signal_id: int,
    ticker: str = "NVDA",
    grade: str = "A++ Signal",
    alert_type: str = "trade_candidate",
    final_outcome: str = "TP1",
    final_r: float = 1.0,
    mfe_r: float = 1.25,
    mae_r: float = -0.35,
    score: float = 78.0,
    path: bool = True,
    status: str = "closed",
    holding_seconds: float = 3600.0,
) -> None:
    db.execute(
        """
        INSERT INTO active_signals (
            id, ticker, grade, alert_type, direction, entry_zone_low, entry_zone_high,
            stop_loss, original_stop_loss, tp1, tp2, tp3, sent_at, status,
            tp1_hit, tp2_hit, tp3_hit, close_reason
        )
        VALUES (?, ?, ?, ?, 'long', 99.50, 100.50, 98.00, 98.00, 102.00, 104.00, 106.00,
                ?, ?, ?, ?, ?, ?)
        """,
        [
            signal_id,
            ticker,
            grade,
            alert_type,
            BASE_TIME,
            status,
            1 if mfe_r >= 1 else 0,
            1 if mfe_r >= 2 else 0,
            1 if mfe_r >= 3 else 0,
            final_outcome,
        ],
    )
    db.execute(
        """
        INSERT INTO signal_diagnostics (
            signal_id, active_signal_id, ticker, grade, alert_type, score,
            entry_zone_low, entry_zone_high, stop_loss, tp1, tp2, tp3,
            atr, stop_distance_pct, stop_distance_atr, breakout_distance_atr,
            distance_from_ema21_atr, distance_from_ema50_atr, relative_volume, rsi,
            macd_minus_signal, spy_trend, qqq_trend, regime, breadth_score, sector,
            telegram_note, git_commit_hash, strategy_version, schema_version,
            research_dataset_version, config_hash, distance_from_ema21, distance_from_ema50,
            distance_from_recent_swing_low, stop_to_swing_low_distance, bars_since_breakout,
            entry_open, entry_high, entry_low, entry_close, entry_volume,
            previous_open, previous_high, previous_low, previous_close, previous_volume,
            spy_state, qqq_state, vix_value, spread_at_entry, slippage_proxy, gap_flag,
            minutes_after_market_open, day_of_week, open_positions_count,
            open_positions_same_sector, scoring_components_json, raw_metrics_json
        )
        VALUES (?, ?, ?, ?, ?, ?, 99.50, 100.50, 98.00, 102.00, 104.00, 106.00,
                2.0, 2.0, 1.0, 0.45, 0.30, 0.90, 1.1, 58.0,
                0.25, 'bull', 'bull', 'bull-trend', 65.0, 'Technology',
                'test note', 'abc123', '1.0', 'research-schema-v1.0',
                'v1.0', 'hash-1', 0.6, 1.8, 2.2, 0.4, 3,
                99.0, 101.0, 98.5, 100.0, 1200000,
                98.0, 99.5, 97.5, 99.0, 900000,
                'healthy', 'healthy', 18.0, 0.04, 0.02, 0,
                45.0, 3, 2, 1, ?, ?)
        """,
        [
            signal_id,
            signal_id,
            ticker,
            grade,
            alert_type,
            score,
            json.dumps(["base:+60", "volume:+5"]),
            json.dumps({"regime": "bull-trend"}),
        ],
    )
    db.execute(
        """
        INSERT INTO signal_outcome_diagnostics (
            active_signal_id, ticker, grade, alert_type, direction, entry_price,
            original_stop_loss, risk_per_share, max_favorable_price, max_adverse_price,
            mfe_r, mae_r, current_r, tp1_hit_at, tp2_hit_at, tp3_hit_at, sl_hit_at,
            holding_seconds, final_outcome, exit_reason, exit_price, exit_timestamp,
            realized_r, time_to_025r_seconds, time_to_050r_seconds, time_to_075r_seconds,
            time_to_100r_seconds
        )
        VALUES (?, ?, ?, ?, 'long', 100.00, 98.00, 2.00, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 600, 900, 1200, 1800)
        """,
        [
            signal_id,
            ticker,
            grade,
            alert_type,
            100 + (mfe_r * 2),
            100 + (mae_r * 2),
            mfe_r,
            mae_r,
            final_r,
            BASE_TIME if mfe_r >= 1 else None,
            BASE_TIME if mfe_r >= 2 else None,
            BASE_TIME if mfe_r >= 3 else None,
            BASE_TIME if final_outcome == "SL" else None,
            holding_seconds,
            final_outcome,
            final_outcome,
            100 + (final_r * 2),
            BASE_TIME,
            final_r,
        ],
    )
    if path:
        for idx, value in enumerate((0.0, mfe_r, mae_r, final_r)):
            db.execute(
                """
                INSERT INTO price_path (signal_id, timestamp, price, high, low, event_type)
                VALUES (?, datetime(?, ?), ?, ?, ?, ?)
                """,
                [
                    signal_id,
                    BASE_TIME,
                    f"+{idx * 15} minutes",
                    100 + (value * 2),
                    100 + (value * 2),
                    100 + (value * 2),
                    "scan" if idx < 3 else "exit",
                ],
            )


def insert_rejected(db: Database, ticker: str = "AAPL", score: float = 56.0) -> None:
    db.execute(
        """
        INSERT INTO rejected_candidate_diagnostics (
            ticker, final_score, computed_grade, first_rejection_gate,
            rejection_reasons_json, conditions_passed_json, conditions_failed_json,
            why_not_trade, breakout_distance_atr, distance_from_ema21, distance_from_ema50,
            distance_from_recent_swing_low, stop_to_swing_low_distance, bars_since_breakout,
            entry_open, entry_high, entry_low, entry_close, entry_volume,
            previous_open, previous_high, previous_low, previous_close, previous_volume,
            gap_flag, raw_metrics_json
        )
        VALUES (?, ?, 'B Watch Alert', 'Grade below A', ?, ?, ?, 'score below A',
                0.2, 0.5, 1.4, 2.1, 0.3, 2, 99, 101, 98, 100, 1000000,
                98, 100, 97, 99, 900000, 0, ?)
        """,
        [
            ticker,
            score,
            json.dumps(["Grade below A", "Relative volume below threshold"]),
            json.dumps(["price above EMA50"]),
            json.dumps(["Relative volume below threshold"]),
            json.dumps({"regime": "choppy", "minutes_after_market_open": 45}),
        ],
    )


def test_research_report_empty_database(tmp_path) -> None:
    db = make_db(tmp_path)
    report = ResearchReport(db, days=30).build()
    assert "Coverage and performance" in report
    assert "Very small sample; descriptive only. Do not change strategy." in report


def test_research_report_separates_trade_candidates_and_strong_b(tmp_path) -> None:
    db = make_db(tmp_path)
    insert_signal(db, 1, ticker="NVDA", grade="A++ Signal", final_outcome="TP1", final_r=1.0)
    insert_signal(
        db,
        2,
        ticker="MSFT",
        grade="B Watch Alert",
        alert_type="experimental_watch",
        final_outcome="SL",
        final_r=-1.0,
        mfe_r=0.2,
    )
    data = ResearchReport(db, days=30).build_data()

    assert data["performance_by_type"]["A++ Signal"]["total"] == 1
    assert data["performance_by_type"]["Strong B Experimental Watch"]["total"] == 1
    assert data["coverage"]["accepted_trade_candidates"] == 1
    assert data["coverage"]["strong_b"] == 1


def test_research_report_mfe_mae_distribution_uses_price_path(tmp_path) -> None:
    db = make_db(tmp_path)
    insert_signal(db, 1, mfe_r=1.5, mae_r=-0.75, final_outcome="SL", final_r=-1.0)
    data = ResearchReport(db, days=30).build_data()

    overall = data["mfe_mae_distribution"]["Overall"]
    assert overall["mfe"]["+1.50R"] == "1/1 (100.0%)"
    assert overall["mae"]["-0.50R"] == "1/1 (100.0%)"


def test_research_report_time_buckets_and_failure_buckets(tmp_path) -> None:
    db = make_db(tmp_path)
    insert_signal(db, 1, final_outcome="SL", final_r=-1.0, mfe_r=0.1, mae_r=-1.0)
    insert_signal(db, 2, ticker="AAPL", grade="A+ Signal", final_outcome="SL", final_r=-1.0, mfe_r=0.6, mae_r=-1.0)
    data = ResearchReport(db, days=30).build_data()

    assert data["time_bucketed_evolution"]["buckets"]["15m"]["coverage"] == 2
    assert data["movement_sequencing"]["Failed immediately"]["count"] == 1
    assert data["movement_sequencing"]["Moved favorably then reversed"]["count"] == 1


def test_research_report_score_rules_and_counterfactual_warning(tmp_path) -> None:
    db = make_db(tmp_path)
    for idx in range(1, 4):
        insert_signal(db, idx, ticker=f"T{idx}", grade="A Signal", score=58 + idx, final_r=float(idx - 2))
    data = ResearchReport(db, days=30).build_data()

    assert data["score_vs_outcome"]["rule"] == "spearman_only_small_sample"
    assert data["counterfactual"]["warning"].startswith("Counterfactual results are exploratory.")


def test_research_report_missing_price_path_fallback_and_legacy_nulls(tmp_path) -> None:
    db = make_db(tmp_path)
    insert_signal(db, 1, path=False, final_outcome="TP1", final_r=1.0, mfe_r=1.1, mae_r=-0.2)
    db.execute("UPDATE signal_diagnostics SET schema_version=NULL, strategy_version=NULL, git_commit_hash=NULL WHERE active_signal_id=1")
    report = ResearchReport(db, days=30).build()

    assert "Used stored MFE/MAE fallback where price_path was unavailable." in report
    assert "legacy/null" in report


def test_research_report_rejections_json_csv_and_read_only(tmp_path) -> None:
    db = make_db(tmp_path)
    insert_signal(db, 1)
    insert_rejected(db, "AAPL", 56)
    before = table_counts(db)

    report = ResearchReport(db, days=30)
    payload = json.loads(report.export_json())
    csv_dir = tmp_path / "csv"
    report.export_csv(csv_dir)
    after = table_counts(db)

    assert payload["rejection_analytics"]["score_distribution"]["count"] == 1
    assert payload["rejection_analytics"]["legacy_count"] == 1
    assert payload["rejection_analytics"]["verified_v2_count"] == 0
    assert (csv_dir / "per_signal_audit.csv").exists()
    assert (csv_dir / "rejection_summary.csv").exists()
    assert before == after


def test_research_report_v2_rejections_do_not_mix_legacy_labels(tmp_path) -> None:
    db = make_db(tmp_path)
    db.insert_dict(
        "rejected_candidate_diagnostics",
        {
            "ticker": "AAPL",
            "final_score": 56,
            "computed_grade": "B Watch Alert",
            "first_rejection_gate": "grade_below_trade_candidate_threshold",
            "rejection_reasons_json": ["Grade below trade threshold"],
            "conditions_passed_json": ["Price above EMA50"],
            "conditions_failed_json": ["Grade below trade threshold"],
            "diagnostics_format_version": 2,
            "evaluated_conditions_json": [
                {"condition_key": "price_above_ema50", "display_name": "Price above EMA50", "result": "PASS", "is_blocking": False},
                {
                    "condition_key": "grade_below_trade_candidate_threshold",
                    "display_name": "Grade below trade threshold",
                    "result": "FAIL",
                    "is_blocking": True,
                },
            ],
            "passed_conditions_v2_json": [
                {"condition_key": "price_above_ema50", "display_name": "Price above EMA50", "result": "PASS", "is_blocking": False}
            ],
            "failed_conditions_v2_json": [
                {
                    "condition_key": "grade_below_trade_candidate_threshold",
                    "display_name": "Grade below trade threshold",
                    "result": "FAIL",
                    "is_blocking": True,
                }
            ],
            "blocking_conditions_json": [
                {
                    "condition_key": "grade_below_trade_candidate_threshold",
                    "display_name": "Grade below trade threshold",
                    "result": "FAIL",
                    "is_blocking": True,
                }
            ],
            "actual_first_blocking_gate": "grade_below_trade_candidate_threshold",
            "why_not_trade": "Grade below trade threshold",
            "raw_metrics_json": {},
        },
    )
    insert_rejected(db, "MSFT", 55)

    data = ResearchReport(db, days=30).build_data()["rejection_analytics"]

    assert data["verified_v2_count"] == 1
    assert data["legacy_count"] == 1
    assert data["top_actual_blocking_gates"] == [("grade_below_trade_candidate_threshold", 1)]
    assert data["top_passed_conditions"] == [("Price above EMA50", 1)]
    assert data["legacy_rejection_labels"]


def test_research_report_runner_builds_without_database_mutation(tmp_path) -> None:
    db = make_db(tmp_path)
    insert_signal(db, 1)
    before = table_counts(db)

    runner = ResearchReportRunner(Settings(database_url=f"sqlite:///{tmp_path / 'research.db'}"), db=db, days=30)
    text = runner.build()
    json_text = runner.build_json()

    assert "Coverage and performance" in text
    assert json.loads(json_text)["metadata"]["signal_rows"] == 1
    assert table_counts(db) == before
