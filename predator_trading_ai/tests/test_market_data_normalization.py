import pandas as pd

from predator_trading_ai.config import Settings
from predator_trading_ai.data.market_data import MarketDataClient


def client() -> MarketDataClient:
    return MarketDataClient(Settings())


def test_normalize_alpaca_multiindex_bars() -> None:
    index = pd.MultiIndex.from_tuples(
        [("AAPL", pd.Timestamp("2026-01-01T14:30:00Z"))],
        names=["symbol", "timestamp"],
    )
    frame = pd.DataFrame(
        {
            "open": [100],
            "high": [101],
            "low": [99],
            "close": [100.5],
            "volume": [1_000_000],
            "trade_count": [123],
        },
        index=index,
    )

    normalized = client()._normalize_ohlcv(frame, ticker="AAPL", source="alpaca")
    assert list(normalized.columns) == ["timestamp", "open", "high", "low", "close", "volume"]
    assert normalized.iloc[0]["close"] == 100.5


def test_normalize_missing_columns_returns_empty_instead_of_keyerror() -> None:
    frame = pd.DataFrame({"open": [100], "close": [101]})
    normalized = client()._normalize_ohlcv(frame, ticker="AAPL", source="test")
    assert normalized.empty


def test_normalize_yfinance_style_columns() -> None:
    frame = pd.DataFrame(
        {
            "Date": [pd.Timestamp("2026-01-01")],
            "Open": [100],
            "High": [102],
            "Low": [99],
            "Close": [101],
            "Volume": [2_000_000],
        }
    )
    normalized = client()._normalize_ohlcv(frame, ticker="AAPL", source="yfinance")
    assert normalized.iloc[0]["volume"] == 2_000_000
