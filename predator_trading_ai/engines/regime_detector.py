from dataclasses import dataclass
from typing import Optional

import pandas as pd


@dataclass(frozen=True)
class MarketRegime:
    regime: str
    volatility: float
    volume_state: str
    trend_strength: float
    is_safe: bool
    reason: str
    risk_level: str = "normal"
    spy_trend: str = "unknown"
    qqq_trend: str = "unknown"
    breadth_score: float = 50.0
    vix_level: Optional[float] = None
    regime_severity: str = "normal"


class RegimeDetector:
    def detect(
        self,
        bars: pd.DataFrame,
        news_driven: bool = False,
        spy_bars: Optional[pd.DataFrame] = None,
        qqq_bars: Optional[pd.DataFrame] = None,
        vix_level: Optional[float] = None,
        breadth_score: Optional[float] = None,
    ) -> MarketRegime:
        if bars.empty or len(bars) < 20:
            return MarketRegime("no-trade", 0.0, "unknown", 0.0, False, "Insufficient market data", "blocked")

        latest = bars.iloc[-1]
        close = float(latest["close"])
        atr_pct = float(latest.get("atr_pct", latest.get("atr_14", 0) / close * 100))
        volume_ratio = float(latest["volume"] / max(latest.get("volume_sma_20", latest["volume"]), 1))
        ema_9 = float(latest.get("ema_9", close))
        ema_21 = float(latest.get("ema_21", close))
        ema_50 = float(latest.get("ema_50", close))
        ema_200 = float(latest.get("ema_200", close))
        trend_strength = abs(ema_9 - ema_21) / close * 100
        volume_state = "low" if volume_ratio < 0.75 else "high" if volume_ratio > 1.5 else "normal"

        spy_trend = self._trend_state(spy_bars) if spy_bars is not None else "unknown"
        qqq_trend = self._trend_state(qqq_bars) if qqq_bars is not None else "unknown"
        breadth = float(breadth_score) if breadth_score is not None else self._internal_breadth_proxy(bars)

        trend_up = ema_9 > ema_21 and close > ema_50 and ema_50 >= ema_200
        trend_down = ema_9 < ema_21 and close < ema_50
        is_bear = close < ema_200 and ema_50 < ema_200
        recent_return = (close - float(bars["close"].iloc[-6])) / float(bars["close"].iloc[-6]) * 100 if len(bars) >= 6 else 0
        panic = (vix_level is not None and vix_level >= 30) or (atr_pct > 3.5 and recent_return < -4.0)

        if news_driven:
            return MarketRegime("news-driven", atr_pct, volume_state, trend_strength, False, "News-driven regime", "blocked", spy_trend, qqq_trend, breadth, vix_level, "severe")
        if panic:
            return MarketRegime("panic", atr_pct, volume_state, trend_strength, False, "Panic mode: VIX/ATR shock or sharp selloff", "blocked", spy_trend, qqq_trend, breadth, vix_level, "panic")
        if vix_level is not None and vix_level >= 25:
            return MarketRegime("high-volatility", atr_pct, volume_state, trend_strength, False, f"VIX too high: {vix_level:.1f}", "blocked", spy_trend, qqq_trend, breadth, vix_level, "severe")
        if is_bear or spy_trend == "bear" or qqq_trend == "bear":
            severity = self._bear_severity(close, ema_50, ema_200, breadth, atr_pct, recent_return, vix_level)
            return MarketRegime("bear", atr_pct, volume_state, trend_strength, False, f"{severity.title()} bear regime: trade entries blocked", "blocked", spy_trend, qqq_trend, breadth, vix_level, severity)
        if breadth < 45:
            return MarketRegime("weak-breadth", atr_pct, volume_state, trend_strength, False, f"Weak market breadth: {breadth:.0f}", "blocked", spy_trend, qqq_trend, breadth, vix_level, "moderate")
        if volume_state == "low":
            return MarketRegime("low-volume", atr_pct, volume_state, trend_strength, False, "Low relative volume", "elevated", spy_trend, qqq_trend, breadth, vix_level, "mild")
        if atr_pct > 5:
            return MarketRegime("high-volatility", atr_pct, volume_state, trend_strength, False, "ATR above safe threshold", "blocked", spy_trend, qqq_trend, breadth, vix_level, "severe")
        if trend_strength < 0.20:
            return MarketRegime("choppy", atr_pct, volume_state, trend_strength, False, "Weak trend strength", "elevated", spy_trend, qqq_trend, breadth, vix_level, "mild")
        if trend_up and breadth >= 55 and (spy_trend in {"bull", "unknown"}) and (qqq_trend in {"bull", "unknown"}):
            return MarketRegime("bull-trend", atr_pct, volume_state, trend_strength, True, "Bull trend with acceptable breadth", "normal", spy_trend, qqq_trend, breadth, vix_level)
        if trend_down:
            severity = self._bear_severity(close, ema_50, ema_200, breadth, atr_pct, recent_return, vix_level)
            return MarketRegime("bear-trend", atr_pct, volume_state, trend_strength, False, f"{severity.title()} bear trend: trade entries blocked", "blocked", spy_trend, qqq_trend, breadth, vix_level, severity)
        return MarketRegime("normal", atr_pct, volume_state, trend_strength, True, "Normal tradable regime", "normal", spy_trend, qqq_trend, breadth, vix_level)

    @staticmethod
    def _bear_severity(
        close: float,
        ema_50: float,
        ema_200: float,
        breadth: float,
        atr_pct: float,
        recent_return: float,
        vix_level: Optional[float],
    ) -> str:
        if vix_level is not None and vix_level >= 30:
            return "panic"
        if atr_pct >= 5 or recent_return <= -4 or breadth < 30:
            return "severe"
        if close < ema_200 and ema_50 < ema_200 and breadth < 45:
            return "severe"
        if close < ema_200 or ema_50 < ema_200 or breadth < 50:
            return "moderate"
        return "mild"

    @staticmethod
    def _trend_state(bars: pd.DataFrame) -> str:
        if bars.empty or len(bars) < 50:
            return "unknown"
        latest = bars.iloc[-1]
        close = float(latest["close"])
        ema_50 = float(latest.get("ema_50", close))
        ema_200 = float(latest.get("ema_200", ema_50))
        if close > ema_50 >= ema_200:
            return "bull"
        if close < ema_50 <= ema_200:
            return "bear"
        return "mixed"

    @staticmethod
    def _internal_breadth_proxy(bars: pd.DataFrame) -> float:
        if len(bars) < 20:
            return 50.0
        recent = bars.tail(20)
        up_days = (recent["close"].diff() > 0).sum()
        above_ema_21 = float(bars.iloc[-1]["close"]) > float(bars.iloc[-1].get("ema_21", bars.iloc[-1]["close"]))
        breadth = (up_days / 19) * 70
        if above_ema_21:
            breadth += 30
        return max(0.0, min(100.0, breadth))
