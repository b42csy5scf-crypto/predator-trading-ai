from pathlib import Path

import pandas as pd

from predator_trading_ai.config import Settings
from predator_trading_ai.database.db import Database
from predator_trading_ai.engines.regime_detector import RegimeDetector
from predator_trading_ai.engines.shadow_mode import ShadowModeLogger
from predator_trading_ai.engines.strategy_engine import StrategySetup


def test_shadow_mode_logs_rejected_signal(tmp_path: Path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'shadow.db'}")
    db = Database(settings)
    db.initialize()
    shadow = ShadowModeLogger(db)
    regime = RegimeDetector().detect(pd.DataFrame())
    diagnostics = shadow.diagnostics("AAPL", pd.DataFrame())

    shadow_id = shadow.log(
        "AAPL",
        "rejected",
        regime,
        diagnostics,
        rejection_stage="strategy",
        rejection_reason="setup score below institutional threshold",
    )

    rows = db.fetch_all("SELECT * FROM shadow_signals WHERE id = ?", [shadow_id])
    rejected = db.fetch_all("SELECT * FROM rejected_signals WHERE shadow_signal_id = ?", [shadow_id])
    assert rows[0]["status"] == "rejected"
    assert rejected[0]["rejection_stage"] == "strategy"


def test_shadow_mode_updates_rejected_would_have_won(tmp_path: Path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'shadow.db'}")
    db = Database(settings)
    db.initialize()
    shadow = ShadowModeLogger(db)
    regime = RegimeDetector().detect(pd.DataFrame())
    diagnostics = shadow.diagnostics("AAPL", pd.DataFrame(), score=80)
    setup = StrategySetup(
        ticker="AAPL",
        direction="long",
        setup_type="high-quality breakout",
        score=80,
        entry_zone_low=100,
        entry_zone_high=100,
        stop_loss=98,
        targets=(103, 105, 108),
        reason="test",
        do_not_enter_conditions=[],
    )
    shadow_id = shadow.log(
        "AAPL",
        "rejected",
        regime,
        diagnostics,
        setup=setup,
        rejection_stage="risk",
        rejection_reason="correlation cap",
    )
    db.execute("UPDATE shadow_signals SET created_at = ? WHERE id = ?", ["2026-01-01T14:30:00+00:00", shadow_id])
    bars = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(["2026-01-01T14:35:00Z"]),
            "open": [100],
            "high": [104],
            "low": [99],
            "close": [103],
            "volume": [1000],
        }
    )

    shadow.update_outcomes("AAPL", bars)
    row = db.fetch_all("SELECT outcome FROM shadow_signals WHERE id = ?", [shadow_id])[0]
    rejected = db.fetch_all("SELECT would_have_won FROM rejected_signals WHERE shadow_signal_id = ?", [shadow_id])[0]
    assert row["outcome"] == "target_hit"
    assert rejected["would_have_won"] == 1
