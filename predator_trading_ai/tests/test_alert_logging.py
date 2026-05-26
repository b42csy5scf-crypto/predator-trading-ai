from predator_trading_ai.config import Settings
from predator_trading_ai.engines.regime_detector import MarketRegime
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
