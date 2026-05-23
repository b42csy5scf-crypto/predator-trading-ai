from predator_trading_ai.database.db import Database


class DailyReport:
    def __init__(self, db: Database) -> None:
        self.db = db

    def build(self) -> str:
        signals = self.db.fetch_all("SELECT COUNT(*) AS count FROM signals WHERE date(created_at)=date('now')")
        trades = self.db.fetch_all("SELECT COUNT(*) AS count FROM trades WHERE date(created_at)=date('now')")
        return (
            "Predator Trading AI Daily Summary\n"
            f"Signals today: {signals[0]['count']}\n"
            f"Trades logged today: {trades[0]['count']}"
        )

