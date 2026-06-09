from datetime import datetime
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
    return MarketRegime("normal", 1.0, "normal", 0.4, True, "Normal tradable regime")


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

    duplicate = policy.evaluate("AAPL", "B Watch Alert", 57, normal_regime(), now)
    upgrade = policy.evaluate("AAPL", "A Signal", 61, normal_regime(), now)

    assert not duplicate.allowed
    assert upgrade.allowed
    assert upgrade.reason == "grade upgrade"


def test_weak_market_only_allows_a_plus_or_better(tmp_path) -> None:
    policy = make_policy(tmp_path)
    choppy = MarketRegime("choppy", 1.2, "normal", 0.05, False, "Weak trend")
    now = datetime(2026, 6, 9, 10, 0, tzinfo=EASTERN)

    assert not policy.evaluate("AAPL", "B Watch Alert", 58, choppy, now).allowed
    assert not policy.evaluate("MSFT", "A Signal", 63, choppy, now).allowed
    assert policy.evaluate("NVDA", "A+ Signal", 70, choppy, now).allowed


def test_weak_b_score_is_rejected(tmp_path) -> None:
    policy = make_policy(tmp_path, min_score_b=55)
    decision = policy.evaluate("AAPL", "B Watch Alert", 54, normal_regime())
    assert not decision.allowed
    assert "strong-watch threshold" in decision.reason
