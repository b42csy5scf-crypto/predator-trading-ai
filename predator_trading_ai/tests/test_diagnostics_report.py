import subprocess

from predator_trading_ai.config import Settings
from predator_trading_ai.database.db import Database
from predator_trading_ai.reports.diagnostics_report import DiagnosticsReport


def make_db(tmp_path) -> Database:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'diagnostics_report.db'}")
    db = Database(settings)
    db.initialize()
    return db


def insert_signal(
    db: Database,
    *,
    active_signal_id: int,
    ticker: str,
    grade: str,
    alert_type: str,
    status: str,
    final_outcome: str | None,
    mfe_r: float,
    mae_r: float,
    current_r: float,
    holding_seconds: float,
) -> None:
    db.insert_dict(
        "active_signals",
        {
            "id": active_signal_id,
            "ticker": ticker,
            "grade": grade,
            "alert_type": alert_type,
            "direction": "long",
            "entry_zone_low": 100,
            "entry_zone_high": 101,
            "stop_loss": 98,
            "tp1": 104,
            "tp2": 106,
            "tp3": 108,
            "sent_at": "2026-07-09T14:30:00+00:00",
            "status": status,
        },
    )
    db.insert_dict(
        "signal_diagnostics",
        {
            "signal_id": active_signal_id,
            "active_signal_id": active_signal_id,
            "ticker": ticker,
            "grade": grade,
            "alert_type": alert_type,
            "score": 78 if grade == "A++ Signal" else 58,
            "entry_zone_low": 100,
            "entry_zone_high": 101,
            "stop_loss": 98,
            "tp1": 104,
            "tp2": 106,
            "tp3": 108,
            "telegram_note": "test",
            "scoring_components_json": [],
            "raw_metrics_json": {},
        },
    )
    db.insert_dict(
        "signal_outcome_diagnostics",
        {
            "active_signal_id": active_signal_id,
            "ticker": ticker,
            "grade": grade,
            "alert_type": alert_type,
            "direction": "long",
            "entry_price": 100.5,
            "original_stop_loss": 98,
            "risk_per_share": 2.5,
            "max_favorable_price": 105,
            "max_adverse_price": 99,
            "mfe_r": mfe_r,
            "mae_r": mae_r,
            "current_r": current_r,
            "tp1_hit_at": "2026-07-09T15:00:00+00:00" if final_outcome and final_outcome.startswith("TP") else None,
            "sl_hit_at": "2026-07-09T15:05:00+00:00" if final_outcome == "SL" else None,
            "holding_seconds": holding_seconds,
            "final_outcome": final_outcome,
            "exit_reason": "test_exit" if final_outcome else None,
        },
    )


def test_diagnostics_report_separates_trade_candidates_and_strong_b(tmp_path) -> None:
    db = make_db(tmp_path)
    insert_signal(
        db,
        active_signal_id=1,
        ticker="NVDA",
        grade="A++ Signal",
        alert_type="trade_candidate",
        status="closed",
        final_outcome="TP3",
        mfe_r=3.0,
        mae_r=-0.2,
        current_r=3.0,
        holding_seconds=1800,
    )
    insert_signal(
        db,
        active_signal_id=2,
        ticker="PLD",
        grade="B Watch Alert",
        alert_type="experimental_watch",
        status="closed",
        final_outcome="SL",
        mfe_r=0.4,
        mae_r=-1.0,
        current_r=-1.0,
        holding_seconds=600,
    )
    db.insert_dict(
        "rejected_candidate_diagnostics",
        {
            "ticker": "AAPL",
            "final_score": 55,
            "computed_grade": "B Watch Alert",
            "first_rejection_gate": "Grade below A",
            "rejection_reasons_json": ["Grade below A", "Relative volume below threshold"],
            "conditions_passed_json": ["price above EMA50"],
            "conditions_failed_json": ["Relative volume below threshold"],
            "why_not_trade": "Grade below A",
            "raw_metrics_json": {},
        },
    )

    report = DiagnosticsReport(db, days=7).build()

    assert "Accepted A/A+/A++ signals: 1" in report
    assert "Strong B Experimental Watch signals: 1" in report
    assert "Sample size is small; do not change strategy yet." in report
    assert "Trade Candidates Summary" in report
    assert "Win rate: 100.0%" in report
    assert "By Grade Performance" in report
    assert "A++ Signal" in report
    assert "A+ Signal" in report
    assert "A Signal" in report
    assert "A++ Only" not in report
    assert "Strong B Experimental Watch\nTotal: 1" in report
    assert "Candidates rejected with score >= 50: 1" in report
    assert "Legacy rejection labels — condition result unavailable" in report
    assert "- Grade below A: 1" in report
    assert "Signals went directly to SL: 1" in report
    assert "Best by final R: NVDA 3.00R" in report
    assert "Worst by final R: PLD -1.00R" in report


def test_diagnostics_report_separates_v2_pass_fail_and_legacy(tmp_path) -> None:
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
    db.insert_dict(
        "rejected_candidate_diagnostics",
        {
            "ticker": "MSFT",
            "final_score": 55,
            "computed_grade": "B Watch Alert",
            "first_rejection_gate": "Grade below A",
            "rejection_reasons_json": ["price above EMA50"],
            "conditions_passed_json": ["price above EMA50"],
            "conditions_failed_json": [],
            "why_not_trade": "legacy",
            "raw_metrics_json": {},
        },
    )

    report = DiagnosticsReport(db, days=7).build()

    assert "Verified diagnostics v2 rows: 1" in report
    assert "Legacy/ambiguous rows: 1" in report
    assert "Top actual blocking gates:" in report
    assert "- grade_below_trade_candidate_threshold: 1" in report
    assert "Most common passed conditions:" in report
    assert "- Price above EMA50: 1" in report
    assert "Legacy rejection labels — condition result unavailable:" in report


def test_run_diagnostics_report_command(tmp_path) -> None:
    db_path = tmp_path / "diagnostics_cli.db"
    settings = Settings(database_url=f"sqlite:///{db_path}")
    Database(settings).initialize()
    result = subprocess.run(
        [
            "predator_trading_ai/.venv/bin/python",
            "-m",
            "predator_trading_ai.run_diagnostics_report",
            "--days",
            "7",
        ],
        cwd="/Users/apple/Documents/Codex/2026-05-19/build-a-complete-ai-assisted-trading",
        env={"DATABASE_URL": f"sqlite:///{db_path}"},
        text=True,
        capture_output=True,
        check=True,
    )
    assert "Predator Diagnostics Report" in result.stdout
    assert "Days included: 7" in result.stdout
