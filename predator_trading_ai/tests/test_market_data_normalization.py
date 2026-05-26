import pandas as pd
from types import SimpleNamespace

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


def test_alpaca_bar_request_uses_iex_feed() -> None:
    from alpaca.data.enums import DataFeed

    captured = {}

    class FakeAlpacaClient:
        def get_stock_bars(self, request):
            captured["feed"] = request.feed
            index = pd.MultiIndex.from_tuples(
                [("AAPL", pd.Timestamp("2026-01-01T14:30:00Z"))],
                names=["symbol", "timestamp"],
            )
            frame = pd.DataFrame(
                {"open": [100], "high": [101], "low": [99], "close": [100.5], "volume": [1000]},
                index=index,
            )
            return SimpleNamespace(df=frame)

    market_client = client()
    market_client._stock_client = FakeAlpacaClient()
    bars = market_client.get_bars("AAPL", pd.Timestamp("2026-01-01T14:30:00Z"), pd.Timestamp("2026-01-01T15:30:00Z"))
    assert captured["feed"] == DataFeed.IEX
    assert not bars.empty


def test_alpaca_latest_snapshot_requests_use_iex_feed() -> None:
    from alpaca.data.enums import DataFeed

    captured = {}

    class FakeAlpacaClient:
        def get_stock_latest_quote(self, request):
            captured["quote_feed"] = request.feed
            return {"AAPL": SimpleNamespace(bid_price=100, ask_price=101)}

        def get_stock_latest_trade(self, request):
            captured["trade_feed"] = request.feed
            return {"AAPL": SimpleNamespace(price=100.5, size=10, timestamp=pd.Timestamp("2026-01-01T14:30:00Z"))}

    market_client = client()
    market_client._stock_client = FakeAlpacaClient()
    snapshot = market_client.get_latest_snapshot("AAPL")
    assert captured["quote_feed"] == DataFeed.IEX
    assert captured["trade_feed"] == DataFeed.IEX
    assert snapshot is not None
