import pandas as pd

from predator_trading_ai.config import Settings
from predator_trading_ai.database.db import Database
from predator_trading_ai.engines.regime_detector import MarketRegime
from predator_trading_ai.engines.signal_diagnostics import SignalDiagnosticsRecorder
from predator_trading_ai.engines.signal_engine import TradingSignal
from predator_trading_ai.engines.strategy_engine import StrategySetup


def make_recorder(tmp_path) -> tuple[SignalDiagnosticsRecorder, Database]:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'diagnostics.db'}")
    db = Database(settings)
    db.initialize()
    return SignalDiagnosticsRecorder(db), db


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
    insert_active_signal(db, 22)

    recorder.record_accepted_signal(
        signal_id=11,
        active_signal_id=22,
        setup=sample_setup(),
        signal=sample_signal(),
        bars=sample_bars(),
        regime=sample_regime(),
        telegram_note="controlled breakout",
    )

    rows = db.fetch_all("SELECT * FROM signal_diagnostics")
    assert len(rows) == 1
    assert rows[0]["signal_id"] == 11
    assert rows[0]["active_signal_id"] == 22
    assert rows[0]["grade"] == "A++ Signal"
    assert rows[0]["atr"] == 2.0
    assert round(rows[0]["macd_minus_signal"], 2) == 0.4
    outcome = db.fetch_all("SELECT * FROM signal_outcome_diagnostics WHERE active_signal_id = 22")[0]
    assert outcome["ticker"] == "NVDA"
    assert outcome["risk_per_share"] == 4.0


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
    assert rows[0]["first_rejection_gate"] == "Grade below A"


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


def test_outcome_updates(tmp_path) -> None:
    recorder, db = make_recorder(tmp_path)
    insert_active_signal(db, 1)
    recorder.initialize_outcome_from_signal(1, sample_signal(), "A++ Signal")

    recorder.update_outcome(active_signal_id=1, current_price=128.2, event="tp1")
    recorder.update_outcome(active_signal_id=1, current_price=121.0, event="stop_loss", final_outcome="SL", exit_reason="stop_loss")

    row = db.fetch_all("SELECT * FROM signal_outcome_diagnostics WHERE active_signal_id = 1")[0]
    assert row["tp1_hit_at"] is not None
    assert row["sl_hit_at"] is not None
    assert row["final_outcome"] == "SL"
    assert row["exit_reason"] == "stop_loss"


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
