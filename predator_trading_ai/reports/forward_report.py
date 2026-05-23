from collections import Counter
from datetime import date

from predator_trading_ai.database.db import Database


class ForwardTestReport:
    def __init__(self, db: Database) -> None:
        self.db = db

    def build_daily_summary(self, report_date: str | None = None) -> str:
        day = report_date or date.today().isoformat()
        rows = self.db.fetch_all(
            """
            SELECT status, rejection_reason, outcome
            FROM shadow_signals
            WHERE date(created_at) = date(?)
            """,
            [day],
        )
        accepted = [row for row in rows if row["status"] == "accepted"]
        rejected = [row for row in rows if row["status"] == "rejected"]
        reason_counts = Counter(row["rejection_reason"] or "unknown" for row in rejected)
        accepted_wins = sum(1 for row in accepted if row["outcome"] == "target_hit")
        accepted_losses = sum(1 for row in accepted if row["outcome"] == "stop_hit")
        rejected_would_win = sum(1 for row in rejected if row["outcome"] == "target_hit")
        rejected_resolved = sum(1 for row in rejected if row["outcome"] in {"target_hit", "stop_hit"})

        top_reasons = reason_counts.most_common(5)
        top_reason_text = ", ".join(f"{reason}: {count}" for reason, count in top_reasons) or "none"
        effectiveness = "n/a"
        if rejected_resolved:
            saved = rejected_resolved - rejected_would_win
            effectiveness = f"{saved}/{rejected_resolved} rejected resolved as non-winners"

        self.db.insert_dict(
            "forward_test_results",
            {
                "period": day,
                "accepted_signals": len(accepted),
                "rejected_signals": len(rejected),
                "accepted_wins": accepted_wins,
                "accepted_losses": accepted_losses,
                "rejected_would_have_won": rejected_would_win,
                "top_rejection_reasons": top_reason_text,
                "notes": f"filter effectiveness: {effectiveness}",
            },
        )

        return (
            "Predator Forward Test Summary\n"
            f"Date: {day}\n"
            f"Accepted signals: {len(accepted)}\n"
            f"Rejected signals: {len(rejected)}\n"
            f"Accepted outcomes: {accepted_wins} target hits / {accepted_losses} stops\n"
            f"Rejected would-have-won: {rejected_would_win}\n"
            f"Top rejection reasons: {top_reason_text}\n"
            f"Filter effectiveness: {effectiveness}"
        )
