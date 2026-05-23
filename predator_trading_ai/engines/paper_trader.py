from dataclasses import asdict, dataclass
from typing import Optional

from predator_trading_ai.config import Settings, get_settings
from predator_trading_ai.database.db import Database
from predator_trading_ai.engines.signal_engine import TradingSignal
from predator_trading_ai.utils.logger import setup_logger


@dataclass(frozen=True)
class PaperOrder:
    signal_id: Optional[int]
    ticker: str
    direction: str
    entry_price: float
    stop_loss: float
    target_price: float
    quantity: float
    status: str = "open"
    predicted_win_rate: Optional[float] = None


class PaperTrader:
    def __init__(self, settings: Optional[Settings] = None, db: Optional[Database] = None) -> None:
        self.settings = settings or get_settings()
        self.db = db or Database(self.settings)
        self.logger = setup_logger(__name__, self.settings.log_level)

    def place_from_signal(self, signal: TradingSignal, signal_id: Optional[int] = None) -> PaperOrder:
        entry = (signal.entry_zone_low + signal.entry_zone_high) / 2
        order = PaperOrder(
            signal_id=signal_id,
            ticker=signal.ticker,
            direction=signal.direction,
            entry_price=round(entry, 2),
            stop_loss=signal.stop_loss,
            target_price=signal.target_1,
            quantity=signal.position_size,
            predicted_win_rate=signal.expected_win_rate,
        )
        self.db.insert_dict("trades", asdict(order))
        self.logger.info("Paper trade logged for %s %s", signal.ticker, signal.direction)
        return order

    def live_trading_allowed(self, confirmation: Optional[str] = None) -> bool:
        if not self.settings.live_trading:
            return False
        if self.settings.require_live_confirmation:
            return confirmation == self.settings.live_confirmation_phrase
        return True

