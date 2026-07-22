from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional

from predator_trading_ai.config import Settings, get_settings
from predator_trading_ai.database.db import Database


class SignalForensicsReport:
    """Read-only reconstruction of stored signal lifecycle data."""

    def __init__(self, settings: Optional[Settings] = None, db: Optional[Database] = None) -> None:
        self.settings = settings or get_settings()
        self.db = db or Database(self.settings)

    def build(self, ticker: str, limit: int = 3) -> str:
        symbol = (ticker or "").strip().upper()
        if not symbol:
            return "Signal Forensics\nUsage: /signal_forensics TICKER [limit]"
        safe_limit = max(1, min(int(limit or 3), 10))
        signals = self.signal_rows(symbol, safe_limit)
        if not signals:
            return f"Signal Forensics: {symbol}\nNo stored signals found."

        signal_ids = [int(row_get(row, "active_signal_id")) for row in signals]
        paths = self.price_path_rows(signal_ids)
        updates = self.update_rows(signal_ids)
        by_signal_path = defaultdict(list)
        by_signal_updates = defaultdict(list)
        for row in paths:
            by_signal_path[int(row_get(row, "signal_id"))].append(row)
        for row in updates:
            by_signal_updates[int(row_get(row, "active_signal_id"))].append(row)

        sections = [f"Signal Forensics: {symbol}\nRead-only production lifecycle evidence."]
        for signal in signals:
            signal_id = int(row_get(signal, "active_signal_id"))
            sections.append(self.signal_section(signal, by_signal_path[signal_id], by_signal_updates[signal_id]))
        return "\n\n".join(sections)

    def signal_rows(self, ticker: str, limit: int) -> list[Any]:
        return self.db.fetch_all(
            """
            SELECT
                a.id AS active_signal_id,
                a.ticker,
                a.grade AS accepted_grade,
                COALESCE(d.setup_grade, d.grade, a.grade) AS setup_grade,
                a.direction,
                a.sent_at AS entry_timestamp,
                a.entry_zone_low,
                a.entry_zone_high,
                a.stop_loss,
                a.tp1,
                a.tp2,
                a.tp3,
                a.status AS active_status,
                a.close_reason,
                o.tp1_hit_at,
                o.tp2_hit_at,
                o.tp3_hit_at,
                o.sl_hit_at,
                o.final_outcome,
                o.realized_r,
                o.exit_timestamp,
                c.outcome AS completed_outcome,
                c.r_multiple,
                c.closed_at
            FROM active_signals a
            LEFT JOIN signal_diagnostics d
              ON d.id = (
                  SELECT MAX(d2.id)
                  FROM signal_diagnostics d2
                  WHERE d2.active_signal_id = a.id
              )
            LEFT JOIN signal_outcome_diagnostics o
              ON o.active_signal_id = a.id
            LEFT JOIN completed_trades c
              ON c.active_signal_id = a.id
            WHERE UPPER(a.ticker) = ?
            ORDER BY a.sent_at DESC, a.id DESC
            LIMIT ?
            """,
            [ticker, limit],
        )

    def price_path_rows(self, signal_ids: list[int]) -> list[Any]:
        if not signal_ids:
            return []
        placeholders = ",".join("?" for _ in signal_ids)
        return self.db.fetch_all(
            f"""
            SELECT signal_id, timestamp, price, high, low, event_type
            FROM price_path
            WHERE signal_id IN ({placeholders})
            ORDER BY signal_id, timestamp, id
            """,
            signal_ids,
        )

    def update_rows(self, signal_ids: list[int]) -> list[Any]:
        if not signal_ids:
            return []
        placeholders = ",".join("?" for _ in signal_ids)
        return self.db.fetch_all(
            f"""
            SELECT active_signal_id, created_at, update_type, price, status
            FROM signal_updates
            WHERE active_signal_id IN ({placeholders})
            ORDER BY active_signal_id, created_at, id
            """,
            signal_ids,
        )

    def signal_section(self, signal: Any, paths: list[Any], updates: list[Any]) -> str:
        signal_id = int(row_get(signal, "active_signal_id"))
        entry_low = float(row_get(signal, "entry_zone_low") or 0)
        entry_high = float(row_get(signal, "entry_zone_high") or 0)
        entry = (entry_low + entry_high) / 2
        tp1 = float(row_get(signal, "tp1") or 0)
        tp2 = float(row_get(signal, "tp2") or 0)
        stop = float(row_get(signal, "stop_loss") or 0)
        direction = str(row_get(signal, "direction") or "long").lower()
        stats = self.path_stats(paths, tp1=tp1, tp2=tp2, stop=stop, direction=direction)
        outcome = row_get(signal, "completed_outcome") or row_get(signal, "final_outcome") or row_get(signal, "close_reason") or row_get(signal, "active_status")
        r_multiple = row_get(signal, "r_multiple")
        if r_multiple is None:
            r_multiple = row_get(signal, "realized_r")

        lines = [
            f"Signal #{signal_id}",
            f"- Ticker: {row_get(signal, 'ticker')}",
            f"- Setup/accepted grade: {row_get(signal, 'setup_grade') or 'n/a'} / {row_get(signal, 'accepted_grade') or 'n/a'}",
            f"- Entry timestamp: {row_get(signal, 'entry_timestamp') or 'n/a'}",
            f"- Entry zone: {fmt(entry_low)} - {fmt(entry_high)} | midpoint {fmt(entry)}",
            f"- Stop: {fmt(stop)} | TP1/TP2/TP3: {fmt(tp1)} / {fmt(row_get(signal, 'tp2'))} / {fmt(row_get(signal, 'tp3'))}",
            f"- price_path.price max/min: {fmt(stats['max_price'])} / {fmt(stats['min_price'])}",
            f"- price_path.high max: {fmt(stats['max_high'])} | low min: {fmt(stats['min_low'])}",
            f"- TP1 sampled/high: {yesno(stats['sampled_tp1'])} / {yesno(stats['high_tp1'])} => {self.tp_classification(stats, 'tp1')}",
            f"- TP2 sampled/high: {yesno(stats['sampled_tp2'])} / {yesno(stats['high_tp2'])} => {self.tp_classification(stats, 'tp2')}",
            f"- Stop sampled/candle: {yesno(stats['sampled_stop'])} / {yesno(stats['candle_stop'])}",
            f"- Stored event timestamps: TP1={row_get(signal, 'tp1_hit_at') or 'n/a'} TP2={row_get(signal, 'tp2_hit_at') or 'n/a'} TP3={row_get(signal, 'tp3_hit_at') or 'n/a'} SL={row_get(signal, 'sl_hit_at') or 'n/a'}",
            f"- Final outcome: {outcome or 'n/a'} | R: {fmt(r_multiple)} | closed: {row_get(signal, 'closed_at') or row_get(signal, 'exit_timestamp') or 'n/a'}",
            "Timeline:",
            *self.timeline(paths, updates, signal),
        ]
        return "\n".join(lines)

    @staticmethod
    def path_stats(paths: list[Any], *, tp1: float, tp2: float, stop: float, direction: str) -> dict[str, Any]:
        prices = [safe_float(row_get(row, "price")) for row in paths if safe_float(row_get(row, "price")) is not None]
        highs = [safe_float(row_get(row, "high")) for row in paths if safe_float(row_get(row, "high")) is not None]
        lows = [safe_float(row_get(row, "low")) for row in paths if safe_float(row_get(row, "low")) is not None]
        if not paths:
            return {
                "has_path": False,
                "max_price": None,
                "min_price": None,
                "max_high": None,
                "min_low": None,
                "sampled_tp1": False,
                "high_tp1": False,
                "sampled_tp2": False,
                "high_tp2": False,
                "sampled_stop": False,
                "candle_stop": False,
            }
        if direction == "short":
            sampled_tp1 = any(price <= tp1 for price in prices)
            high_tp1 = any(low <= tp1 for low in lows)
            sampled_tp2 = any(price <= tp2 for price in prices)
            high_tp2 = any(low <= tp2 for low in lows)
            sampled_stop = any(price >= stop for price in prices)
            candle_stop = any(high >= stop for high in highs)
        else:
            sampled_tp1 = any(price >= tp1 for price in prices)
            high_tp1 = any(high >= tp1 for high in highs)
            sampled_tp2 = any(price >= tp2 for price in prices)
            high_tp2 = any(high >= tp2 for high in highs)
            sampled_stop = any(price <= stop for price in prices)
            candle_stop = any(low <= stop for low in lows)
        return {
            "has_path": True,
            "max_price": max(prices) if prices else None,
            "min_price": min(prices) if prices else None,
            "max_high": max(highs) if highs else None,
            "min_low": min(lows) if lows else None,
            "sampled_tp1": sampled_tp1,
            "high_tp1": high_tp1,
            "sampled_tp2": sampled_tp2,
            "high_tp2": high_tp2,
            "sampled_stop": sampled_stop,
            "candle_stop": candle_stop,
        }

    @staticmethod
    def tp_classification(stats: dict[str, Any], key: str) -> str:
        if not stats["has_path"]:
            return "INSUFFICIENT_PRICE_PATH_DATA"
        if stats[f"sampled_{key}"]:
            return "SAMPLED_PRICE_TP_HIT"
        if stats[f"high_{key}"]:
            return "CANDLE_HIGH_TOUCHED_BUT_SAMPLE_MISSED"
        return "TP_NOT_TOUCHED"

    @staticmethod
    def timeline(paths: list[Any], updates: list[Any], signal: Any) -> list[str]:
        events: list[tuple[str, str]] = []
        entry_ts = row_get(signal, "entry_timestamp") or ""
        events.append((str(entry_ts), f"entry {fmt(row_get(signal, 'entry_zone_low'))}-{fmt(row_get(signal, 'entry_zone_high'))}"))
        for row in paths:
            event_type = str(row_get(row, "event_type") or "scan")
            if event_type == "scan":
                continue
            events.append(
                (
                    str(row_get(row, "timestamp") or ""),
                    f"price_path {event_type} price={fmt(row_get(row, 'price'))} high={fmt(row_get(row, 'high'))} low={fmt(row_get(row, 'low'))}",
                )
            )
        for row in updates:
            events.append(
                (
                    str(row_get(row, "created_at") or ""),
                    f"signal_update {row_get(row, 'update_type')} price={fmt(row_get(row, 'price'))} status={row_get(row, 'status')}",
                )
            )
        closed_at = row_get(signal, "closed_at") or row_get(signal, "exit_timestamp")
        if closed_at:
            events.append((str(closed_at), f"closed outcome={row_get(signal, 'completed_outcome') or row_get(signal, 'final_outcome') or row_get(signal, 'close_reason') or 'n/a'}"))
        events = sorted(events, key=lambda item: item[0])
        if len(events) == 1:
            return ["- no recorded lifecycle events beyond entry"]
        return [f"- {timestamp or 'n/a'} {text}" for timestamp, text in events[:18]]


def row_get(row: Any, key: str, default: Any = None) -> Any:
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        if hasattr(row, "get"):
            return row.get(key, default)
        return default


def safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def fmt(value: Any) -> str:
    number = safe_float(value)
    if number is None:
        return "n/a"
    return f"{number:.2f}"


def yesno(value: bool) -> str:
    return "yes" if value else "no"
