import json

import pandas as pd
from dataclasses import dataclass
from datetime import datetime, timezone

from predator_trading_ai.config import Settings
from predator_trading_ai.database.db import Database
from predator_trading_ai.engines.active_signal_tracker import ActiveSignalTracker
from predator_trading_ai.engines.regime_detector import MarketRegime
from predator_trading_ai.engines.signal_diagnostics import SignalDiagnosticsRecorder
from predator_trading_ai.engines.signal_engine import TradingSignal
from predator_trading_ai.engines.strategy_engine import StrategySetup


def make_recorder(tmp_path) -> tuple[SignalDiagnosticsRecorder, Database]:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'diagnostics.db'}")
    db = Database(settings)
    db.initialize()
    return SignalDiagnosticsRecorder(db), db


@dataclass(frozen=True)
class FakeSnapshot:
    ticker: str = "NVDA"
    price: float = 125.0
    bid: float = 124.95
    ask: float = 125.05
    volume: int = 1000
    vwap: float | None = None
    timestamp: datetime = datetime(2026, 7, 9, 14, 45, tzinfo=timezone.utc)


def sample_bars() -> pd.DataFrame:
    rows = []
    for idx in range(60):
        close = 100 + idx * 0.5
        rows.append(
            {
                "open": close - 0.2,
                "high": close + 0.6,
                "low": close - 0.6,
                "close": close,
                "volume": 1_000_000 + idx,
                "atr_14": 2.0,
                "ema_21": close - 1.0,
                "ema_50": close - 3.0,
                "relative_volume": 1.4,
                "rsi_14": 61.0,
                "macd": 1.2,
                "macd_signal": 0.8,
            }
        )
    return pd.DataFrame(rows)


def sample_regime() -> MarketRegime:
    return MarketRegime(
        regime="bull-trend",
        volatility=1.5,
        volume_state="normal",
        trend_strength=0.8,
        is_safe=True,
        reason="test",
        spy_trend="bull",
        qqq_trend="bull",
        breadth_score=67,
    )


def sample_setup() -> StrategySetup:
    return StrategySetup(
        ticker="NVDA",
        direction="long",
        setup_type="high-quality breakout",
        score=76,
        entry_zone_low=124.50,
        entry_zone_high=125.50,
        stop_loss=121.00,
        targets=(128.00, 130.00, 134.00),
        reason="controlled breakout",
        do_not_enter_conditions=[],
        signal_tier="A++ Signal",
        confirmations=("EMA50 above EMA200", "relative volume >= 0.80"),
        scoring_components=("breakout base:+58.00", "breadth confirmation:+4.00"),
    )


def sample_signal() -> TradingSignal:
    return TradingSignal(
        ticker="NVDA",
        direction="long",
        setup_type="high-quality breakout",
        entry_zone_low=124.50,
        entry_zone_high=125.50,
        target_1=128.00,
        target_2=130.00,
        target_3=134.00,
        stop_loss=121.00,
        risk_reward=2.0,
        confidence=76,
        expected_win_rate=None,
        position_size=100,
        liquidity_score=90,
        market_regime="bull-trend",
        reason="controlled breakout",
        do_not_enter_conditions=[],
    )


def insert_active_signal(db: Database, active_signal_id: int = 1) -> None:
    db.execute(
        """
        INSERT INTO active_signals (
            id, ticker, grade, direction, entry_zone_low, entry_zone_high,
            stop_loss, original_stop_loss, tp1, tp2, tp3, sent_at, status
        )
        VALUES (?, 'NVDA', 'A++ Signal', 'long', 124.50, 125.50, 121.00, 121.00, 128.00, 130.00, 134.00, CURRENT_TIMESTAMP, 'active')
        """,
        [active_signal_id],
    )


def test_accepted_signal_persistence(tmp_path) -> None:
    recorder, db = make_recorder(tmp_path)
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'diagnostics.db'}")
    insert_active_signal(db, 22)

    recorder.record_accepted_signal(
        signal_id=11,
        active_signal_id=22,
        setup=sample_setup(),
        signal=sample_signal(),
        bars=sample_bars(),
        regime=sample_regime(),
        telegram_note="controlled breakout",
        settings=settings,
        snapshot=FakeSnapshot(),
        market_context={"VIX": 18.5},
        open_positions_count=1,
        open_positions_same_sector=1,
        git_commit_hash="abc123",
    )

    rows = db.fetch_all("SELECT * FROM signal_diagnostics")
    assert len(rows) == 1
    assert rows[0]["signal_id"] == 11
    assert rows[0]["active_signal_id"] == 22
    assert rows[0]["grade"] == "A++ Signal"
    assert rows[0]["atr"] == 2.0
    assert round(rows[0]["macd_minus_signal"], 2) == 0.4
    assert rows[0]["git_commit_hash"] == "abc123"
    assert rows[0]["research_dataset_version"] == "v1.0"
    assert rows[0]["config_hash"]
    assert rows[0]["entry_open"] is not None
    assert rows[0]["previous_close"] is not None
    assert rows[0]["vix_value"] == 18.5
    outcome = db.fetch_all("SELECT * FROM signal_outcome_diagnostics WHERE active_signal_id = 22")[0]
    assert outcome["ticker"] == "NVDA"
    assert outcome["risk_per_share"] == 4.0
    config = db.fetch_all("SELECT * FROM config_snapshots WHERE config_hash = ?", [rows[0]["config_hash"]])
    assert len(config) == 1
    assert "dummy" not in config[0]["config_json"]
    path = db.fetch_all("SELECT * FROM price_path WHERE signal_id = ?", [22])
    assert len(path) == 1
    assert path[0]["event_type"] == "entry"


def test_rejected_candidate_persistence(tmp_path) -> None:
    recorder, db = make_recorder(tmp_path)
    insert_active_signal(db, 1)

    recorder.record_rejected_candidate(
        ticker="AAPL",
        final_score=54,
        computed_grade="B Watch Alert",
        first_rejection_gate="Grade below A",
        rejection_reasons=["Grade below A", "Relative volume below threshold"],
        conditions_passed=["price above EMA50"],
        conditions_failed=["Relative volume below threshold"],
        bars=sample_bars(),
        regime=sample_regime(),
    )

    rows = db.fetch_all("SELECT * FROM rejected_candidate_diagnostics")
    assert len(rows) == 1
    assert rows[0]["ticker"] == "AAPL"
    assert rows[0]["final_score"] == 54
    assert rows[0]["first_rejection_gate"] == "grade_below_trade_candidate_threshold"
    assert rows[0]["actual_first_blocking_gate"] == "grade_below_trade_candidate_threshold"
    assert rows[0]["diagnostics_format_version"] == 2
    assert rows[0]["entry_open"] is not None
    assert rows[0]["breakout_distance_atr"] is not None


def test_rejected_candidate_v2_grade_only_does_not_blame_passed_conditions(tmp_path) -> None:
    recorder, db = make_recorder(tmp_path)

    recorder.record_rejected_candidate(
        ticker="AAPL",
        final_score=56.2,
        computed_grade="B Watch Alert",
        first_rejection_gate="Grade below A",
        rejection_reasons=["price above EMA50", "EMA50 above EMA200", "short-term momentum improving", "Grade below A"],
        conditions_passed=["price above EMA50", "EMA50 above EMA200", "short-term momentum improving"],
        conditions_failed=[],
        bars=sample_bars(),
        regime=sample_regime(),
    )

    row = db.fetch_all("SELECT * FROM rejected_candidate_diagnostics")[0]
    passed = json.loads(row["passed_conditions_v2_json"])
    failed = json.loads(row["failed_conditions_v2_json"])
    blocking = json.loads(row["blocking_conditions_json"])
    rejections = json.loads(row["rejection_reasons_json"])
    assert {item["condition_key"] for item in passed} >= {
        "price_above_ema50",
        "ema50_above_ema200",
        "short_term_momentum_improving",
    }
    assert "price_above_ema50" not in {item["condition_key"] for item in failed}
    assert row["actual_first_blocking_gate"] == "grade_below_trade_candidate_threshold"
    assert blocking[0]["condition_key"] == "grade_below_trade_candidate_threshold"
    assert "Price above EMA50" not in rejections
    assert "price above EMA50" not in rejections


def test_rejected_candidate_v2_ema50_fail_is_truthful(tmp_path) -> None:
    recorder, db = make_recorder(tmp_path)
    bars = sample_bars()
    bars.loc[bars.index[-1], "ema_50"] = float(bars.iloc[-1]["close"]) + 1.0

    recorder.record_rejected_candidate(
        ticker="AAPL",
        final_score=52,
        computed_grade="C Risky/Early Alert",
        first_rejection_gate="price below EMA50",
        rejection_reasons=["price below EMA50"],
        conditions_passed=["EMA50 above EMA200"],
        conditions_failed=["price below EMA50"],
        bars=bars,
        regime=sample_regime(),
    )

    row = db.fetch_all("SELECT * FROM rejected_candidate_diagnostics")[0]
    failed = json.loads(row["failed_conditions_v2_json"])
    rejections = json.loads(row["rejection_reasons_json"])
    assert failed[0]["condition_key"] == "price_above_ema50"
    assert "Price not above EMA50" in rejections
    assert row["actual_first_blocking_gate"] == "price_above_ema50"


def test_mfe_mae_calculation(tmp_path) -> None:
    recorder, db = make_recorder(tmp_path)
    insert_active_signal(db, 1)
    recorder.initialize_outcome_from_signal(1, sample_signal(), "A++ Signal")

    recorder.update_outcome(active_signal_id=1, current_price=133.0)
    recorder.update_outcome(active_signal_id=1, current_price=123.0)

    row = db.fetch_all("SELECT * FROM signal_outcome_diagnostics WHERE active_signal_id = 1")[0]
    assert row["max_favorable_price"] == 133.0
    assert row["max_adverse_price"] == 123.0
    assert row["mfe_r"] == 2.0
    assert row["mae_r"] == -0.5
    assert row["current_r"] == -0.5
    assert row["time_to_mfe_seconds"] is not None
    assert row["time_to_mae_seconds"] is not None


def test_outcome_updates(tmp_path) -> None:
    recorder, db = make_recorder(tmp_path)
    insert_active_signal(db, 1)
    recorder.initialize_outcome_from_signal(1, sample_signal(), "A++ Signal")

    recorder.update_outcome(active_signal_id=1, current_price=128.2, event="tp1")
    recorder.update_outcome(
        active_signal_id=1,
        current_price=121.0,
        event="stop_loss",
        final_outcome="SL",
        exit_reason="stop_loss",
        exit_atr=2.0,
    )

    row = db.fetch_all("SELECT * FROM signal_outcome_diagnostics WHERE active_signal_id = 1")[0]
    assert row["tp1_hit_at"] is not None
    assert row["sl_hit_at"] is not None
    assert row["final_outcome"] == "SL"
    assert row["exit_reason"] == "stop_loss"
    assert row["exit_price"] == 121.0
    assert row["exit_atr"] == 2.0
    assert row["realized_r"] == -1.0


def test_price_path_sampling_and_event_recording(tmp_path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'path.db'}")
    db = Database(settings)
    db.initialize()
    recorder = SignalDiagnosticsRecorder(db)
    tracker = ActiveSignalTracker(db, settings, recorder)
    signal_id = tracker.register(
        ticker="NVDA",
        grade="A+ Signal",
        direction="long",
        entry_zone_low=124.50,
        entry_zone_high=125.50,
        stop_loss=121.00,
        targets=(128.00, 130.00, 134.00),
    )

    tracker.check_ticker("NVDA", 126.0, high=126.5, low=125.8, timestamp="2026-07-09T14:35:00+00:00")
    tracker.check_ticker("NVDA", 128.1, high=128.2, low=127.5, timestamp="2026-07-09T14:40:00+00:00")

    rows = db.fetch_all("SELECT event_type, price, high, low FROM price_path WHERE signal_id = ? ORDER BY id", [signal_id])
    assert [row["event_type"] for row in rows] == ["scan", "scan", "tp1"]
    assert rows[0]["high"] == 126.5
    assert rows[-1]["event_type"] == "tp1"


def test_config_hash_lookup_and_redaction(tmp_path) -> None:
    recorder, db = make_recorder(tmp_path)
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'diagnostics.db'}",
        alpaca_api_key="dummy-key",
        telegram_bot_token="dummy-token",
    )

    first = recorder.record_config(settings)
    second = recorder.record_config(settings)

    assert first == second
    rows = db.fetch_all("SELECT * FROM config_snapshots WHERE config_hash = ?", [first])
    assert len(rows) == 1
    assert "dummy-key" not in rows[0]["config_json"]
    assert "<redacted>" in rows[0]["config_json"]


def test_universe_snapshot_recording(tmp_path) -> None:
    recorder, db = make_recorder(tmp_path)

    recorder.record_universe_snapshot(
        symbols_scanned=50,
        symbols_skipped=3,
        api_failures=1,
        missing_market_data=2,
        symbols_successfully_evaluated=47,
        timestamp="2026-07-09T14:30:00+00:00",
    )

    rows = db.fetch_all("SELECT * FROM universe_snapshot")
    assert len(rows) == 1
    assert rows[0]["symbols_scanned"] == 50
    assert rows[0]["api_failures"] == 1


def test_retention_cleanup(tmp_path) -> None:
    recorder, db = make_recorder(tmp_path)
    db.execute(
        """
        INSERT INTO signal_diagnostics (
            created_at, ticker, grade, score, entry_zone_low, entry_zone_high,
            stop_loss, tp1, tp2, tp3, scoring_components_json, raw_metrics_json
        )
        VALUES (datetime('now', '-40 days'), 'NVDA', 'A Signal', 60, 1, 2, 0.5, 3, 4, 5, '[]', '{}')
        """
    )
    db.execute(
        """
        INSERT INTO rejected_candidate_diagnostics (
            created_at, ticker, final_score, computed_grade, rejection_reasons_json,
            conditions_passed_json, conditions_failed_json, why_not_trade, raw_metrics_json
        )
        VALUES (datetime('now', '-40 days'), 'AAPL', 55, 'B Watch Alert', '[]', '[]', '[]', 'old', '{}')
        """
    )

    recorder.cleanup(retention_days=30)

    assert db.fetch_all("SELECT * FROM signal_diagnostics") == []
    assert db.fetch_all("SELECT * FROM rejected_candidate_diagnostics") == []
