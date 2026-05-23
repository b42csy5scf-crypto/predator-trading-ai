from predator_trading_ai.database.db import Database


class PerformanceReport:
    def __init__(self, db: Database) -> None:
        self.db = db

    def build(self) -> str:
        rows = self.db.fetch_all("SELECT result_r, predicted_win_rate FROM trades WHERE status='closed'")
        if not rows:
            return "No closed trades available for performance reporting."
        wins = [row for row in rows if (row["result_r"] or 0) > 0]
        losses = [abs(row["result_r"] or 0) for row in rows if (row["result_r"] or 0) < 0]
        gross_profit = sum(row["result_r"] for row in wins)
        gross_loss = sum(losses)
        profit_factor = gross_profit / gross_loss if gross_loss else float("inf")
        win_rate = len(wins) / len(rows) * 100
        return (
            "Predator Trading AI Performance Report\n"
            f"Trades: {len(rows)}\n"
            f"Win rate: {win_rate:.1f}%\n"
            f"Profit factor: {profit_factor:.2f}"
        )
