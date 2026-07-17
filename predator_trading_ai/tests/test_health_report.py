from __future__ import annotations

import predator_trading_ai.alerts.telegram_bot as telegram_module
from predator_trading_ai.config import Settings
from predator_trading_ai.reports.health_report import HealthReport
from predator_trading_ai.tests.test_monitor_status import (
    iso_ahead,
    iso_ago,
    make_db,
    make_settings,
    seed_healthy,
    seed_process_ids,
    table_counts,
)


def test_health_report_fully_healthy_runtime(tmp_path) -> None:
    db = make_db(tmp_path)
    seed_healthy(db)
    telegram_module.TELEGRAM_POLLING_STARTED = True
    settings = make_settings(tmp_path, telegram_bot_token="token", telegram_chat_id="123")

    report = HealthReport(settings, db).build()

    assert "Predator Trading AI Health" in report
    assert "✅ HEALTHY" in report
    assert "Scanner:" in report
    assert "- Running: YES" in report
    assert "TP/SL Monitor:" in report
    assert "ActiveSignalTracker:" in report
    assert "Database:" in report
    assert "Runtime:" in report
    telegram_module.TELEGRAM_POLLING_STARTED = False


def test_health_report_market_closed_idle_with_zero_active_signals_is_healthy(tmp_path) -> None:
    db = make_db(tmp_path)
    now = iso_ago(10)
    seed_process_ids(db)
    db.set_state("process_started_at", iso_ago(60))
    db.set_state("main_loop_heartbeat_at", now)
    db.set_state("market_status", "CLOSED")
    db.set_state("next_market_check_at", iso_ahead(3600))
    db.set_state("tp_sl_monitor_heartbeat_at", now)
    db.set_state("tp_sl_monitor_state", "IDLE")
    db.set_state("tracker_started_at", now)
    db.set_state("tracker_running", "true")
    telegram_module.TELEGRAM_POLLING_STARTED = True
    settings = make_settings(tmp_path, telegram_bot_token="token", telegram_chat_id="123")

    report = HealthReport(settings, db).build()

    assert "✅ HEALTHY" in report
    assert "- Running: IDLE / MARKET CLOSED" in report
    assert "- Active monitored signals: 0" in report
    telegram_module.TELEGRAM_POLLING_STARTED = False


def test_health_report_market_open_delayed_scanner_is_warning(tmp_path) -> None:
    db = make_db(tmp_path)
    seed_process_ids(db)
    db.set_state("process_started_at", iso_ago(600))
    db.set_state("main_loop_heartbeat_at", iso_ago(200))
    db.set_state("market_status", "OPEN")
    db.set_state("last_completed_scan_at", iso_ago(20))
    db.set_state("tp_sl_monitor_heartbeat_at", iso_ago(20))
    db.set_state("tp_sl_monitor_state", "IDLE")
    db.set_state("tracker_started_at", iso_ago(20))
    db.set_state("tracker_running", "true")
    telegram_module.TELEGRAM_POLLING_STARTED = True

    report = HealthReport(make_settings(tmp_path, telegram_bot_token="token", telegram_chat_id="123"), db).build()

    assert "⚠️ WARNING" in report
    telegram_module.TELEGRAM_POLLING_STARTED = False


def test_health_report_stale_tp_sl_with_active_signals_is_error(tmp_path) -> None:
    db = make_db(tmp_path)
    seed_healthy(db)
    db.set_state("process_started_at", iso_ago(1200))
    db.set_state("tp_sl_monitor_heartbeat_at", iso_ago(400))
    db.set_state("tp_sl_monitor_heartbeat_utc", iso_ago(400))
    telegram_module.TELEGRAM_POLLING_STARTED = True

    report = HealthReport(make_settings(tmp_path, telegram_bot_token="token", telegram_chat_id="123"), db).build()

    assert "❌ ERROR" in report
    telegram_module.TELEGRAM_POLLING_STARTED = False


def test_health_report_telegram_conflict_is_warning_not_crash(tmp_path) -> None:
    db = make_db(tmp_path)
    seed_healthy(db)
    telegram_module.TELEGRAM_POLLING_STARTED = False
    telegram_module.TELEGRAM_POLLING_SKIPPED_REASON = "conflict_detected"
    telegram_module.TELEGRAM_POLLING_DISABLED_REASON = "terminated by other getUpdates request"

    report = HealthReport(make_settings(tmp_path, telegram_bot_token="token", telegram_chat_id="123"), db).build()

    assert "⚠️ WARNING" in report
    assert "- Conflict status: YES" in report
    telegram_module.TELEGRAM_POLLING_SKIPPED_REASON = "not_started"
    telegram_module.TELEGRAM_POLLING_DISABLED_REASON = None


def test_health_report_database_unavailable_is_error() -> None:
    class BrokenDB:
        def fetch_all(self, sql, params=()):
            raise RuntimeError("database unavailable")

    report = HealthReport(Settings(), BrokenDB()).build()

    assert "❌ ERROR" in report
    assert "Database:" in report


def test_health_report_is_read_only(tmp_path) -> None:
    db = make_db(tmp_path)
    seed_healthy(db)
    telegram_module.TELEGRAM_POLLING_STARTED = True
    before = table_counts(db)

    HealthReport(make_settings(tmp_path, telegram_bot_token="token", telegram_chat_id="123"), db).build()

    assert table_counts(db) == before
    telegram_module.TELEGRAM_POLLING_STARTED = False
