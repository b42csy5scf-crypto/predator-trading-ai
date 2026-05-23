import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, TypeVar

from predator_trading_ai.utils.logger import setup_logger


T = TypeVar("T")


@dataclass(frozen=True)
class HealthEvent:
    component: str
    status: str
    message: str
    created_at: str


class RetryPolicy:
    def __init__(self, attempts: int = 3, base_delay_seconds: float = 1.0, max_delay_seconds: float = 10.0) -> None:
        self.attempts = attempts
        self.base_delay_seconds = base_delay_seconds
        self.max_delay_seconds = max_delay_seconds
        self.logger = setup_logger(__name__)

    def run(self, label: str, func: Callable[[], T], fallback: T) -> T:
        delay = self.base_delay_seconds
        for attempt in range(1, self.attempts + 1):
            try:
                return func()
            except Exception as exc:
                self.logger.warning("%s failed on attempt %d/%d: %s", label, attempt, self.attempts, exc)
                if attempt == self.attempts:
                    self.logger.exception("%s exhausted retries.", label)
                    return fallback
                time.sleep(delay)
                delay = min(delay * 2, self.max_delay_seconds)
        return fallback


class HealthMonitor:
    def __init__(self) -> None:
        self.events: list[HealthEvent] = []

    def record(self, component: str, status: str, message: str) -> HealthEvent:
        event = HealthEvent(
            component=component,
            status=status,
            message=message,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        self.events.append(event)
        return event

    @property
    def latest_status(self) -> str:
        if not self.events:
            return "unknown"
        return self.events[-1].status


class CircuitBreaker:
    def __init__(self, max_failures: int = 5) -> None:
        self.max_failures = max_failures

    def record_success(self, state) -> bool:
        changed = state.consecutive_failures != 0 or state.safe_mode
        state.consecutive_failures = 0
        if state.safe_mode and state.safe_mode_reason == "consecutive failures exceeded watchdog limit":
            state.safe_mode = False
            state.safe_mode_reason = None
        return changed

    def record_failure(self, state, reason: str) -> bool:
        state.consecutive_failures += 1
        if state.consecutive_failures >= self.max_failures:
            state.safe_mode = True
            state.safe_mode_reason = reason
            return True
        return False

    @staticmethod
    def trip(state, reason: str) -> None:
        state.safe_mode = True
        state.safe_mode_reason = reason
