from predator_trading_ai.database.db import Database


class LearningEngine:
    def __init__(self, db: Database) -> None:
        self.db = db

    def review_closed_trades(self) -> dict:
        rows = self.db.fetch_all(
            "SELECT ticker, direction, result_r, predicted_win_rate, actual_result FROM trades WHERE status='closed'"
        )
        if not rows:
            return {"summary": "No closed trades to review.", "suggestions": []}
        winners = [row for row in rows if (row["result_r"] or 0) > 0]
        losers = [row for row in rows if (row["result_r"] or 0) <= 0]
        suggestions: list[str] = []
        if losers and len(losers) > len(winners):
            suggestions.append("Review filters around entry timing, liquidity, and market regime before activation.")
        if winners:
            suggestions.append("Compare winning signals by setup type and require backtest validation for any changes.")
        return {
            "summary": f"Reviewed {len(rows)} closed trades: {len(winners)} winners, {len(losers)} losers.",
            "suggestions": suggestions,
            "activation_rule": "No strategy changes are activated until a new version passes backtesting and walk-forward tests.",
        }

