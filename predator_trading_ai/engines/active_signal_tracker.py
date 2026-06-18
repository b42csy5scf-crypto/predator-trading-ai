from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from predator_trading_ai.database.db import Database
from predator_trading_ai.engines.signal_engine import TradingSignal
from predator_trading_ai.engines.strategy_engine import StrategySetup


@dataclass(frozen=True)
class SignalUpdate:
    active_signal_id: int
    ticker: str
    update_type: str
    price: float
    status: str
    message: str


class ActiveSignalTracker:
    def __init__(self, db: Database) -> None:
        self.db = db

    def register_trading_signal(self, signal: TradingSignal, grade: str) -> int:
        return self.register(
            ticker=signal.ticker,
            grade=grade,
            direction=signal.direction,
            entry_zone_low=signal.entry_zone_low,
            entry_zone_high=signal.entry_zone_high,
            stop_loss=signal.stop_loss,
            targets=(signal.target_1, signal.target_2, signal.target_3),
        )

    def register_watch_signal(self, setup: StrategySetup) -> int:
        return self.register(
            ticker=setup.ticker,
            grade=setup.signal_tier,
            direction=setup.direction,
            entry_zone_low=setup.entry_zone_low,
            entry_zone_high=setup.entry_zone_high,
            stop_loss=setup.stop_loss,
            targets=setup.targets,
        )

    def register(
        self,
        ticker: str,
        grade: str,
        direction: str,
        entry_zone_low: float,
        entry_zone_high: float,
        stop_loss: float,
        targets: tuple[float, float, float],
        sent_at: Optional[datetime] = None,
    ) -> int:
        timestamp = (sent_at or datetime.now(timezone.utc)).isoformat()
        self.db.execute(
            """
            UPDATE active_signals
            SET status = 'closed', close_reason = 'superseded', closed_at = ?, updated_at = ?
            WHERE ticker = ? AND status = 'active'
            """,
            [timestamp, timestamp, ticker],
        )
        return self.db.insert_dict(
            "active_signals",
            {
                "ticker": ticker,
                "grade": grade,
                "direction": direction,
                "entry_zone_low": entry_zone_low,
                "entry_zone_high": entry_zone_high,
                "stop_loss": stop_loss,
                "tp1": targets[0],
                "tp2": targets[1],
                "tp3": targets[2],
                "sent_at": timestamp,
                "status": "active",
            },
        )

    def check_ticker(self, ticker: str, current_price: float) -> list[SignalUpdate]:
        rows = self.db.fetch_all(
            "SELECT * FROM active_signals WHERE ticker = ? AND status = 'active' ORDER BY id",
            [ticker],
        )
        updates: list[SignalUpdate] = []
        for row in rows:
            updates.extend(self._evaluate_row(row, current_price))
        return updates

    def _evaluate_row(self, row, current_price: float) -> list[SignalUpdate]:
        signal_id = int(row["id"])
        direction = row["direction"]
        if direction == "short":
            stop_hit = current_price >= float(row["stop_loss"])
            target_hit = lambda target: current_price <= float(target)
        else:
            stop_hit = current_price <= float(row["stop_loss"])
            target_hit = lambda target: current_price >= float(target)

        if stop_hit:
            update = self._create_update(row, "stop_loss", current_price, "closed")
            self._close(signal_id, current_price, "invalidated")
            self._record_completed_trade(row, "SL", "closed", current_price, "stop_loss")
            return [update] if update else []

        updates = []
        for number in (1, 2, 3):
            flag = f"tp{number}_hit"
            if int(row[flag]) or not target_hit(row[f"tp{number}"]):
                continue
            status = "closed" if number == 3 else "active"
            update = self._create_update(row, f"tp{number}", current_price, status)
            if update:
                updates.append(update)
            self.db.execute(
                f"UPDATE active_signals SET {flag} = 1, last_price = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                [current_price, signal_id],
            )
            self._record_completed_trade(row, f"TP{number}", status, current_price)
        if any(update.update_type == "tp3" for update in updates):
            self._close(signal_id, current_price, "tp3_completed")
        elif updates:
            self.db.execute(
                "UPDATE active_signals SET last_price = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                [current_price, signal_id],
            )
        return updates

    def _create_update(self, row, update_type: str, price: float, status: str) -> Optional[SignalUpdate]:
        signal_id = int(row["id"])
        existing = self.db.fetch_all(
            "SELECT id FROM signal_updates WHERE active_signal_id = ? AND update_type = ?",
            [signal_id, update_type],
        )
        if existing:
            return None
        title = {
            "tp1": "TP1 Hit",
            "tp2": "TP2 Hit",
            "tp3": "TP3 Hit",
            "stop_loss": "Stop Loss Hit",
        }[update_type]
        if update_type == "stop_loss":
            detail = f"Stop: {float(row['stop_loss']):.2f}"
            status_line = "Closed / Invalidated"
        else:
            detail = f"{update_type.upper()}: {float(row[update_type]):.2f}"
            status_line = "Closed / Signal completed" if update_type == "tp3" else f"Active / {update_type.upper()} reached"
        message = (
            f"Predator Update: {title}\n"
            f"Ticker: {row['ticker']}\n"
            f"Signal: {self.short_grade(row['grade'])}\n"
            f"Entry: {float(row['entry_zone_low']):.2f} - {float(row['entry_zone_high']):.2f}\n"
            f"{detail}\n"
            f"Current Price: {price:.2f}\n"
            f"Status: {status_line}"
        )
        self.db.insert_dict(
            "signal_updates",
            {
                "active_signal_id": signal_id,
                "ticker": row["ticker"],
                "update_type": update_type,
                "price": price,
                "status": status,
                "message": message,
            },
        )
        return SignalUpdate(signal_id, row["ticker"], update_type, price, status, message)

    def _close(self, signal_id: int, price: float, reason: str) -> None:
        self.db.execute(
            """
            UPDATE active_signals
            SET status = 'closed', last_price = ?, close_reason = ?,
                closed_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            [price, reason, signal_id],
        )

    def _record_completed_trade(
        self,
        row,
        outcome: str,
        status: str,
        price: float,
        stop_loss_reason: str | None = None,
    ) -> None:
        signal_id = int(row["id"])
        entry = (float(row["entry_zone_low"]) + float(row["entry_zone_high"])) / 2
        risk = abs(entry - float(row["stop_loss"]))
        r_multiple = self._outcome_r_multiple(row, outcome, risk, entry)
        alert = self._matching_sent_alert(row)
        closed_at = "CURRENT_TIMESTAMP" if status == "closed" else "NULL"
        self.db.execute(
            f"""
            INSERT INTO completed_trades (
                active_signal_id, ticker, grade, direction,
                entry_zone_low, entry_zone_high, entry_price, stop_loss,
                tp1, tp2, tp3, outcome, status, opened_at, closed_at,
                close_price, r_multiple, regime, score, stop_loss_reason
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, {closed_at}, ?, ?, ?, ?, ?)
            ON CONFLICT(active_signal_id) DO UPDATE SET
                updated_at = CURRENT_TIMESTAMP,
                outcome = excluded.outcome,
                status = excluded.status,
                closed_at = CASE WHEN excluded.status = 'closed' THEN CURRENT_TIMESTAMP ELSE completed_trades.closed_at END,
                close_price = excluded.close_price,
                r_multiple = excluded.r_multiple,
                regime = COALESCE(excluded.regime, completed_trades.regime),
                score = COALESCE(excluded.score, completed_trades.score),
                stop_loss_reason = COALESCE(excluded.stop_loss_reason, completed_trades.stop_loss_reason)
            """,
            [
                signal_id,
                row["ticker"],
                row["grade"],
                row["direction"],
                float(row["entry_zone_low"]),
                float(row["entry_zone_high"]),
                entry,
                float(row["stop_loss"]),
                float(row["tp1"]),
                float(row["tp2"]),
                float(row["tp3"]),
                outcome,
                status,
                row["sent_at"],
                price,
                r_multiple,
                alert["regime"] if alert else None,
                float(alert["score"]) if alert and alert["score"] is not None else None,
                stop_loss_reason,
            ],
        )

    @staticmethod
    def _outcome_r_multiple(row, outcome: str, risk: float, entry: float) -> float:
        if risk <= 0:
            return 0.0
        if outcome == "SL":
            return -1.0
        target_key = outcome.lower()
        return round(abs(float(row[target_key]) - entry) / risk, 3)

    def _matching_sent_alert(self, row):
        rows = self.db.fetch_all(
            """
            SELECT *
            FROM sent_alerts
            WHERE ticker = ? AND grade = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            [row["ticker"], row["grade"]],
        )
        return rows[0] if rows else None

    @staticmethod
    def short_grade(grade: str) -> str:
        return grade.replace(" Signal", "").replace(" Watch Alert", "")
