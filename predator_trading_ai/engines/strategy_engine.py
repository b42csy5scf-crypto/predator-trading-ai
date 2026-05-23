from dataclasses import dataclass
from typing import Optional

import pandas as pd

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


class StrategyEngine:
    def evaluate(
        self,
        ticker: str,
        bars: pd.DataFrame,
        regime: MarketRegime,
        options_confirmation: Optional[dict] = None,
        sentiment_confirmation: Optional[dict] = None,
    ) -> Optional[StrategySetup]:
        if bars.empty or len(bars) < 30:
            return None
        
        if regime.regime in ("choppy", "bear", "bear-trend", "panic", "high-volatility", "low-volume"):
            return None

        latest = bars.iloc[-1]
        close = float(latest["close"])

        ema_50 = float(latest.get("ema_50", close))
        ema_200 = float(latest.get("ema_200", close))
        if close < ema_50 or ema_50 < ema_200:
            return None



        latest = bars.iloc[-1]
        previous_high = float(bars["high"].iloc[-21:-1].max())
        previous_low = float(bars["low"].iloc[-21:-1].min())
        close = float(latest["close"])
        atr = float(latest.get("atr_14", close * 0.02))
        rsi = float(latest.get("rsi_14", 50))
        ema_9 = float(latest.get("ema_9", close))
        ema_21 = float(latest.get("ema_21", close))
        macd = float(latest.get("macd", 0))
        macd_signal = float(latest.get("macd_signal", 0))
        volume_ratio = float(latest["volume"] / max(latest.get("volume_sma_20", latest["volume"]), 1))

        candidates = [
            self._breakout(ticker, close, previous_high, atr, volume_ratio, rsi, ema_9, ema_21),
            self._reversal(ticker, close, previous_low, atr, volume_ratio, rsi),
            self._momentum(ticker, close, atr, volume_ratio, ema_9, ema_21, macd, macd_signal, rsi),
            self._breakdown_short(ticker, close, previous_low, atr, volume_ratio, rsi, ema_9, ema_21),
            self._mean_reversion_short(ticker, close, previous_high, atr, volume_ratio, rsi, ema_9, ema_21),
        ]
        valid = [candidate for candidate in candidates if candidate is not None]
        if not valid:
            return None

        best = max(valid, key=lambda setup: setup.score)
        if best.score < 65:
                return None

        bonus = 0.0
        reason_parts = [best.reason]
        if options_confirmation:
            bonus += 8
            reason_parts.append("options flow confirmation")
        if sentiment_confirmation and sentiment_confirmation.get("sentiment_score", 0) > 0.2:
            bonus += 3
            reason_parts.append("positive sentiment confirmation")
        if not regime.is_safe:
            reason_parts.append(f"regime warning: {regime.reason}")

        return StrategySetup(
            ticker=best.ticker,
            direction=best.direction,
            setup_type=best.setup_type,
            score=clamp(best.score + bonus, 0, 100),
            entry_zone_low=best.entry_zone_low,
            entry_zone_high=best.entry_zone_high,
            stop_loss=best.stop_loss,
            targets=best.targets,
            reason=", ".join(reason_parts),
            do_not_enter_conditions=[
                "price fails to hold entry zone",
                "relative volume fades below 1.0",
                "spread widens beyond configured limit",
                "market regime turns unsafe",
            ],
        )

    def _breakout(self, ticker: str, close: float, previous_high: float, atr: float, volume_ratio: float, rsi: float, ema_9: float, ema_21: float) -> Optional[StrategySetup]:
        if close <= previous_high or volume_ratio < 1.25 or ema_9 <= ema_21:
            return None
        score = 55 + min((close - previous_high) / max(atr, 0.01) * 10, 15) + min((volume_ratio - 1) * 10, 15)
        return self._long_setup(ticker, "breakout", close, atr, score, f"break above 20-bar high {previous_high:.2f}, RSI {rsi:.1f}")

    def _reversal(self, ticker: str, close: float, previous_low: float, atr: float, volume_ratio: float, rsi: float) -> Optional[StrategySetup]:
        if close > previous_low + atr or rsi > 38 or volume_ratio < 1.1:
            return None
        score = 52 + min((40 - rsi), 18) + min((volume_ratio - 1) * 8, 10)
        return self._long_setup(ticker, "reversal", close, atr, score, f"oversold reversal near 20-bar low {previous_low:.2f}, RSI {rsi:.1f}")

    def _momentum(self, ticker: str, close: float, atr: float, volume_ratio: float, ema_9: float, ema_21: float, macd: float, macd_signal: float, rsi: float) -> Optional[StrategySetup]:
        if not (ema_9 > ema_21 and macd > macd_signal and 45 <= rsi <= 72 and volume_ratio >= 1.2):
            return None
        score = 50 + min((ema_9 - ema_21) / max(close, 0.01) * 1000, 20) + min((volume_ratio - 1) * 10, 10)
        return self._long_setup(ticker, "momentum continuation", close, atr, score, f"EMA and MACD momentum aligned, RSI {rsi:.1f}")

    def _breakdown_short(self, ticker: str, close: float, previous_low: float, atr: float, volume_ratio: float, rsi: float, ema_9: float, ema_21: float) -> Optional[StrategySetup]:
        if close >= previous_low or volume_ratio < 1.25 or ema_9 >= ema_21:
            return None
        score = 55 + min((previous_low - close) / max(atr, 0.01) * 10, 15) + min((volume_ratio - 1) * 10, 15)
        return self._short_setup(
            ticker,
            "breakdown short",
            close,
            atr,
            score,
            f"break below 20-bar low {previous_low:.2f}, RSI {rsi:.1f}",
        )

    def _mean_reversion_short(self, ticker: str, close: float, previous_high: float, atr: float, volume_ratio: float, rsi: float, ema_9: float, ema_21: float) -> Optional[StrategySetup]:
        extended_above_high = close >= previous_high - (atr * 0.05)
        bearish_pressure = rsi >= 68 and ema_9 < ema_21
        if not (extended_above_high and bearish_pressure and volume_ratio >= 1.2):
            return None
        score = 52 + min((rsi - 65), 18) + min((volume_ratio - 1) * 8, 10)
        return self._short_setup(
            ticker,
            "mean reversion short",
            close,
            atr,
            score,
            f"overbought rejection near 20-bar high {previous_high:.2f}, RSI {rsi:.1f}",
        )

    @staticmethod
    def _long_setup(ticker: str, setup_type: str, close: float, atr: float, score: float, reason: str) -> StrategySetup:
        risk = max(atr, close * 0.01)
        return StrategySetup(
            ticker=ticker,
            direction="long",
            setup_type=setup_type,
            score=round(clamp(score, 0, 100), 2),
            entry_zone_low=round(close - risk * 0.15, 2),
            entry_zone_high=round(close + risk * 0.15, 2),
            stop_loss=round(close - risk, 2),
            targets=(round(close + risk * 1.5, 2), round(close + risk * 2.0, 2), round(close + risk * 3.0, 2)),
            reason=reason,
            do_not_enter_conditions=[],
        )

    @staticmethod
    def _short_setup(ticker: str, setup_type: str, close: float, atr: float, score: float, reason: str) -> StrategySetup:
        risk = max(atr, close * 0.01)
        return StrategySetup(
            ticker=ticker,
            direction="short",
            setup_type=setup_type,
            score=round(clamp(score, 0, 100), 2),
            entry_zone_low=round(close - risk * 0.15, 2),
            entry_zone_high=round(close + risk * 0.15, 2),
            stop_loss=round(close + risk, 2),
            targets=(round(close - risk * 1.5, 2), round(close - risk * 2.0, 2), round(close - risk * 3.0, 2)),
            reason=reason,
            do_not_enter_conditions=[],
        )
