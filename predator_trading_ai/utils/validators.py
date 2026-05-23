from typing import Iterable, Optional


def has_required_values(values: Iterable[object]) -> bool:
    return all(value is not None and value != "" for value in values)


def spread_pct(bid: Optional[float], ask: Optional[float]) -> float:
    if bid is None or ask is None or bid <= 0 or ask <= 0 or ask < bid:
        return float("inf")
    midpoint = (bid + ask) / 2
    return ((ask - bid) / midpoint) * 100 if midpoint else float("inf")


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))

