from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from predator_trading_ai.config import Settings
from predator_trading_ai.database.db import Database
from predator_trading_ai.engines.alert_policy import AlertPolicy
from predator_trading_ai.engines.regime_detector import MarketRegime


EASTERN = ZoneInfo("America/New_York")


def make_policy(tmp_path, **overrides) -> AlertPolicy:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'policy.db'}",
        max_alerts_per_day=overrides.pop("max_alerts_per_day", 20),
        max_alerts_per_ticker_per_day=1,
        **overrides,
    )
    db = Database(settings)
    db.initialize()
    return AlertPolicy(settings, db)


def normal_regime() -> MarketRegime:
    return MarketRegime("normal", 1.0, "normal", 0.4, True, "Normal tradable regime", spy_trend="bull", qqq_trend="bull")


def strong_b_confirmations() -> tuple[str, ...]:
    return (
        "price above EMA50",
        "EMA50 above EMA200",
        "RSI between 45 and 65",
        "relative volume >= 0.80",
    )


def test_max_daily_alerts(tmp_path) -> None:
    policy = make_policy(tmp_path, max_alerts_per_day=2)
    now = datetime(2026, 6, 9, 10, 0, tzinfo=EASTERN)
    for ticker in ("AAPL", "MSFT"):
        assert policy.evaluate(ticker, "A Signal", 62, normal_regime(), now).allowed
        policy.record(ticker, "A Signal", now)
    decision = policy.evaluate("NVDA", "A+ Signal", 70, normal_regime(), now)
    assert not decision.allowed
    assert "maximum daily" in decision.reason


def test_duplicate_ticker_requires_grade_improvement(tmp_path) -> None:
    policy = make_policy(tmp_path)
    now = datetime(2026, 6, 9, 10, 0, tzinfo=EASTERN)
    policy.record("AAPL", "B Watch Alert", now)

    duplicate = policy.evaluate("AAPL", "B Watch Alert", 58, normal_regime(), now, confirmations=strong_b_confirmations())
    upgrade = policy.evaluate("AAPL", "A Signal", 61, normal_regime(), now)

    assert not duplicate.allowed
    assert upgrade.allowed
    assert upgrade.reason == "grade upgrade"


def test_choppy_market_allows_strong_b_but_not_a(tmp_path) -> None:
    policy = make_policy(tmp_path)
    now = datetime(2026, 6, 9, 10, 0, tzinfo=EASTERN)

    choppy = MarketRegime("choppy", 1.2, "normal", 0.05, False, "Weak trend", spy_trend="bull", qqq_trend="mixed")
    assert policy.evaluate("AAPL", "B Watch Alert", 58, choppy, now, confirmations=strong_b_confirmations()).allowed
    assert not policy.evaluate("MSFT", "A Signal", 63, choppy, now).allowed
    assert policy.evaluate("NVDA", "A+ Signal", 70, choppy, now).allowed


def test_weak_b_score_is_rejected(tmp_path) -> None:
    policy = make_policy(tmp_path, min_score_b=58)
    decision = policy.evaluate("AAPL", "B Watch Alert", 57, normal_regime(), confirmations=strong_b_confirmations())
    assert not decision.allowed
    assert "strong-watch threshold" in decision.reason


def test_moderate_bear_allows_strong_b(tmp_path) -> None:
    policy = make_policy(tmp_path)
    moderate_bear = MarketRegime(
        "bear",
        2.0,
        "normal",
        0.2,
        False,
        "Moderate bear",
        regime_severity="moderate",
        spy_trend="bull",
        qqq_trend="mixed",
    )
    assert policy.evaluate("AAPL", "B Watch Alert", 58, moderate_bear, confirmations=strong_b_confirmations()).allowed


def test_severe_bear_and_panic_remain_blocked(tmp_path) -> None:
    policy = make_policy(tmp_path)
    severe_bear = MarketRegime(
        "bear",
        5.0,
        "high",
        0.4,
        False,
        "Severe bear",
        regime_severity="severe",
    )
    panic = MarketRegime(
        "panic",
        6.0,
        "high",
        0.5,
        False,
        "Panic",
        regime_severity="panic",
    )
    assert not policy.evaluate("AAPL", "A++ Signal", 90, severe_bear).allowed
    assert not policy.evaluate("NVDA", "A++ Signal", 90, panic).allowed


def test_b_suppressed_if_only_price_above_ema50(tmp_path) -> None:
    policy = make_policy(tmp_path)
    decision = policy.evaluate(
        "AAPL",
        "B Watch Alert",
        58,
        normal_regime(),
        confirmations=("price above EMA50",),
    )
    assert not decision.allowed
    assert "only confirmation" in decision.reason


def test_b_requires_four_confirmations(tmp_path) -> None:
    policy = make_policy(tmp_path)
    regime = normal_regime()
    weak = policy.evaluate(
        "AAPL",
        "B Watch Alert",
        58,
        regime,
        confirmations=("price above EMA50", "EMA50 above EMA200", "RSI between 45 and 65"),
    )
    strong = policy.evaluate("MSFT", "B Watch Alert", 58, regime, confirmations=strong_b_confirmations())

    assert not weak.allowed
    assert "needs 4 confirmations" in weak.reason
    assert strong.allowed


def test_weak_spy_qqq_suppresses_b(tmp_path) -> None:
    policy = make_policy(tmp_path)
    weak_market = MarketRegime(
        "normal",
        1.0,
        "normal",
        0.4,
        True,
        "Benchmarks weak",
        spy_trend="bear",
        qqq_trend="mixed",
    )
    decision = policy.evaluate("AAPL", "B Watch Alert", 60, weak_market, confirmations=strong_b_confirmations())
    assert not decision.allowed
    assert "SPY/QQQ not healthy" in decision.reason


def test_ticker_cooldown_after_two_recent_stop_losses_only_suppresses_b(tmp_path) -> None:
    policy = make_policy(tmp_path)
    now = datetime(2026, 6, 9, 10, 0, tzinfo=EASTERN)
    for offset in (1, 3):
        policy.db.insert_dict(
            "completed_trades",
            {
                "active_signal_id": 100 + offset,
                "ticker": "AAPL",
                "grade": "A Signal",
                "direction": "long",
                "entry_zone_low": 100,
                "entry_zone_high": 101,
                "entry_price": 100.5,
                "stop_loss": 98,
                "tp1": 104,
                "tp2": 106,
                "tp3": 108,
                "outcome": "SL",
                "status": "closed",
                "opened_at": (now - timedelta(days=offset)).isoformat(),
                "closed_at": (now - timedelta(days=offset)).isoformat(),
                "close_price": 98,
                "r_multiple": -1,
            },
        )

    b_decision = policy.evaluate("AAPL", "B Watch Alert", 60, normal_regime(), now, confirmations=strong_b_confirmations())
    a_plus_decision = policy.evaluate("AAPL", "A+ Signal", 70, normal_regime(), now)

    assert not b_decision.allowed
    assert "2 stop losses" in b_decision.reason
    assert a_plus_decision.allowed


def test_sector_throttle_limits_b_alerts(tmp_path) -> None:
    policy = make_policy(tmp_path, max_b_alerts_per_sector_per_day=2)
    now = datetime(2026, 6, 9, 10, 0, tzinfo=EASTERN)
    policy.record("AAPL", "B Watch Alert", now)
    policy.record("MSFT", "B Watch Alert", now)

    decision = policy.evaluate("NVDA", "B Watch Alert", 60, normal_regime(), now, confirmations=strong_b_confirmations())

    assert not decision.allowed
    assert "sector" in decision.reason
