from dataclasses import dataclass
from typing import Optional

import pandas as pd

from predator_trading_ai.config import Settings, get_settings
from predator_trading_ai.engines.regime_detector import MarketRegime
from predator_trading_ai.utils.validators import clamp


@dataclass(frozen=True)
class StrategySetup:
    ticker: str
    direction: str
    setup_type: str
    score: float
    entry_zone_low: float
    entry_zone_high: float
    stop_loss: float
    targets: tuple[float, float, float]
    reason: str
    do_not_enter_conditions: list[str]
    signal_tier: str = "A Signal"


@dataclass(frozen=True)
class SetupQuality:
    approved: bool
    score_bonus: float
    reasons: list[str]
    rejections: list[str]


@dataclass(frozen=True)
class WatchEvaluation:
    setup: Optional[StrategySetup]
    score: float
    grade_candidate: str
    rejected_by: str
    reason: str


class StrategyEngine:
    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings = settings or get_settings()

    def evaluate(
        self,
        ticker: str,
        bars: pd.DataFrame,
        regime: MarketRegime,
        options_confirmation: Optional[dict] = None,
        sentiment_confirmation: Optional[dict] = None,
    ) -> Optional[StrategySetup]:
        if bars.empty or len(bars) < 50:
            return None
        if self.is_trade_blocked_regime(regime) or not regime.is_safe:
            return None

        latest = bars.iloc[-1]
        previous_high = float(bars["high"].iloc[-21:-1].max())
        previous_low = float(bars["low"].iloc[-21:-1].min())
        close = float(latest["close"])
        atr = float(latest.get("atr_14", close * 0.02))
        rsi = float(latest.get("rsi_14", 50))
        ema_9 = float(latest.get("ema_9", close))
        ema_21 = float(latest.get("ema_21", close))
        ema_50 = float(latest.get("ema_50", close))
        ema_200 = float(latest.get("ema_200", close))
        macd = float(latest.get("macd", 0))
        macd_signal = float(latest.get("macd_signal", 0))
        volume_ratio = float(latest.get("relative_volume", latest["volume"] / max(latest.get("volume_sma_20", latest["volume"]), 1)))
        atr_pct = float(latest.get("atr_pct", atr / close * 100))
        return_20 = float(latest.get("return_20", 0) or 0)

        quality = self._quality_gate(close, atr, atr_pct, volume_ratio, ema_21, ema_50, ema_200, return_20, regime)
        if not quality.approved:
            return None

        candidates = [
            self._breakout(ticker, close, previous_high, atr, volume_ratio, rsi, ema_9, ema_21),
            self._reversal(ticker, close, previous_low, atr, volume_ratio, rsi, ema_50, ema_200),
            self._momentum(ticker, close, atr, volume_ratio, ema_9, ema_21, ema_50, ema_200, macd, macd_signal, rsi),
        ]
        valid = [candidate for candidate in candidates if candidate is not None]
        if not valid:
            return None

        best = max(valid, key=lambda setup: setup.score)
        bonus = quality.score_bonus
        reason_parts = [best.reason, *quality.reasons]
        if options_confirmation:
            bonus += 8
            reason_parts.append("options flow confirmation")
        if sentiment_confirmation and sentiment_confirmation.get("sentiment_score", 0) > 0.2:
            bonus += 2
            reason_parts.append("positive sentiment confirmation")

        final_score = clamp(best.score + bonus, 0, 100)
        if final_score < self._score_threshold(regime):
            return None
        tier = self.grade_for_score(final_score)

        return StrategySetup(
            ticker=best.ticker,
            direction=best.direction,
            setup_type=best.setup_type,
            score=round(final_score, 2),
            entry_zone_low=best.entry_zone_low,
            entry_zone_high=best.entry_zone_high,
            stop_loss=best.stop_loss,
            targets=best.targets,
            reason=", ".join(reason_parts),
            do_not_enter_conditions=[
                "price fails to hold entry zone",
                "relative volume fades below 1.0",
                "price extends more than 3.0 ATR from EMA21",
                "SPY/QQQ regime loses bull alignment",
                "spread widens beyond configured limit",
            ],
            signal_tier=tier,
        )

    def _score_threshold(self, regime: MarketRegime) -> float:
        if regime.regime == "bull-trend":
            return self.settings.min_score_a
        if regime.regime == "normal":
            return max(self.settings.min_score_a, 60)
        return self.settings.min_score_a_plus

    def evaluate_watch_alert(self, ticker: str, bars: pd.DataFrame, regime: MarketRegime) -> Optional[StrategySetup]:
        return self.evaluate_watch_candidate(ticker, bars, regime).setup

    def evaluate_watch_candidate(self, ticker: str, bars: pd.DataFrame, regime: MarketRegime) -> WatchEvaluation:
        if not self.settings.enable_watchlist_alerts:
            return WatchEvaluation(None, 0.0, "disabled", "config", "watchlist alerts disabled")
        if bars.empty or len(bars) < 30:
            return WatchEvaluation(None, 0.0, "none", "data", "insufficient bars for watch alert")
        if self.is_watch_hard_blocked_regime(regime):
            return WatchEvaluation(None, 0.0, "blocked", "regime", f"hard-blocked regime: {regime.regime}")

        latest = bars.iloc[-1]
        close = float(latest["close"])
        atr = float(latest.get("atr_14", close * 0.02))
        previous_high = float(bars["high"].iloc[-21:-1].max())
        ema_9 = float(latest.get("ema_9", close))
        ema_21 = float(latest.get("ema_21", close))
        ema_50 = float(latest.get("ema_50", close))
        ema_200 = float(latest.get("ema_200", close))
        volume_ratio = float(latest.get("relative_volume", 0) or 0)
        return_20 = float(latest.get("return_20", 0) or 0)
        rsi = float(latest.get("rsi_14", 50) or 50)
        distance_to_breakout = (previous_high - close) / max(atr, close * 0.005)
        distance_from_ema21 = abs(close - ema_21) / max(atr, close * 0.005)

        score = 0.0
        reasons = []
        soft_rejections = []
        if close > ema_50:
            score += 10
            reasons.append("price above EMA50")
        else:
            soft_rejections.append("price below EMA50")
        if ema_50 >= ema_200:
            score += 8
            reasons.append("EMA50 above EMA200")
        else:
            soft_rejections.append("EMA50 below EMA200")
        if close > ema_50 >= ema_200:
            score += 6
            reasons.append("bull trend building")
        if ema_9 > ema_21:
            score += 10
            reasons.append("short-term momentum improving")
        else:
            soft_rejections.append("short-term momentum not confirmed")
        if -0.75 <= distance_to_breakout <= 2.5:
            score += 14
            reasons.append(f"near 20-bar breakout {previous_high:.2f}")
        elif distance_to_breakout < -0.75:
            score += 6
            reasons.append("already probing breakout area")
        else:
            soft_rejections.append(f"not near breakout: {distance_to_breakout:.2f} ATR away")
        if volume_ratio >= 0.8:
            score += 10
            reasons.append(f"volume rising {volume_ratio:.2f}x")
        elif volume_ratio >= 0.55:
            score += 5
            reasons.append(f"low but usable volume {volume_ratio:.2f}x")
            soft_rejections.append(f"volume not confirmed: {volume_ratio:.2f}x")
        else:
            soft_rejections.append(f"volume too quiet: {volume_ratio:.2f}x")
        if 42 <= rsi <= 72:
            score += 6
            reasons.append(f"RSI in tradable zone {rsi:.1f}")
        else:
            soft_rejections.append(f"RSI outside preferred zone: {rsi:.1f}")
        if distance_from_ema21 <= 2.75:
            score += 6
            reasons.append("not too extended from EMA21")
        else:
            soft_rejections.append(f"extended from EMA21: {distance_from_ema21:.2f} ATR")
        if return_20 > 0:
            score += min(return_20, 12) * 0.5
            reasons.append(f"positive 20-bar strength {return_20:.1f}%")
        else:
            soft_rejections.append(f"negative 20-bar strength {return_20:.1f}%")

        if regime.regime == "bull-trend":
            score += 5
            reasons.append("bull regime support")
        elif regime.regime in {"bear", "bear-trend"} and regime.regime_severity in {"mild", "moderate"}:
            score -= 8
            soft_rejections.append(f"{regime.regime_severity} bear regime active")
        elif regime.regime in {"choppy", "low-volume", "weak-breadth"}:
            score -= 6
            soft_rejections.append(f"soft regime warning: {regime.regime}")

        score = round(clamp(score, 0, self.settings.min_score_a - 0.01), 2)
        tier = self.grade_for_score(score)
        if score < self.settings.min_score_c or score >= self.settings.min_score_a:
            reason = "; ".join(soft_rejections or reasons or ["score below watch threshold"])
            return WatchEvaluation(None, score, tier, "score", reason)
        if tier == "B Watch Alert" and not self.settings.enable_b_alerts:
            return WatchEvaluation(None, score, tier, "config", "B alerts disabled")
        if tier == "C Risky/Early Alert" and not self.settings.enable_c_alerts:
            return WatchEvaluation(None, score, tier, "config", "C alerts disabled")
        reason = "; ".join(reasons) if reasons else "early setup forming"
        if soft_rejections:
            reason = f"{reason}; watch risks: {'; '.join(soft_rejections[:3])}"
        setup = self._long_setup(
            ticker,
            "graded watch setup",
            close,
            atr,
            score,
            reason,
            signal_tier=tier,
        )
        return WatchEvaluation(setup, score, tier, "none", reason)

    @staticmethod
    def is_trade_blocked_regime(regime: MarketRegime) -> bool:
        return regime.regime in {"no-trade", "bear", "bear-trend", "panic", "high-volatility", "news-driven"}

    @staticmethod
    def is_watch_hard_blocked_regime(regime: MarketRegime) -> bool:
        if regime.regime in {"no-trade", "panic", "high-volatility", "news-driven"}:
            return True
        if regime.regime in {"bear", "bear-trend"}:
            return regime.regime_severity in {"severe", "panic"}
        return False

    def grade_for_score(self, score: float) -> str:
        if score >= self.settings.min_score_a_plus_plus:
            return "A++ Signal"
        if score >= self.settings.min_score_a_plus:
            return "A+ Signal"
        if score >= self.settings.min_score_a:
            return "A Signal"
        if score >= self.settings.min_score_b:
            return "B Watch Alert"
        return "C Risky/Early Alert"

    @staticmethod
    def _quality_gate(
        close: float,
        atr: float,
        atr_pct: float,
        volume_ratio: float,
        ema_21: float,
        ema_50: float,
        ema_200: float,
        return_20: float,
        regime: MarketRegime,
    ) -> SetupQuality:
        rejections: list[str] = []
        reasons: list[str] = []
        bonus = 0.0

        if not (close > ema_50 >= ema_200):
            rejections.append("price/EMA50/EMA200 not bull aligned")
        else:
            bonus += 8
            reasons.append("EMA50/EMA200 bull alignment")

        if volume_ratio < 1.0:
            rejections.append(f"relative volume too low: {volume_ratio:.2f}")
        elif volume_ratio >= 1.5:
            bonus += 6
            reasons.append(f"strong relative volume {volume_ratio:.2f}")
        elif volume_ratio >= 1.15:
            bonus += 3
            reasons.append(f"acceptable relative volume {volume_ratio:.2f}")
        else:
            reasons.append(f"volume building {volume_ratio:.2f}")

        distance_atr = abs(close - ema_21) / max(atr, close * 0.005)
        if distance_atr > 3.0:
            rejections.append(f"entry extended from EMA21: {distance_atr:.2f} ATR")
        elif distance_atr <= 1.2:
            bonus += 5
            reasons.append("entry not extended")

        if atr_pct > 6.0:
            rejections.append(f"volatility too high: ATR {atr_pct:.2f}%")
        elif atr_pct <= 4.0:
            bonus += 4
            reasons.append(f"controlled volatility ATR {atr_pct:.2f}%")

        if return_20 > 0:
            bonus += min(return_20, 12) * 0.5
            reasons.append(f"positive 20-bar relative strength {return_20:.1f}%")

        if regime.breadth_score >= 60:
            bonus += 4
            reasons.append(f"breadth confirmation {regime.breadth_score:.0f}")

        return SetupQuality(not rejections, bonus, reasons, rejections)

    def _breakout(self, ticker: str, close: float, previous_high: float, atr: float, volume_ratio: float, rsi: float, ema_9: float, ema_21: float) -> Optional[StrategySetup]:
        breakout_distance = (close - previous_high) / max(atr, 0.01)
        if close <= previous_high or volume_ratio < 1.1 or ema_9 <= ema_21 or not (45 <= rsi <= 75):
            return None
        if breakout_distance > 1.75:
            return None
        score = 58 + min(breakout_distance * 12, 14) + min((volume_ratio - 1) * 12, 16)
        return self._long_setup(ticker, "high-quality breakout", close, atr, score, f"controlled breakout above 20-bar high {previous_high:.2f}, RSI {rsi:.1f}")

    def _reversal(self, ticker: str, close: float, previous_low: float, atr: float, volume_ratio: float, rsi: float, ema_50: float, ema_200: float) -> Optional[StrategySetup]:
        if close > previous_low + (atr * 0.85) or rsi > 45 or volume_ratio < 1.05 or close < ema_200 or ema_50 < ema_200:
            return None
        score = 54 + min((45 - rsi), 16) + min((volume_ratio - 1) * 10, 12)
        return self._long_setup(ticker, "bull-market reversal", close, atr, score, f"oversold reversal within bull structure near {previous_low:.2f}, RSI {rsi:.1f}")

    def _momentum(self, ticker: str, close: float, atr: float, volume_ratio: float, ema_9: float, ema_21: float, ema_50: float, ema_200: float, macd: float, macd_signal: float, rsi: float) -> Optional[StrategySetup]:
        if not (ema_9 > ema_21 > ema_50 >= ema_200 and macd > macd_signal and 48 <= rsi <= 72 and volume_ratio >= 1.0):
            return None
        score = 56 + min((ema_9 - ema_21) / max(close, 0.01) * 1200, 18) + min((volume_ratio - 1) * 12, 14)
        return self._long_setup(ticker, "institutional momentum continuation", close, atr, score, f"stacked EMA/MACD momentum, RSI {rsi:.1f}")

    @staticmethod
    def _long_setup(ticker: str, setup_type: str, close: float, atr: float, score: float, reason: str, signal_tier: str = "A Signal") -> StrategySetup:
        risk = max(atr, close * 0.01)
        return StrategySetup(
            ticker=ticker,
            direction="long",
            setup_type=setup_type,
            score=round(clamp(score, 0, 100), 2),
            entry_zone_low=round(close - risk * 0.12, 2),
            entry_zone_high=round(close + risk * 0.12, 2),
            stop_loss=round(close - risk, 2),
            targets=(round(close + risk * 1.6, 2), round(close + risk * 2.2, 2), round(close + risk * 3.2, 2)),
            reason=reason,
            do_not_enter_conditions=[],
            signal_tier=signal_tier,
        )
