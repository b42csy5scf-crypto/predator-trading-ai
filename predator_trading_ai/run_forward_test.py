import argparse
import asyncio
import time
from datetime import datetime, time as dt_time, timezone
from zoneinfo import ZoneInfo

from predator_trading_ai.alerts.telegram_bot import TelegramAlertBot
from predator_trading_ai.config import Settings, get_settings
from predator_trading_ai.data.market_data import MarketDataClient
from predator_trading_ai.database.db import Database
from predator_trading_ai.engines.regime_detector import RegimeDetector
from predator_trading_ai.engines.risk_engine import RiskEngine
from predator_trading_ai.engines.shadow_mode import ShadowModeLogger
from predator_trading_ai.engines.signal_engine import SignalEngine
from predator_trading_ai.engines.strategy_engine import StrategyEngine
from predator_trading_ai.reports.forward_report import ForwardTestReport
from predator_trading_ai.state.runtime_state import RuntimeStateStore
from predator_trading_ai.utils.logger import setup_logger
from predator_trading_ai.utils.reliability import CircuitBreaker, RetryPolicy
from predator_trading_ai.utils.validators import clamp, spread_pct
from predator_trading_ai.utils.watchlist import parse_watchlist


EASTERN = ZoneInfo("America/New_York")
MARKET_OPEN = dt_time(9, 30)
MARKET_CLOSE = dt_time(16, 0)


class ForwardTester:
    def __init__(self, settings: Settings | None = None, send_telegram: bool = True) -> None:
        self.settings = settings or get_settings()
        self.logger = setup_logger("predator_trading_ai.forward", self.settings.log_level)
        self.db = Database(self.settings)
        self.market_data = MarketDataClient(self.settings)
        self.regime_detector = RegimeDetector()
        self.strategy_engine = StrategyEngine(self.settings)
        self.risk_engine = RiskEngine(self.settings)
        self.signal_engine = SignalEngine(self.db)
        self.shadow = ShadowModeLogger(self.db)
        self.telegram = TelegramAlertBot(self.settings, self.db)
        self.report = ForwardTestReport(self.db)
        self.retry = RetryPolicy(self.settings.retry_attempts, self.settings.retry_base_delay_seconds)
        self.circuit = CircuitBreaker(self.settings.watchdog_max_failures)
        self.state_store = RuntimeStateStore()
        self.state = self.state_store.load()
        self.watchlist = parse_watchlist(self.settings.watchlist)
        self.send_telegram = send_telegram

    def run(self, once: bool = False, ignore_market_hours: bool = False, summary_only: bool = False) -> None:
        self.db.initialize()
        if self.settings.live_trading:
            self.logger.warning("LIVE_TRADING is true in env, but forward test never executes orders.")
        if summary_only:
            self.send_summary()
            return

        try:
            while True:
                self.heartbeat()
                if self.state.safe_mode:
                    self.logger.warning("SAFE MODE active; forward test scan skipped: %s", self.state.safe_mode_reason)
                    if once:
                        return
                    time.sleep(self.settings.loop_interval_seconds)
                    continue

                now = datetime.now(EASTERN)
                if not ignore_market_hours and not self.is_market_open(now):
                    self.logger.info("Market closed at %s ET; scan skipped.", now.strftime("%Y-%m-%d %H:%M:%S"))
                    if once:
                        return
                    time.sleep(self.settings.loop_interval_seconds)
                    continue

                self.scan_once()
                if once:
                    return
                if now.time() >= dt_time(15, 58):
                    self.send_summary()
                time.sleep(self.settings.loop_interval_seconds)
        except KeyboardInterrupt:
            self.logger.info("Forward test stopped cleanly by Ctrl+C.")

    def scan_once(self) -> None:
        self.logger.info("Forward shadow scan started for %d tickers.", len(self.watchlist))
        context = self.load_market_context()
        failures = 0
        for ticker in self.watchlist:
            try:
                self.scan_ticker(ticker, context)
            except Exception as exc:
                failures += 1
                self.logger.exception("Forward scan failed for %s: %s", ticker, exc)

        if failures:
            if self.circuit.record_failure(self.state, "forward test scanner failures exceeded watchdog limit"):
                self.logger.warning("SAFE MODE enabled by forward tester circuit breaker.")
            self.state_store.save(self.state)
        else:
            self.circuit.record_success(self.state)
            self.state_store.mark_scan(self.state)
        self.logger.info("Forward shadow scan complete. failures=%d", failures)

    def scan_ticker(self, ticker: str, context: dict) -> None:
        bars = self.retry.run(
            f"forward bars {ticker}",
            lambda: self.market_data.get_recent_bars(ticker, lookback_days=10, timeframe="5Min"),
            fallback=None,
        )
        if bars is None or bars.empty:
            fallback_bars = bars if bars is not None else self._empty_frame()
            regime = self.regime_detector.detect(fallback_bars)
            diagnostics = self.shadow.diagnostics(ticker, self._empty_frame(), self.state.active_signals)
            self.shadow.log(ticker, "rejected", regime, diagnostics, rejection_stage="data", rejection_reason="missing market data")
            return

        self.shadow.update_outcomes(ticker, bars)
        snapshot = self.market_data.get_latest_snapshot(ticker)
        price = float(bars.iloc[-1]["close"]) if snapshot is None else snapshot.price
        bid = snapshot.bid if snapshot else price * 0.9995
        ask = snapshot.ask if snapshot else price * 1.0005
        regime = self.regime_detector.detect(
            bars,
            spy_bars=context.get("SPY"),
            qqq_bars=context.get("QQQ"),
            vix_level=context.get("VIX"),
            breadth_score=context.get("breadth_score"),
        )
        diagnostics = self.shadow.diagnostics(ticker, bars, {**self.state.active_positions, **self.state.active_signals})

        setup = self.strategy_engine.evaluate(ticker, bars, regime)
        if setup is None:
            self.shadow.log(
                ticker,
                "rejected",
                regime,
                diagnostics,
                rejection_stage="strategy",
                rejection_reason=self.strategy_rejection_reason(regime, diagnostics),
            )
            return

        risk = self.risk_engine.evaluate(
            setup=setup,
            account_equity=self.settings.paper_account_equity,
            bid=bid,
            ask=ask,
            open_trades=0,
            daily_loss_pct=0,
            liquidity_score=self.estimate_liquidity_score(bid, ask),
            market_is_safe=regime.is_safe,
            ticker=ticker,
            active_positions={**self.state.active_positions, **self.state.active_signals},
        )
        diagnostics = self.shadow.diagnostics(ticker, bars, {**self.state.active_positions, **self.state.active_signals}, score=setup.score)
        if not risk.approved:
            self.shadow.log(
                ticker,
                "rejected",
                regime,
                diagnostics,
                setup=setup,
                risk=risk,
                rejection_stage="risk",
                rejection_reason="; ".join(risk.reasons),
            )
            return

        self.shadow.log(ticker, "accepted", regime, diagnostics, setup=setup, risk=risk)
        self.logger.info("Accepted forward signal: %s %s %.0f%%", ticker, setup.setup_type, setup.score)
        signal_key = self.state_store.signal_key(ticker, setup.setup_type, setup.direction)
        if self.state_store.is_on_cooldown(self.state, signal_key, self.settings.signal_cooldown_seconds):
            self.logger.info("Accepted signal is on cooldown; Telegram suppressed for %s.", signal_key)
            return
        signal = self.signal_engine.build_signal(setup, risk, regime, expected_win_rate=None)
        self.state.active_signals[signal_key] = {
            "ticker": ticker,
            "setup_type": setup.setup_type,
            "direction": setup.direction,
            "confidence": setup.score,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self.state_store.set_cooldown(self.state, signal_key)
        if signal and self.send_telegram:
            asyncio.run(self.telegram.send_signal(signal))

    def load_market_context(self) -> dict:
        context: dict = {}
        for ticker in ("SPY", "QQQ"):
            bars = self.retry.run(
                f"context bars {ticker}",
                lambda ticker=ticker: self.market_data.get_recent_bars(ticker, lookback_days=10, timeframe="5Min"),
                fallback=None,
            )
            if bars is not None and not bars.empty:
                context[ticker] = bars
        vix_bars = self.retry.run(
            "context VIX",
            lambda: self.market_data.get_recent_bars("^VIX", lookback_days=10, timeframe="5Min"),
            fallback=None,
        )
        if vix_bars is not None and not vix_bars.empty:
            context["VIX"] = float(vix_bars.iloc[-1]["close"])
        context["breadth_score"] = self.market_breadth_proxy(context.get("SPY"), context.get("QQQ"))
        return context

    def send_summary(self) -> None:
        summary = self.report.build_daily_summary()
        self.logger.info("\n%s", summary)
        if self.send_telegram:
            asyncio.run(self.telegram.send_message(summary))

    def heartbeat(self) -> None:
        timestamp = datetime.now(timezone.utc).isoformat()
        self.db.set_state("forward_test_heartbeat_utc", timestamp)
        self.state_store.save(self.state)
        self.logger.info("Forward heartbeat UTC: %s", timestamp)

    @staticmethod
    def strategy_rejection_reason(regime, diagnostics) -> str:
        if not regime.is_safe:
            return f"unsafe regime: {regime.regime} - {regime.reason}"
        failed = [
            diagnostics.volume_condition,
            diagnostics.trend_condition,
            diagnostics.volatility_condition,
            diagnostics.correlation_condition,
        ]
        failed = [item for item in failed if item.startswith("fail")]
        return "; ".join(failed) if failed else "setup score below institutional threshold or no clean setup"

    @staticmethod
    def estimate_liquidity_score(bid: float | None, ask: float | None) -> float:
        spread = spread_pct(bid, ask)
        if spread == float("inf"):
            return 0.0
        return round(clamp(100 - (spread * 25), 0, 100), 2)

    @staticmethod
    def market_breadth_proxy(spy_bars, qqq_bars) -> float:
        scores = []
        for bars in (spy_bars, qqq_bars):
            if bars is None or bars.empty:
                continue
            latest = bars.iloc[-1]
            close = float(latest["close"])
            score = 50
            if close > float(latest.get("ema_21", close)):
                score += 20
            if close > float(latest.get("ema_50", close)):
                score += 20
            if float(latest.get("return_20", 0) or 0) > 0:
                score += 10
            scores.append(score)
        return sum(scores) / len(scores) if scores else 50.0

    @staticmethod
    def is_market_open(now: datetime) -> bool:
        return now.weekday() < 5 and MARKET_OPEN <= now.time() <= MARKET_CLOSE

    @staticmethod
    def _empty_frame():
        import pandas as pd

        return pd.DataFrame()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Predator Trading AI forward shadow test.")
    parser.add_argument("--once", action="store_true", help="Run one scan cycle and exit.")
    parser.add_argument("--summary", action="store_true", help="Build and send today's forward-test summary only.")
    parser.add_argument("--ignore-market-hours", action="store_true", help="Run even when the US market is closed.")
    parser.add_argument("--no-telegram", action="store_true", help="Do not send Telegram messages.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    ForwardTester(send_telegram=not args.no_telegram).run(
        once=args.once,
        ignore_market_hours=args.ignore_market_hours,
        summary_only=args.summary,
    )


if __name__ == "__main__":
    main()
