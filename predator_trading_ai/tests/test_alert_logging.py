from dataclasses import dataclass

from predator_trading_ai.config import Settings
from predator_trading_ai.engines.regime_detector import MarketRegime
from predator_trading_ai.engines.strategy_engine import StrategySetup
from predator_trading_ai.main import PredatorTradingAI


@dataclass(frozen=True)
class FakeSnapshot:
    price: float
    bid: float = 0.0
    ask: float = 0.0
    volume: int = 0


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


def test_b_watch_alert_is_not_tracked_as_active_trade_by_default(tmp_path, monkeypatch) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'predator_test.db'}",
        telegram_bot_token=None,
        enable_b_tp_sl_tracking=False,
        min_score_b=58,
    )
    app = PredatorTradingAI(settings)
    app.db.initialize()
    app.state.cooldowns.clear()
    log_messages: list[str] = []
    original_info = app.logger.info

    def capture_info(message, *args, **kwargs):
        log_messages.append(message % args if args else message)
        original_info(message, *args, **kwargs)

    monkeypatch.setattr(app.logger, "info", capture_info)
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
    output = "\n".join(log_messages)
    assert "B_ALERT_POLICY_DECISION ticker=AAPL" in output
    assert "allowed=True" in output


def test_b_watch_alert_below_effective_floor_is_logged_and_not_sent(tmp_path, monkeypatch) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'predator_test.db'}",
        telegram_bot_token=None,
        min_score_b=50,
    )
    app = PredatorTradingAI(settings)
    app.db.initialize()
    app.state.cooldowns.clear()
    log_messages: list[str] = []
    original_info = app.logger.info

    def capture_info(message, *args, **kwargs):
        log_messages.append(message % args if args else message)
        original_info(message, *args, **kwargs)

    monkeypatch.setattr(app.logger, "info", capture_info)
    setup = StrategySetup(
        ticker="AAPL",
        direction="long",
        setup_type="graded watch setup",
        score=55,
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

    assert app.db.fetch_all("SELECT * FROM sent_alerts") == []
    output = "\n".join(log_messages)
    assert "B_ALERT_POLICY_DECISION ticker=AAPL" in output
    assert "min_score_b_configured=50" in output
    assert "min_score_b_effective=58" in output
    assert "allowed=False" in output


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


def test_monitoring_workers_start_and_log(tmp_path, monkeypatch) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'workers.db'}")
    app = PredatorTradingAI(settings)
    app.db.initialize()
    log_messages: list[str] = []
    original_info = app.logger.info

    def capture_info(message, *args, **kwargs):
        log_messages.append(message % args if args else message)
        original_info(message, *args, **kwargs)

    monkeypatch.setattr(app.logger, "info", capture_info)

    app.start_monitoring_workers()

    output = "\n".join(log_messages)
    assert "Starting ActiveSignalTracker..." in output
    assert "ActiveSignalTracker started." in output
    assert "Starting TP/SL monitor..." in output
    assert "TP/SL monitor started." in output
    assert "Starting PerformanceReportRunner..." in output
    assert "PerformanceReportRunner started." in output
    assert app.tp_sl_monitor_started is True
    assert app.performance_report_runner is not None


def test_tp_sl_monitor_runs_active_signal_checks(tmp_path, monkeypatch) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'tp_sl.db'}", telegram_bot_token=None)
    app = PredatorTradingAI(settings)
    app.db.initialize()
    app.active_signal_tracker.register(
        ticker="NVDA",
        grade="A+ Signal",
        direction="long",
        entry_zone_low=124.50,
        entry_zone_high=125.20,
        stop_loss=121.80,
        targets=(128.40, 130.00, 132.00),
    )
    monkeypatch.setattr(app.market_data, "get_latest_snapshot", lambda ticker: FakeSnapshot(price=128.55))

    app.tp_sl_monitor_started = True
    app.run_tp_sl_monitor()

    updates = app.db.fetch_all("SELECT update_type FROM signal_updates ORDER BY id")
    completed = app.db.fetch_all("SELECT outcome, status FROM completed_trades ORDER BY id")
    assert [row["update_type"] for row in updates] == ["tp1"]
    assert completed[0]["outcome"] == "TP1"
    assert completed[0]["status"] == "active"
