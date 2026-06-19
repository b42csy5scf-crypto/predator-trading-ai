from predator_trading_ai.config import Settings
from predator_trading_ai.engines.regime_detector import MarketRegime
from predator_trading_ai.engines.strategy_engine import StrategySetup
from predator_trading_ai.main import PredatorTradingAI


def test_alert_cooldown_uses_ticker_and_grade() -> None:
    assert PredatorTradingAI.alert_cooldown_key("AAPL", "A++ Signal") == "AAPL:grade:A++ Signal"
    assert PredatorTradingAI.alert_cooldown_key("AAPL", "B Watch Alert") != PredatorTradingAI.alert_cooldown_key("AAPL", "A Signal")


def test_sent_alerts_table_logs_messages(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'predator_test.db'}",
        telegram_bot_token=None,
        alert_cooldown_minutes=60,
    )
    app = PredatorTradingAI(settings)
    app.db.initialize()
    app.state.cooldowns.clear()
    app.log_sent_alert(
        ticker="AAPL",
        grade="B Watch Alert",
        alert_type="observe_only",
        score=51,
        setup_type="graded watch setup",
        regime="bull-trend",
        message="Observe only — not a trade entry.",
    )
    rows = app.db.fetch_all("SELECT ticker, grade, alert_type, message FROM sent_alerts")
    assert len(rows) == 1
    assert rows[0]["ticker"] == "AAPL"
    assert rows[0]["grade"] == "B Watch Alert"
    assert "Observe only" in rows[0]["message"]


def test_bear_watch_regime_detection() -> None:
    mild_bear = MarketRegime(
        "bear",
        1.0,
        "normal",
        0.2,
        False,
        "Mild bear",
        regime_severity="mild",
    )
    severe_bear = MarketRegime(
        "bear",
        5.0,
        "high",
        0.4,
        False,
        "Severe bear",
        regime_severity="severe",
    )
    assert PredatorTradingAI.is_bear_watch_regime(mild_bear)
    assert not PredatorTradingAI.is_bear_watch_regime(severe_bear)


def test_c_grade_watch_alert_is_not_sent_or_logged(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'predator_test.db'}",
        telegram_bot_token="dummy-token",
        telegram_chat_id="123",
    )
    app = PredatorTradingAI(settings)
    app.db.initialize()
    app.state.cooldowns.clear()
    setup = StrategySetup(
        ticker="AAPL",
        direction="long",
        setup_type="graded watch setup",
        score=44,
        entry_zone_low=100,
        entry_zone_high=101,
        stop_loss=98,
        targets=(103, 105, 108),
        reason="early setup forming",
        do_not_enter_conditions=[],
        signal_tier="C Risky/Early Alert",
    )
    regime = MarketRegime("normal", 1.0, "normal", 0.3, True, "Normal tradable regime")

    app.send_watch_alert("AAPL", setup, regime)

    rows = app.db.fetch_all("SELECT * FROM sent_alerts")
    assert rows == []


def test_b_watch_alert_is_not_tracked_as_active_trade_by_default(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'predator_test.db'}",
        telegram_bot_token=None,
        enable_b_tp_sl_tracking=False,
        min_score_b=58,
    )
    app = PredatorTradingAI(settings)
    app.db.initialize()
    app.state.cooldowns.clear()
    setup = StrategySetup(
        ticker="AAPL",
        direction="long",
        setup_type="graded watch setup",
        score=60,
        entry_zone_low=100,
        entry_zone_high=101,
        stop_loss=98,
        targets=(103, 105, 108),
        reason="strong watch setup",
        do_not_enter_conditions=[],
        signal_tier="B Watch Alert",
        confirmations=(
            "price above EMA50",
            "EMA50 above EMA200",
            "RSI between 45 and 65",
            "relative volume >= 0.80",
        ),
    )
    regime = MarketRegime("normal", 1.0, "normal", 0.3, True, "Normal tradable regime", spy_trend="bull", qqq_trend="bull")

    app.send_watch_alert("AAPL", setup, regime)

    sent = app.db.fetch_all("SELECT * FROM sent_alerts")
    active = app.db.fetch_all("SELECT * FROM active_signals")
    assert len(sent) == 1
    assert active == []


def test_scan_alert_summary_counts_generated_and_suppressed(tmp_path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'summary.db'}")
    app = PredatorTradingAI(settings)
    app.reset_scan_alert_summary()

    app.record_signal_generated()
    app.record_signal_suppressed("duplicate cooldown")
    app.record_signal_suppressed("duplicate cooldown")
    app.record_signal_suppressed("C alerts disabled")

    assert app.scan_signals_generated == 1
    assert app.scan_signals_suppressed == 3
    assert app.scan_suppression_reasons["duplicate cooldown"] == 2
    assert app.scan_suppression_reasons["C alerts disabled"] == 1
