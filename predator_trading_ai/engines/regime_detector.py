from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class MarketRegime:
    regime: str
    volatility: float
    volume_state: str
    trend_strength: float
    is_safe: bool
    reason: str


class RegimeDetector:
    def detect(self, bars: pd.DataFrame, news_driven: bool = False) -> MarketRegime:
        if bars.empty or len(bars) < 20:
            return MarketRegime("no-trade", 0.0, "unknown", 0.0, False, "Insufficient market data")

        latest = bars.iloc[-1]
        close = float(latest["close"])
        atr_pct = float(latest.get("atr_14", 0) / close * 100)
        volume_ratio = float(latest["volume"] / max(latest.get("volume_sma_20", latest["volume"]), 1))
        ema_9 = float(latest.get("ema_9", close))
        ema_21 = float(latest.get("ema_21", close))
        ema_spread = abs(ema_9 - ema_21)
        trend_strength = ema_spread / close * 100

        # EMA 50 و 200 للتصنيف البعيد
        ema_50 = float(latest.get("ema_50", close))
        ema_200 = float(latest.get("ema_200", close))


        # اتجاه الترند
        trend_up = ema_9 > ema_21 and close > ema_50
        trend_down = ema_9 < ema_21 and close < ema_50

        # Bear Market
        is_bear = close < ema_200 and ema_50 < ema_200

        # Panic (هبوط سريع + تذبذب عالي)
        recent_return = (close - float(bars["close"].iloc[-6])) / float(bars["close"].iloc[-6]) * 100 if len(bars) >= 6 else 0
        is_panic = atr_pct > 3.0 and recent_return < -4.0

        volume_state = "low" if volume_ratio < 0.75 else "high" if volume_ratio > 1.5 else "normal"

        # التصنيف بالترتيب
        if news_driven:
            return MarketRegime("news-driven", atr_pct, volume_state, trend_strength, False, "News-driven regime")
        if is_panic:
            return MarketRegime("panic", atr_pct, volume_state, trend_strength, False, "Panic: high volatility + sharp drop")
        if is_bear:
            return MarketRegime("bear", atr_pct, volume_state, trend_strength, False, "Bear market: price below EMA200 and EMA50 < EMA200")
        if volume_state == "low":
            return MarketRegime("low-volume", atr_pct, volume_state, trend_strength, False, "Low relative volume")
        if atr_pct > 5:
            return MarketRegime("high-volatility", atr_pct, volume_state, trend_strength, False, "ATR above safe threshold")
        if trend_strength < 0.25:
            return MarketRegime("choppy", atr_pct, volume_state, trend_strength, False, "Weak trend strength")
        if trend_up and trend_strength > 0.75 and volume_ratio >= 1.0:
            return MarketRegime("bull-trend", atr_pct, volume_state, trend_strength, True, "Strong bull trend confirmed")
        if trend_down:
            return MarketRegime("bear-trend", atr_pct, volume_state, trend_strength, False, "Downtrend: EMAs bearish")
        return MarketRegime("normal", atr_pct, volume_state, trend_strength, True, "Normal tradable regime")
