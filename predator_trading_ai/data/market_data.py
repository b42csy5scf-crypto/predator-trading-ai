from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import numpy as np
import pandas as pd

from predator_trading_ai.config import Settings, get_settings
from predator_trading_ai.utils.logger import setup_logger


@dataclass(frozen=True)
class MarketSnapshot:
    ticker: str
    price: float
    bid: Optional[float]
    ask: Optional[float]
    volume: int
    vwap: Optional[float]
    timestamp: datetime


class MarketDataClient:
    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings = settings or get_settings()
        self.logger = setup_logger(__name__, self.settings.log_level)
        self._stock_client = None

    def _alpaca_client(self):
        if self._stock_client is not None:
            return self._stock_client
        if not self.settings.alpaca_api_key or not self.settings.alpaca_secret_key:
            self.logger.warning("Alpaca credentials missing; market data client is offline.")
            return None
        try:
            from alpaca.data.historical import StockHistoricalDataClient

            self._stock_client = StockHistoricalDataClient(
                self.settings.alpaca_api_key,
                self.settings.alpaca_secret_key,
            )
            return self._stock_client
        except Exception as exc:
            self.logger.exception("Failed to initialize Alpaca market data client: %s", exc)
            return None

    def get_bars(
        self,
        ticker: str,
        start: datetime,
        end: datetime,
        timeframe: str = "1Day",
    ) -> pd.DataFrame:
        client = self._alpaca_client()
        if client is None:
            return self._get_polygon_bars(ticker, start, end, timeframe)
        try:
            from alpaca.data.requests import StockBarsRequest
            from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

            tf = self._alpaca_timeframe(timeframe, TimeFrame, TimeFrameUnit)
            request = StockBarsRequest(symbol_or_symbols=ticker, start=start, end=end, timeframe=tf)
            bars = client.get_stock_bars(request).df
            if bars.empty:
                return pd.DataFrame()
            if isinstance(bars.index, pd.MultiIndex):
                bars = bars.xs(ticker, level=0)
            return bars.reset_index()
        except Exception as exc:
            self.logger.exception("Failed to fetch bars for %s: %s", ticker, exc)
            return self._get_polygon_bars(ticker, start, end, timeframe)

    def get_recent_bars(self, ticker: str, lookback_days: int = 45, timeframe: str = "1Day") -> pd.DataFrame:
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=lookback_days)
        bars = self.get_bars(ticker, start, end, timeframe)
        if bars.empty:
            return bars
        return self.add_indicators(bars)

    def get_latest_snapshot(self, ticker: str) -> Optional[MarketSnapshot]:
        client = self._alpaca_client()
        if client is None:
            return self._get_yfinance_snapshot(ticker)
        try:
            from alpaca.data.requests import StockLatestQuoteRequest, StockLatestTradeRequest

            quote_request = StockLatestQuoteRequest(symbol_or_symbols=ticker)
            trade_request = StockLatestTradeRequest(symbol_or_symbols=ticker)
            quotes = client.get_stock_latest_quote(quote_request)
            trades = client.get_stock_latest_trade(trade_request)
            quote = quotes[ticker] if isinstance(quotes, dict) else quotes
            trade = trades[ticker] if isinstance(trades, dict) else trades
            bid = float(getattr(quote, "bid_price", 0) or 0) or None
            ask = float(getattr(quote, "ask_price", 0) or 0) or None
            price = float(getattr(trade, "price", 0) or 0)
            size = int(getattr(trade, "size", 0) or 0)
            timestamp = getattr(trade, "timestamp", datetime.now(timezone.utc))
            if price <= 0:
                return None
            return MarketSnapshot(ticker, price, bid, ask, size, None, timestamp)
        except Exception as exc:
            self.logger.exception("Failed to fetch latest snapshot for %s: %s", ticker, exc)
            return self._get_yfinance_snapshot(ticker)

    def add_indicators(self, bars: pd.DataFrame) -> pd.DataFrame:
        if bars.empty:
            return bars
        df = bars.copy()
        for column in ["open", "high", "low", "close", "volume"]:
            if column not in df:
                raise ValueError(f"Missing required OHLCV column: {column}")

        typical = (df["high"] + df["low"] + df["close"]) / 3
        df["vwap"] = (typical * df["volume"]).cumsum() / df["volume"].replace(0, np.nan).cumsum()
        df["ema_9"] = df["close"].ewm(span=9, adjust=False).mean()
        df["ema_21"] = df["close"].ewm(span=21, adjust=False).mean()
        df["rsi_14"] = self._rsi(df["close"], 14)
        df["atr_14"] = self._atr(df, 14)
        ema_12 = df["close"].ewm(span=12, adjust=False).mean()
        ema_26 = df["close"].ewm(span=26, adjust=False).mean()
        df["macd"] = ema_12 - ema_26
        df["macd_signal"] = df["macd"].ewm(span=9, adjust=False).mean()
        df["volume_sma_20"] = df["volume"].rolling(20, min_periods=1).mean()
        return df

    def _get_polygon_bars(
        self,
        ticker: str,
        start: datetime,
        end: datetime,
        timeframe: str,
    ) -> pd.DataFrame:
        if not self.settings.polygon_api_key:
            self.logger.warning("Polygon API key missing; no fallback market data for %s.", ticker)
            return self._get_yfinance_bars(ticker, start, end, timeframe)
        try:
            from polygon import RESTClient

            client = RESTClient(self.settings.polygon_api_key)
            multiplier, timespan = self._polygon_timeframe(timeframe)
            aggs = client.list_aggs(
                ticker=ticker,
                multiplier=multiplier,
                timespan=timespan,
                from_=start.date().isoformat(),
                to=end.date().isoformat(),
                limit=5000,
            )
            rows = []
            for agg in aggs:
                timestamp = getattr(agg, "timestamp", None)
                rows.append(
                    {
                        "timestamp": pd.to_datetime(timestamp, unit="ms", utc=True) if timestamp else None,
                        "open": float(getattr(agg, "open", 0)),
                        "high": float(getattr(agg, "high", 0)),
                        "low": float(getattr(agg, "low", 0)),
                        "close": float(getattr(agg, "close", 0)),
                        "volume": int(getattr(agg, "volume", 0)),
                    }
                )
            return pd.DataFrame(rows)
        except Exception as exc:
            self.logger.exception("Failed to fetch Polygon fallback bars for %s: %s", ticker, exc)
            return self._get_yfinance_bars(ticker, start, end, timeframe)

    def _get_yfinance_bars(
        self,
        ticker: str,
        start: datetime,
        end: datetime,
        timeframe: str,
    ) -> pd.DataFrame:
        try:
            import yfinance as yf

            interval = "5m" if timeframe == "5Min" else "1d"
            period = "60d" if interval == "5m" else None
            frame = yf.download(
                ticker,
                start=None if period else start.date().isoformat(),
                end=None if period else end.date().isoformat(),
                period=period,
                interval=interval,
                progress=False,
                auto_adjust=False,
                threads=False,
            )
            if frame.empty:
                return pd.DataFrame()
            if isinstance(frame.columns, pd.MultiIndex):
                frame.columns = frame.columns.get_level_values(0)
            frame = frame.rename(
                columns={
                    "Open": "open",
                    "High": "high",
                    "Low": "low",
                    "Close": "close",
                    "Volume": "volume",
                }
            )
            frame = frame.reset_index().rename(columns={"Datetime": "timestamp", "Date": "timestamp"})
            return frame[["timestamp", "open", "high", "low", "close", "volume"]].dropna()
        except Exception as exc:
            self.logger.exception("Failed to fetch yfinance fallback bars for %s: %s", ticker, exc)
            return pd.DataFrame()

    def _get_yfinance_snapshot(self, ticker: str) -> Optional[MarketSnapshot]:
        bars = self._get_yfinance_bars(
            ticker,
            datetime.now(timezone.utc) - timedelta(days=5),
            datetime.now(timezone.utc),
            "5Min",
        )
        if bars.empty:
            return None
        latest = bars.iloc[-1]
        price = float(latest["close"])
        timestamp = pd.to_datetime(latest["timestamp"], utc=True).to_pydatetime()
        return MarketSnapshot(
            ticker=ticker,
            price=price,
            bid=price * 0.9995,
            ask=price * 1.0005,
            volume=int(latest.get("volume", 0) or 0),
            vwap=None,
            timestamp=timestamp,
        )

    @staticmethod
    def _alpaca_timeframe(timeframe: str, timeframe_cls, timeframe_unit_cls):
        if timeframe == "1Day":
            return timeframe_cls.Day
        if timeframe == "5Min":
            return timeframe_cls(5, timeframe_unit_cls.Minute)
        return timeframe_cls.Minute

    @staticmethod
    def _polygon_timeframe(timeframe: str) -> tuple[int, str]:
        if timeframe == "1Day":
            return 1, "day"
        if timeframe == "5Min":
            return 5, "minute"
        return 1, "minute"

    @staticmethod
    def _rsi(series: pd.Series, period: int) -> pd.Series:
        delta = series.diff()
        gain = delta.clip(lower=0).rolling(period, min_periods=period).mean()
        loss = -delta.clip(upper=0).rolling(period, min_periods=period).mean()
        rs = gain / loss.replace(0, np.nan)
        return (100 - (100 / (1 + rs))).fillna(50)

    @staticmethod
    def _atr(df: pd.DataFrame, period: int) -> pd.Series:
        high_low = df["high"] - df["low"]
        high_close = (df["high"] - df["close"].shift()).abs()
        low_close = (df["low"] - df["close"].shift()).abs()
        true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        return true_range.rolling(period, min_periods=1).mean()
