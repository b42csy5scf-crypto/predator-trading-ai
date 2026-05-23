from dataclasses import dataclass
from typing import Iterable, Optional
from urllib.error import URLError
from urllib.request import Request, urlopen
import json

from predator_trading_ai.config import Settings, get_settings
from predator_trading_ai.utils.logger import setup_logger
from predator_trading_ai.utils.validators import clamp, spread_pct


@dataclass(frozen=True)
class OptionFlowEvent:
    ticker: str
    contract_symbol: str
    option_type: str
    strike: float
    expiry: str
    volume: int
    open_interest: int
    premium: float
    bid: float
    ask: float
    trade_type: str


class OptionsDataClient:
    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings = settings or get_settings()
        self.logger = setup_logger(__name__, self.settings.log_level)

    def fetch_chain(self, ticker: str) -> list[dict]:
        if not self.settings.polygon_api_key:
            self.logger.warning("Polygon API key missing; options chain unavailable for %s.", ticker)
            return []
        try:
            from polygon import RESTClient

            client = RESTClient(self.settings.polygon_api_key)
            contracts = client.list_options_contracts(underlying_ticker=ticker, limit=1000)
            return [contract.__dict__ for contract in contracts]
        except Exception as exc:
            self.logger.exception("Failed to fetch options chain for %s: %s", ticker, exc)
            return []


class UnusualWhalesClient:
    base_url = "https://api.unusualwhales.com/api"

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings = settings or get_settings()
        self.logger = setup_logger(__name__, self.settings.log_level)

    def fetch_recent_flow(self, ticker: str) -> list[OptionFlowEvent]:
        if not self.settings.unusual_whales_api_key:
            self.logger.info("Unusual Whales API key missing; skipping options flow for %s.", ticker)
            return []
        url = f"{self.base_url}/stock/{ticker}/option-contracts/flow"
        request = Request(url, headers={"Authorization": f"Bearer {self.settings.unusual_whales_api_key}"})
        try:
            with urlopen(request, timeout=15) as response:
                payload = json.loads(response.read().decode("utf-8"))
            records = payload.get("data", payload if isinstance(payload, list) else [])
            events = [self._to_event(ticker, record) for record in records]
            return [event for event in events if event is not None]
        except (URLError, TimeoutError, ValueError, KeyError, TypeError) as exc:
            self.logger.exception("Failed to fetch Unusual Whales flow for %s: %s", ticker, exc)
            return []

    def _to_event(self, ticker: str, record: dict) -> Optional[OptionFlowEvent]:
        try:
            option_type = str(record.get("option_type") or record.get("type") or record.get("call_put") or "").lower()
            if option_type in {"c", "call"}:
                option_type = "call"
            elif option_type in {"p", "put"}:
                option_type = "put"
            else:
                return None
            premium = float(record.get("premium") or record.get("total_premium") or record.get("cost_basis") or 0)
            volume = int(float(record.get("volume") or record.get("size") or 0))
            open_interest = int(float(record.get("open_interest") or record.get("oi") or 0))
            bid = float(record.get("bid") or record.get("bid_price") or 0)
            ask = float(record.get("ask") or record.get("ask_price") or 0)
            trade_type = str(record.get("trade_type") or record.get("side") or record.get("execution_type") or "unknown")
            return OptionFlowEvent(
                ticker=ticker,
                contract_symbol=str(record.get("contract_symbol") or record.get("option_symbol") or ""),
                option_type=option_type,
                strike=float(record.get("strike") or 0),
                expiry=str(record.get("expiry") or record.get("expiration") or ""),
                volume=volume,
                open_interest=open_interest,
                premium=premium,
                bid=bid,
                ask=ask,
                trade_type=trade_type,
            )
        except (TypeError, ValueError):
            return None


class OptionsFlowDetector:
    def __init__(
        self,
        min_volume_oi_ratio: float = 2.0,
        min_premium: float = 100_000,
        max_spread_pct: float = 10.0,
    ) -> None:
        self.min_volume_oi_ratio = min_volume_oi_ratio
        self.min_premium = min_premium
        self.max_spread_pct = max_spread_pct

    def detect(self, events: Iterable[OptionFlowEvent]) -> list[dict]:
        detected: list[dict] = []
        calls = 0
        puts = 0
        for event in events:
            if event.option_type.lower() == "call":
                calls += event.volume
            else:
                puts += event.volume
            score = self.liquidity_score(event)
            oi = max(event.open_interest, 1)
            volume_oi_ratio = event.volume / oi
            is_unusual = (
                event.premium >= self.min_premium
                and volume_oi_ratio >= self.min_volume_oi_ratio
                and score >= 60
            )
            noisy = event.trade_type.lower() not in {"sweep", "block", "split"}
            if is_unusual and not noisy:
                detected.append(
                    {
                        "ticker": event.ticker,
                        "contract_symbol": event.contract_symbol,
                        "option_type": event.option_type.lower(),
                        "strike": event.strike,
                        "expiry": event.expiry,
                        "volume": event.volume,
                        "open_interest": event.open_interest,
                        "premium": event.premium,
                        "trade_type": event.trade_type.lower(),
                        "call_put_ratio": calls / max(puts, 1),
                        "liquidity_score": score,
                        "is_unusual": 1,
                        "reason": (
                            f"{event.trade_type} premium ${event.premium:,.0f}, "
                            f"volume/OI {volume_oi_ratio:.2f}, liquidity {score:.0f}"
                        ),
                    }
                )
        return detected

    def liquidity_score(self, event: OptionFlowEvent) -> float:
        spread = spread_pct(event.bid, event.ask)
        spread_component = clamp(100 - (spread / self.max_spread_pct * 100), 0, 100)
        oi_component = clamp(event.open_interest / 1000 * 100, 0, 100)
        volume_component = clamp(event.volume / 1000 * 100, 0, 100)
        return round((spread_component * 0.5) + (oi_component * 0.25) + (volume_component * 0.25), 2)
