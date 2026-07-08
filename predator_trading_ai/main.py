import argparse
import asyncio
import os
import subprocess
import time
from collections import Counter
from datetime import datetime, time as dt_time, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from predator_trading_ai.alerts.telegram_bot import TelegramAlertBot
from predator_trading_ai.config import Settings, get_settings
from predator_trading_ai.data.market_data import MarketDataClient, MarketSnapshot
from predator_trading_ai.data.options_data import OptionsFlowDetector, UnusualWhalesClient
from predator_trading_ai.database.db import Database
from predator_trading_ai.engines.active_signal_tracker import ActiveSignalTracker
from predator_trading_ai.engines.alert_policy import AlertPolicy
from predator_trading_ai.engines.regime_detector import MarketRegime, RegimeDetector
from predator_trading_ai.engines.risk_engine import RiskEngine
from predator_trading_ai.engines.shadow_mode import ShadowModeLogger
from predator_trading_ai.engines.signal_engine import SignalEngine
from predator_trading_ai.engines.strategy_engine import StrategyEngine, StrategySetup
from predator_trading_ai.reports.report_runner import PerformanceReportRunner
from predator_trading_ai.state.runtime_state import RuntimeState, RuntimeStateStore
from predator_trading_ai.utils.logger import setup_logger
from predator_trading_ai.utils.reliability import CircuitBreaker, HealthMonitor, RetryPolicy
from predator_trading_ai.utils.validators import clamp, spread_pct
from predator_trading_ai.utils.watchlist import (
    CORRELATION_GROUP_BY_TICKER,
    SECTOR_BY_TICKER,
    parse_watchlist,
    validate_watchlist,
)


EASTERN = ZoneInfo("America/New_York")
MARKET_OPEN = dt_time(9, 30)
MARKET_CLOSE = dt_time(16, 0)


class PredatorTradingAI:
    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings = settings or get_settings()
        self.logger = setup_logger(level=self.settings.log_level)
        self.db = Database(self.settings)
        self.market_data = MarketDataClient(self.settings)
        self.unusual_whales = UnusualWhalesClient(self.settings)
        self.options_detector = OptionsFlowDetector(max_spread_pct=self.settings.max_spread_pct)
        self.regime_detector = RegimeDetector()
        self.strategy_engine = StrategyEngine(self.settings)
        self.risk_engine = RiskEngine(self.settings)
        self.signal_engine = SignalEngine(self.db)
        self.alert_policy = AlertPolicy(self.settings, self.db)
        self.active_signal_tracker = ActiveSignalTracker(self.db, self.settings)
        self.performance_report_runner: Optional[PerformanceReportRunner] = None
        self.tp_sl_monitor_started = False
        self.shadow_logger = ShadowModeLogger(self.db)
        self.telegram_bot = TelegramAlertBot(self.settings, self.db)
        self.retry = RetryPolicy(
            attempts=self.settings.retry_attempts,
            base_delay_seconds=self.settings.retry_base_delay_seconds,
        )
        self.health = HealthMonitor()
        self.circuit_breaker = CircuitBreaker(self.settings.watchdog_max_failures)
        self.state_store = RuntimeStateStore()
        self.state: RuntimeState = self.state_store.load()
        self.scan_signals_generated = 0
        self.scan_signals_suppressed = 0
        self.scan_suppression_reasons: Counter[str] = Counter()
        self.watchlist = parse_watchlist(self.settings.watchlist)
        watchlist_issues = validate_watchlist(self.watchlist)
        if watchlist_issues:
            self.logger.warning("Watchlist validation issues: %s", "; ".join(watchlist_issues))

    def run(self, run_once: bool = False) -> None:
        self.db.initialize()
        self.logger.info("Predator Trading AI started. Live trading enabled: %s", self.settings.live_trading)
        self.logger.info("Runtime revision: %s", self.runtime_revision())
        self.logger.info(
            "Alert config: MIN_SCORE_B configured=%.0f effective=%.0f B_MIN_CONFIRMATIONS=%d "
            "B_MIN_REL_VOLUME=%.2f ENABLE_B_ALERTS=%s ENABLE_B_TP_SL_TRACKING=%s",
            self.settings.min_score_b,
            self.alert_policy.effective_min_score_b(),
            self.settings.b_min_confirmations,
            self.settings.b_min_rel_volume,
            self.settings.enable_b_alerts,
            self.settings.enable_b_tp_sl_tracking,
        )
        self.logger.info("Watchlist: %s", ", ".join(self.watchlist))
        self.logger.info("Loop interval: %s seconds", self.settings.loop_interval_seconds)
        self.start_monitoring_workers()
        self.telegram_bot.start_command_polling(source_module="predator_trading_ai.main")
        if self.state.safe_mode:
            self.logger.warning("Recovered in SAFE MODE: %s", self.state.safe_mode_reason)
        self.record_health("system", "ok", "system started")

        while True:
            try:
                self._run_loop(run_once)
                return
            except KeyboardInterrupt:
                self.logger.info("Shutdown requested by user. Stopping cleanly.")
                return
            except Exception as exc:
                self.logger.exception("Main loop crashed unexpectedly: %s", exc)
                self.record_health("supervisor", "error", str(exc))
                tripped = self.circuit_breaker.record_failure(
                    self.state,
                    "consecutive supervisor failures exceeded watchdog limit",
                )
                self.state_store.save(self.state)
                if tripped:
                    self.system_alert("Predator Trading AI SAFE MODE: supervisor restart limit reached.")
                    return
                if run_once:
                    raise
                self.logger.info("Supervisor restarting scanner after %.0f seconds.", self.settings.retry_base_delay_seconds)
                time.sleep(self.settings.retry_base_delay_seconds)

    def _run_loop(self, run_once: bool) -> None:
        while True:
            self.heartbeat()
            now = datetime.now(EASTERN)
            if not self.clock_is_sane(now):
                self.enter_safe_mode("system clock or timezone validation failed")
                if run_once:
                    return
                time.sleep(self.settings.loop_interval_seconds)
                continue

            if not self.is_market_open(now):
                sleep_seconds = 0 if run_once else self.seconds_until_next_open(now)
                self.logger.info(
                    "Market is closed at %s ET. Next check in %.0f seconds.",
                    now.strftime("%Y-%m-%d %H:%M:%S"),
                    sleep_seconds,
                )
                if run_once:
                    return
                time.sleep(sleep_seconds)
                continue

            if self.state.safe_mode:
                self.logger.warning("SAFE MODE active: %s", self.state.safe_mode_reason)
                self.record_health("watchdog", "safe_mode", self.state.safe_mode_reason or "safe mode active")
                if run_once:
                    return
                time.sleep(self.settings.loop_interval_seconds)
                continue

            self.run_iteration()
            if run_once:
                return
            self.logger.info("Iteration complete. Sleeping %s seconds.", self.settings.loop_interval_seconds)
            time.sleep(self.settings.loop_interval_seconds)

    def run_iteration(self) -> None:
        self.logger.info("Starting market data iteration for %d tickers.", len(self.watchlist))
        self.reset_scan_alert_summary()
        self.market_context = self.load_market_context()
        self.run_tp_sl_monitor()
        had_failure = False
        for ticker in self.watchlist:
            try:
                self.process_ticker(ticker)
            except Exception as exc:
                had_failure = True
                self.logger.exception("Unhandled error while processing %s; continuing: %s", ticker, exc)
                self.record_health("scanner", "error", f"{ticker}: {exc}")
        if had_failure:
            tripped = self.circuit_breaker.record_failure(
                self.state,
                "consecutive failures exceeded watchdog limit",
            )
            self.state_store.save(self.state)
            if tripped:
                self.system_alert("SAFE MODE enabled after repeated scanner failures.")
        else:
            self.circuit_breaker.record_success(self.state)
            self.state_store.mark_scan(self.state)
            self.record_health("scanner", "ok", "iteration completed")
        self.log_scan_alert_summary()

    def start_monitoring_workers(self) -> None:
        self.logger.info("Starting ActiveSignalTracker...")
        active_count = self.active_signal_tracker.active_count()
        self.logger.info("ActiveSignalTracker started. active_signals=%d", active_count)

        self.logger.info("Starting TP/SL monitor...")
        self.tp_sl_monitor_started = True
        self.logger.info("TP/SL monitor started.")

        self.logger.info("Starting PerformanceReportRunner...")
        self.performance_report_runner = PerformanceReportRunner(self.settings, self.db)
        self.logger.info("PerformanceReportRunner started.")

    def run_tp_sl_monitor(self) -> None:
        if not self.tp_sl_monitor_started:
            self.logger.warning("TP/SL monitor was not started; starting now.")
            self.tp_sl_monitor_started = True
        active_tickers = self.active_signal_tracker.active_tickers()
        self.logger.info("TP/SL monitor running. active_tickers=%d", len(active_tickers))
        for ticker in active_tickers:
            try:
                snapshot = self.retry.run(
                    f"tp/sl latest snapshot {ticker}",
                    lambda ticker=ticker: self.market_data.get_latest_snapshot(ticker),
                    fallback=None,
                )
                if snapshot is None:
                    self.logger.warning("TP/SL monitor skipped %s: latest snapshot missing.", ticker)
                    continue
                self.process_active_signal_updates(ticker, snapshot.price)
            except Exception as exc:
                self.logger.exception("TP/SL monitor failed for %s; continuing: %s", ticker, exc)

    def process_ticker(self, ticker: str) -> None:
        self.logger.info("Processing %s.", ticker)
        diagnostic = self.new_candidate_diagnostic(ticker)
        try:
            bars = self.retry.run(
                f"market bars {ticker}",
                lambda: self.market_data.get_recent_bars(ticker, lookback_days=10, timeframe="5Min"),
                fallback=None,
            )
            if bars is None:
                self.add_rejection(diagnostic, "market bars failed after retries")
                raise RuntimeError(f"{ticker} market bars failed after retries")
            if bars.empty:
                self.add_rejection(diagnostic, "missing/empty market data")
                self.logger.warning("Skipping %s: no market bars available.", ticker)
                return

            snapshot = self.retry.run(
                f"latest snapshot {ticker}",
                lambda: self.market_data.get_latest_snapshot(ticker),
                fallback=None,
            )
            regime = self.regime_detector.detect(
                bars,
                spy_bars=self.market_context.get("SPY"),
                qqq_bars=self.market_context.get("QQQ"),
                vix_level=self.market_context.get("VIX"),
                breadth_score=self.market_context.get("breadth_score"),
            )
            self.log_regime(ticker, regime)
            if snapshot is None:
                self.add_rejection(diagnostic, "missing latest quote/trade snapshot")
                self.logger.warning("Skipping %s signal generation: latest quote/trade snapshot missing.", ticker)
                return
            self.process_active_signal_updates(ticker, snapshot.price)
            if self.is_extreme_illiquidity(snapshot):
                reason = f"extreme illiquidity or invalid spread: bid={snapshot.bid} ask={snapshot.ask}"
                self.add_rejection(diagnostic, "liquidity/spread filter failed")
                self.log_rejected_or_watch(ticker, bars, regime, "liquidity", reason, allow_watch=False)
                self.logger.info(
                    "Ticker %s score=0 grade_candidate=blocked rejected_by=liquidity reason=%s",
                    ticker,
                    reason,
                )
                return

            options_confirmation = self.get_options_confirmation(ticker)
            setup = self.strategy_engine.evaluate(
                ticker=ticker,
                bars=bars,
                regime=regime,
                options_confirmation=options_confirmation,
            )
            if setup is None:
                watch_evaluation = self.log_rejected_or_watch(ticker, bars, regime, "strategy", "no valid strategy setup")
                self.update_diagnostic_from_watch(diagnostic, watch_evaluation)
                self.add_strategy_rejections(diagnostic, watch_evaluation)
                return

            diagnostic["score"] = setup.score
            diagnostic["grade"] = setup.signal_tier
            if self.active_signal_tracker.has_active_signal(ticker):
                self.add_rejection(diagnostic, "Already active signal")

            risk = self.evaluate_risk(setup, snapshot, regime, options_confirmation)
            if not risk.approved:
                grade_candidate = setup.signal_tier
                reason = "; ".join(risk.reasons)
                self.add_rejection(diagnostic, f"Risk engine rejected: {reason}")
                self.record_signal_suppressed(f"risk: {reason}")
                diagnostics = self.shadow_logger.diagnostics(ticker, bars, {**self.state.active_positions, **self.state.active_signals}, score=setup.score)
                self.shadow_logger.log(
                    ticker,
                    "rejected",
                    regime,
                    diagnostics,
                    setup=setup,
                    risk=risk,
                    rejection_stage="risk",
                    rejection_reason=reason,
                )
                self.logger.info(
                    "Ticker %s score=%.0f grade_candidate=%s rejected_by=risk reason=%s",
                    ticker,
                    setup.score,
                    grade_candidate,
                    reason,
                )
                return

            alert_decision = self.alert_policy.evaluate(
                ticker,
                setup.signal_tier,
                setup.score,
                regime,
                confirmations=setup.confirmations,
                sector=SECTOR_BY_TICKER.get(ticker),
                setup_reason=setup.reason,
            )
            if not alert_decision.allowed:
                self.add_rejection(diagnostic, alert_decision.reason)
                self.record_signal_suppressed(alert_decision.reason)
                self.logger.info(
                    "Telegram alert suppressed for %s grade=%s score=%.0f: %s",
                    ticker,
                    setup.signal_tier,
                    setup.score,
                    alert_decision.reason,
                )
                return
            alert_key = self.alert_cooldown_key(ticker, setup.signal_tier)
            if self.state_store.is_on_cooldown(self.state, alert_key, self.alert_cooldown_seconds):
                self.add_rejection(diagnostic, "Cooldown active")
                self.record_signal_suppressed("duplicate cooldown")
                self.logger.info("Skipping duplicate %s alert for %s due to cooldown.", setup.signal_tier, ticker)
                return

            expected_win_rate = self.expected_win_rate(ticker, setup.setup_type)
            self.logger.info(
                "Entering signal generation ticker=%s grade=%s score=%.0f",
                ticker,
                setup.signal_tier,
                setup.score,
            )
            signal = self.signal_engine.build_signal(setup, risk, regime, expected_win_rate)
            if signal is None:
                self.add_rejection(diagnostic, "Signal engine returned no signal")
                self.logger.info(
                    "Signal engine returned None ticker=%s reason=%s",
                    ticker,
                    "signal_engine.build_signal returned None",
                )
                self.logger.info(
                    "Early return after candidate accepted ticker=%s reason=%s",
                    ticker,
                    "signal engine returned None",
                )
                self.logger.info("Signal not created for %s after risk evaluation.", ticker)
                return
            self.logger.info(
                "Signal engine returned signal ticker=%s grade=%s confidence=%.0f",
                ticker,
                setup.signal_tier,
                signal.confidence,
            )

            diagnostic["passed"] = True
            self.logger.info(
                "Signal generated: %s grade=%s setup=%s confidence=%.0f",
                ticker,
                setup.signal_tier,
                signal.setup_type,
                signal.confidence,
            )
            self.state.active_signals[alert_key] = {
                "ticker": ticker,
                "setup_type": setup.setup_type,
                "direction": setup.direction,
                "grade": setup.signal_tier,
                "confidence": signal.confidence,
                "sector": SECTOR_BY_TICKER.get(ticker),
                "correlation_group": CORRELATION_GROUP_BY_TICKER.get(ticker),
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            message = SignalEngine.format_alert(signal, label=setup.signal_tier)
            self.log_sent_alert(ticker, setup.signal_tier, "trade_candidate", setup.score, setup.setup_type, regime.regime, message)
            self.alert_policy.record(ticker, setup.signal_tier)
            self.logger.info(
                "Preparing Telegram dispatch ticker=%s grade=%s",
                ticker,
                setup.signal_tier,
            )
            self.logger.info("Sending signal to Telegram: %s grade=%s", ticker, setup.signal_tier)
            self.logger.info(
                "Preparing ActiveSignalTracker add ticker=%s grade=%s",
                ticker,
                setup.signal_tier,
            )
            signal_id = self.active_signal_tracker.register_trading_signal(signal, setup.signal_tier)
            self.logger.info(
                "Added to ActiveSignalTracker confirmed ticker=%s id=%s active_signals=%d",
                ticker,
                signal_id,
                self.active_signal_tracker.active_count(),
            )
            self.state.last_telegram_alert = alert_key
            self.state_store.set_cooldown(self.state, alert_key)
            asyncio.run(self.telegram_bot.send_message(message))
            self.logger.info("Signal sent to Telegram: %s grade=%s", ticker, setup.signal_tier)
            self.record_signal_generated()
        finally:
            self.log_candidate_diagnostic(diagnostic)

    @staticmethod
    def new_candidate_diagnostic(ticker: str) -> dict:
        return {
            "ticker": ticker,
            "score": None,
            "grade": "unknown",
            "passed": False,
            "rejections": [],
        }

    @staticmethod
    def add_rejection(diagnostic: dict, reason: str) -> None:
        if reason and reason not in diagnostic["rejections"]:
            diagnostic["rejections"].append(reason)

    @staticmethod
    def update_diagnostic_from_watch(diagnostic: dict, watch_evaluation) -> None:
        if watch_evaluation is None:
            return
        diagnostic["score"] = watch_evaluation.score
        diagnostic["grade"] = watch_evaluation.grade_candidate

    def add_strategy_rejections(self, diagnostic: dict, watch_evaluation) -> None:
        if watch_evaluation is None:
            self.add_rejection(diagnostic, "No strategy candidate produced")
            return
        if watch_evaluation.rejected_by and watch_evaluation.rejected_by != "none":
            self.add_rejection(diagnostic, f"{watch_evaluation.rejected_by} filter failed")
        for part in self.split_rejection_reasons(watch_evaluation.reason):
            self.add_rejection(diagnostic, part)
        if watch_evaluation.grade_candidate in {"B Watch Alert", "C Risky/Early Alert"}:
            self.add_rejection(diagnostic, "Grade below A")

    @staticmethod
    def split_rejection_reasons(reason: str) -> list[str]:
        return [
            part.strip()
            for part in (reason or "").replace("watch risks:", ";").split(";")
            if part.strip()
        ]

    def log_candidate_diagnostic(self, diagnostic: dict) -> None:
        score = diagnostic["score"]
        score_text = "unknown" if score is None else f"{float(score):.0f}"
        header = (
            "Candidate diagnostic\n"
            f"Ticker={diagnostic['ticker']}\n"
            f"Final score={score_text}\n"
            f"Grade={diagnostic['grade']}\n"
            f"{'Passed' if diagnostic['passed'] else 'Failed'}"
        )
        if diagnostic["passed"]:
            self.logger.info(
                "%s\nCandidate accepted:\nTicker=%s\nScore=%s\nGrade=%s",
                header,
                diagnostic["ticker"],
                score_text,
                diagnostic["grade"],
            )
            return
        reasons = diagnostic["rejections"] or ["No candidate accepted"]
        self.logger.info(
            "%s\nRejected:\n%s",
            header,
            "\n".join(f"- {reason}" for reason in reasons),
        )

    def log_rejected_or_watch(
        self,
        ticker: str,
        bars,
        regime: MarketRegime,
        stage: str,
        reason: str,
        allow_watch: bool = True,
    ):
        diagnostics = self.shadow_logger.diagnostics(ticker, bars, {**self.state.active_positions, **self.state.active_signals})
        watch_evaluation = self.strategy_engine.evaluate_watch_candidate(ticker, bars, regime) if allow_watch else None
        if watch_evaluation is not None:
            self.logger.info(
                "Ticker %s score=%.0f grade_candidate=%s rejected_by=%s reason=%s",
                ticker,
                watch_evaluation.score,
                watch_evaluation.grade_candidate,
                watch_evaluation.rejected_by,
                watch_evaluation.reason,
            )
        watch = watch_evaluation.setup if watch_evaluation is not None else None
        if watch is not None:
            diagnostics = self.shadow_logger.diagnostics(ticker, bars, {**self.state.active_positions, **self.state.active_signals}, score=watch.score)
            self.shadow_logger.log(ticker, "watch_alert", regime, diagnostics, setup=watch)
            self.send_watch_alert(ticker, watch, regime)
            return watch_evaluation
        if watch_evaluation is not None:
            reason = f"{reason}; candidate_score={watch_evaluation.score:.0f}; grade_candidate={watch_evaluation.grade_candidate}; {watch_evaluation.reason}"
        self.shadow_logger.log(ticker, "rejected", regime, diagnostics, rejection_stage=stage, rejection_reason=reason)
        return watch_evaluation

    def send_watch_alert(self, ticker: str, setup: StrategySetup, regime: MarketRegime) -> None:
        if not self.settings.enable_watchlist_alerts:
            self.record_signal_suppressed("watchlist alerts disabled")
            return
        if setup.signal_tier == "B Watch Alert" and not self.settings.enable_b_alerts:
            self.record_signal_suppressed("B alerts disabled")
            return
        if setup.signal_tier == "C Risky/Early Alert":
            self.record_signal_suppressed("C alerts disabled")
            self.logger.info("Skipping Telegram alert for %s: C-grade alerts are disabled.", ticker)
            return
        alert_decision = self.alert_policy.evaluate(
            ticker,
            setup.signal_tier,
            setup.score,
            regime,
            confirmations=setup.confirmations,
            sector=SECTOR_BY_TICKER.get(ticker),
            setup_reason=setup.reason,
        )
        if setup.signal_tier == "B Watch Alert":
            self.log_b_alert_policy_decision(ticker, setup, regime, alert_decision)
        if not alert_decision.allowed:
            self.record_signal_suppressed(alert_decision.reason)
            self.logger.info(
                "Telegram watch alert suppressed for %s grade=%s score=%.0f: %s",
                ticker,
                setup.signal_tier,
                setup.score,
                alert_decision.reason,
            )
            return
        alert_key = self.alert_cooldown_key(ticker, setup.signal_tier)
        if self.state_store.is_on_cooldown(self.state, alert_key, self.alert_cooldown_seconds):
            self.record_signal_suppressed("duplicate cooldown")
            return
        message = SignalEngine.format_watch_alert(setup, bear_regime=self.is_bear_watch_regime(regime))
        self.log_sent_alert(ticker, setup.signal_tier, "observe_only", setup.score, setup.setup_type, regime.regime, message)
        self.alert_policy.record(ticker, setup.signal_tier)
        if self.settings.enable_b_tp_sl_tracking and setup.signal_tier == "B Watch Alert":
            self.active_signal_tracker.register_watch_signal(setup)
        self.state.last_telegram_alert = alert_key
        self.state_store.set_cooldown(self.state, alert_key)
        asyncio.run(self.telegram_bot.send_message(message))
        self.record_signal_generated()

    def log_b_alert_policy_decision(self, ticker: str, setup: StrategySetup, regime: MarketRegime, decision) -> None:
        confirmations = tuple(setup.confirmations)
        self.logger.info(
            "B_ALERT_POLICY_DECISION ticker=%s score=%.0f min_score_b_configured=%.0f "
            "min_score_b_effective=%.0f confirmations=%d confirmations_detail=%s "
            "spy_qqq_healthy=%s spy_trend=%s qqq_trend=%s regime=%s severity=%s "
            "allowed=%s reason=%s",
            ticker,
            setup.score,
            self.settings.min_score_b,
            self.alert_policy.effective_min_score_b(),
            len(confirmations),
            "|".join(confirmations) if confirmations else "none",
            self.alert_policy.market_healthy_for_b(regime),
            regime.spy_trend,
            regime.qqq_trend,
            regime.regime,
            regime.regime_severity,
            decision.allowed,
            decision.reason,
        )

    def process_active_signal_updates(self, ticker: str, current_price: float) -> None:
        updates = self.active_signal_tracker.check_ticker(ticker, current_price)
        for update in updates:
            self.logger.info(
                "Active signal update for %s: %s at %.2f",
                ticker,
                update.update_type,
                current_price,
            )
            asyncio.run(self.telegram_bot.send_message(update.message))

    def reset_scan_alert_summary(self) -> None:
        self.scan_signals_generated = 0
        self.scan_signals_suppressed = 0
        self.scan_suppression_reasons.clear()

    def record_signal_generated(self) -> None:
        self.scan_signals_generated += 1

    def record_signal_suppressed(self, reason: str) -> None:
        self.scan_signals_suppressed += 1
        self.scan_suppression_reasons[reason] += 1

    def log_scan_alert_summary(self) -> None:
        reasons = ", ".join(
            f"{reason}={count}"
            for reason, count in self.scan_suppression_reasons.most_common()
        ) or "none"
        self.logger.info(
            "Signal summary: generated=%d suppressed=%d suppression_reasons=%s",
            self.scan_signals_generated,
            self.scan_signals_suppressed,
            reasons,
        )

    @staticmethod
    def is_bear_watch_regime(regime: MarketRegime) -> bool:
        return regime.regime in {"bear", "bear-trend"} and regime.regime_severity in {"mild", "moderate"}

    @property
    def alert_cooldown_seconds(self) -> int:
        return int(self.settings.alert_cooldown_minutes * 60)

    @staticmethod
    def alert_cooldown_key(ticker: str, grade: str) -> str:
        return f"{ticker}:grade:{grade}"

    def log_sent_alert(
        self,
        ticker: str,
        grade: str,
        alert_type: str,
        score: float,
        setup_type: str,
        regime: str,
        message: str,
    ) -> None:
        payload = {
            "ticker": ticker,
            "grade": grade,
            "alert_type": alert_type,
            "score": score,
            "setup_type": setup_type,
            "regime": regime,
            "message": message,
        }
        try:
            self.db.insert_dict("sent_alerts", payload)
        except Exception as exc:
            self.logger.warning("sent_alerts insert failed; applying schema and retrying: %s", exc)
            self.db.initialize()
            self.db.insert_dict("sent_alerts", payload)

    def load_market_context(self) -> dict:
        context: dict = {}
        for ticker in ("SPY", "QQQ"):
            bars = self.retry.run(
                f"benchmark bars {ticker}",
                lambda ticker=ticker: self.market_data.get_recent_bars(ticker, lookback_days=10, timeframe="5Min"),
                fallback=None,
            )
            if bars is not None and not bars.empty:
                context[ticker] = bars
        vix_bars = self.retry.run(
            "VIX bars",
            lambda: self.market_data.get_recent_bars("^VIX", lookback_days=10, timeframe="5Min"),
            fallback=None,
        )
        if vix_bars is not None and not vix_bars.empty:
            context["VIX"] = float(vix_bars.iloc[-1]["close"])
        context["breadth_score"] = self.market_breadth_proxy(context.get("SPY"), context.get("QQQ"))
        self.logger.info(
            "Market context: SPY=%s QQQ=%s VIX=%s breadth=%.0f",
            "ok" if "SPY" in context else "missing",
            "ok" if "QQQ" in context else "missing",
            f"{context['VIX']:.1f}" if "VIX" in context else "missing",
            context["breadth_score"],
        )
        return context

    @staticmethod
    def market_breadth_proxy(spy_bars, qqq_bars) -> float:
        scores = []
        for bars in (spy_bars, qqq_bars):
            if bars is None or bars.empty:
                continue
            latest = bars.iloc[-1]
            close = float(latest["close"])
            ema_21 = float(latest.get("ema_21", close))
            ema_50 = float(latest.get("ema_50", close))
            score = 50
            if close > ema_21:
                score += 20
            if close > ema_50:
                score += 20
            if float(latest.get("return_20", 0) or 0) > 0:
                score += 10
            scores.append(score)
        return sum(scores) / len(scores) if scores else 50.0

    def get_options_confirmation(self, ticker: str) -> Optional[dict]:
        events = self.retry.run(
            f"options flow {ticker}",
            lambda: self.unusual_whales.fetch_recent_flow(ticker),
            fallback=[],
        )
        detected = self.options_detector.detect(events)
        for event in detected:
            self.db.insert_dict("options_flow", event)
        if not detected:
            self.logger.info("No unusual options confirmation for %s.", ticker)
            return None
        strongest = max(detected, key=lambda event: event["premium"])
        self.logger.info("Options confirmation for %s: %s", ticker, strongest["reason"])
        return strongest

    def evaluate_risk(
        self,
        setup: StrategySetup,
        snapshot: MarketSnapshot,
        regime: MarketRegime,
        options_confirmation: Optional[dict],
    ):
        liquidity_score = self.estimate_liquidity_score(snapshot, options_confirmation)
        return self.risk_engine.evaluate(
            setup=setup,
            account_equity=self.settings.paper_account_equity,
            bid=snapshot.bid,
            ask=snapshot.ask,
            open_trades=self.open_trade_count(),
            daily_loss_pct=self.daily_loss_pct(),
            liquidity_score=liquidity_score,
            market_is_safe=regime.is_safe,
            ticker=setup.ticker,
            active_positions={**self.state.active_positions, **self.state.active_signals},
        )

    def log_regime(self, ticker: str, regime: MarketRegime) -> None:
        self.db.insert_dict(
            "market_regime",
            {
                "ticker": ticker,
                "regime": regime.regime,
                "volatility": regime.volatility,
                "volume_state": regime.volume_state,
                "trend_strength": regime.trend_strength,
                "is_safe": int(regime.is_safe),
                "reason": regime.reason,
            },
        )
        self.logger.info("Regime for %s: %s (%s)", ticker, regime.regime, regime.reason)

    def heartbeat(self) -> None:
        timestamp = datetime.now(timezone.utc).isoformat()
        self.db.set_state("heartbeat_utc", timestamp)
        self.state_store.save(self.state)
        self.logger.info("Heartbeat UTC: %s", timestamp)

    def record_health(self, component: str, status: str, message: str) -> None:
        event = self.health.record(component, status, message)
        self.db.insert_dict(
            "health_events",
            {
                "component": event.component,
                "status": event.status,
                "message": event.message,
            },
        )

    def enter_safe_mode(self, reason: str) -> None:
        CircuitBreaker.trip(self.state, reason)
        self.state_store.save(self.state)
        self.record_health("watchdog", "safe_mode", reason)
        self.system_alert(f"Predator Trading AI SAFE MODE: {reason}")

    def system_alert(self, message: str) -> None:
        self.logger.warning(message)
        try:
            asyncio.run(self.telegram_bot.send_message(message))
        except Exception as exc:
            self.logger.exception("System alert failed: %s", exc)

    @staticmethod
    def runtime_revision() -> str:
        for key in ("RAILWAY_GIT_COMMIT_SHA", "RAILWAY_GIT_COMMIT", "SOURCE_COMMIT", "GIT_COMMIT_SHA"):
            value = os.getenv(key)
            if value:
                return f"{key}={value}"
        try:
            completed = subprocess.run(
                ["git", "rev-parse", "--short=12", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
                timeout=2,
            )
            return f"git={completed.stdout.strip()}"
        except Exception:
            return "unknown"

    @staticmethod
    def clock_is_sane(now: datetime) -> bool:
        if now.tzinfo is None:
            return False
        utc_now = datetime.now(timezone.utc)
        return abs((now.astimezone(timezone.utc) - utc_now).total_seconds()) < 300

    def expected_win_rate(self, ticker: str, setup_type: str) -> Optional[float]:
        rows = self.db.fetch_all(
            """
            SELECT win_rate
            FROM backtest_results
            WHERE ticker = ? AND strategy_name = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            [ticker, setup_type],
        )
        return float(rows[0]["win_rate"]) if rows else None

    def open_trade_count(self) -> int:
        rows = self.db.fetch_all("SELECT COUNT(*) AS count FROM trades WHERE status='open'")
        return int(rows[0]["count"]) if rows else 0

    def daily_loss_pct(self) -> float:
        rows = self.db.fetch_all(
            """
            SELECT COALESCE(SUM(pnl), 0) AS pnl
            FROM trades
            WHERE date(created_at) = date('now') AND status = 'closed'
            """
        )
        pnl = float(rows[0]["pnl"]) if rows else 0.0
        if pnl >= 0:
            return 0.0
        return abs(pnl) / self.settings.paper_account_equity * 100

    @staticmethod
    def estimate_liquidity_score(snapshot: MarketSnapshot, options_confirmation: Optional[dict]) -> float:
        if options_confirmation:
            return float(options_confirmation.get("liquidity_score", 0))
        spread = spread_pct(snapshot.bid, snapshot.ask)
        if spread == float("inf"):
            return 0.0
        return round(clamp(100 - (spread * 25), 0, 100), 2)

    def is_extreme_illiquidity(self, snapshot: MarketSnapshot) -> bool:
        spread = spread_pct(snapshot.bid, snapshot.ask)
        hard_spread_limit = max(self.settings.max_spread_pct * 3, 6.0)
        return spread == float("inf") or spread > hard_spread_limit

    @staticmethod
    def is_market_open(now: Optional[datetime] = None) -> bool:
        current = now or datetime.now(EASTERN)
        if current.tzinfo is None:
            current = current.replace(tzinfo=EASTERN)
        if current.weekday() >= 5:
            return False
        return MARKET_OPEN <= current.time() <= MARKET_CLOSE

    @staticmethod
    def seconds_until_next_open(now: Optional[datetime] = None) -> float:
        current = now or datetime.now(EASTERN)
        if current.tzinfo is None:
            current = current.replace(tzinfo=EASTERN)
        next_open = datetime.combine(current.date(), MARKET_OPEN, EASTERN)
        if current.time() >= MARKET_CLOSE or current.weekday() >= 5:
            next_open = next_open + timedelta(days=1)
        while next_open.weekday() >= 5:
            next_open = next_open + timedelta(days=1)
        if current < next_open:
            return max((next_open - current).total_seconds(), 60)
        return 60


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Predator Trading AI market intelligence loop.")
    parser.add_argument("--once", action="store_true", help="Run one market-hours iteration and exit.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    PredatorTradingAI().run(run_once=args.once)


if __name__ == "__main__":
    main()
