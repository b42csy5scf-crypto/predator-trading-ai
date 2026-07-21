from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from predator_trading_ai.config import Settings, get_settings
from predator_trading_ai.database.db import Database
from predator_trading_ai.engines.alert_policy import MIN_B_ALERT_SCORE_FLOOR
from predator_trading_ai.engines.signal_diagnostics import SignalDiagnosticsRecorder
from predator_trading_ai.utils.validators import spread_pct


SPREAD_FORMULA = "((ask - bid) / ((bid + ask) / 2)) * 100"


@dataclass(frozen=True)
class AuditThresholds:
    min_score_b_effective: float
    min_score_a: float
    min_score_a_plus: float
    min_score_a_plus_plus: float


class ProductionAuditReport:
    """Read-only production transparency reports for grade and spread diagnostics."""

    def __init__(self, settings: Optional[Settings] = None, db: Optional[Database] = None) -> None:
        self.settings = settings or get_settings()
        self.db = db or Database(self.settings)

    def grade_trace(self, limit: int = 10) -> str:
        safe_limit = max(1, min(int(limit or 10), 25))
        rows = self.db.fetch_all(
            """
            SELECT created_at, ticker, final_score, computed_grade, actual_first_blocking_gate,
                   first_rejection_gate, why_not_trade, rejection_reasons_json,
                   blocking_conditions_json, raw_metrics_json, diagnostics_format_version
            FROM rejected_candidate_diagnostics
            WHERE final_score >= 65
              AND diagnostics_format_version = 2
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            [safe_limit],
        )
        if not rows:
            return "Grade Trace\nNo verified diagnostics v2 rows with score >= 65 are available yet."

        thresholds = self.thresholds()
        lines = [
            f"Grade Trace (latest {len(rows)})",
            "No separate market-adjusted grade exists; displayed grade comes from rejected_candidate_diagnostics.computed_grade.",
            f"Thresholds: B>={thresholds.min_score_b_effective:.0f} A>={thresholds.min_score_a:.0f} "
            f"A+>={thresholds.min_score_a_plus:.0f} A++>={thresholds.min_score_a_plus_plus:.0f}",
        ]
        for row in rows:
            score = optional_float(row_get(row, "final_score"))
            displayed = str(row_get(row, "computed_grade") or "n/a")
            score_grade = self.score_based_grade(score)
            blocking = decode_json_list(row_get(row, "blocking_conditions_json"))
            raw = decode_json_dict(row_get(row, "raw_metrics_json"))
            gate = display_gate(row_get(row, "actual_first_blocking_gate") or row_get(row, "first_rejection_gate"), blocking)
            reasons = self.rejection_reasons(row)
            status = self.final_acceptance_status(displayed, reasons)
            flag = self.consistency_flag(score_grade, displayed, reasons, gate)
            policy_grade = self.policy_eligible_grade(score_grade, displayed, reasons, gate)
            market_health = self.market_health(raw)
            lines.extend(
                [
                    "",
                    f"{row_get(row, 'ticker')} | {score_label(score)} | {flag}",
                    f"Time: {row_get(row, 'created_at')}",
                    f"Score grade: {score_grade}",
                    "Market-adjusted grade: n/a",
                    f"Policy eligible grade/status: {policy_grade}",
                    f"Displayed/report grade: {displayed}",
                    "Alert type: n/a / rejected",
                    f"Acceptance: {status}",
                    f"SPY={raw.get('spy_trend') or raw.get('spy_state') or 'n/a'} "
                    f"QQQ={raw.get('qqq_trend') or raw.get('qqq_state') or 'n/a'} "
                    f"Regime={raw.get('regime') or 'n/a'}",
                    f"Market health: {market_health}",
                    f"Downgrade trigger: {self.downgrade_trigger(score_grade, displayed, reasons, gate)}",
                    f"First block: {gate}",
                    f"Risk decision: {risk_decision(reasons)}",
                    f"Alert policy: {alert_policy_decision(reasons, gate)}",
                    "Telegram reached: NO",
                    "Tracker add reached: NO",
                ]
            )
        return "\n".join(lines).strip()

    def spread_forensics(self, ticker: str, limit: int = 5) -> str:
        symbol = (ticker or "").strip().upper()
        if not symbol:
            return "Spread Forensics\nUsage: /spread_forensics TICKER [count]"
        safe_limit = max(1, min(int(limit or 5), 20))
        rows = self.forensics_rows(symbol, safe_limit)
        if not rows:
            return f"Spread Forensics: {symbol}\nNo diagnostics rows found for this ticker."

        lines = [
            f"Spread Forensics: {symbol} (latest {len(rows)})",
            f"Formula: {SPREAD_FORMULA}",
            "Historical rows without bid/ask are shown as unavailable; no values are guessed.",
        ]
        for row in rows:
            raw = decode_json_dict(row_get(row, "raw_metrics_json"))
            reasons = self.rejection_reasons(row)
            bid = first_float(raw, "bid", "raw_bid", "quote_bid", "bid_price")
            ask = first_float(raw, "ask", "raw_ask", "quote_ask", "ask_price")
            last_price = first_float(raw, "last_price", "price", "trade_price", "close")
            midpoint = ((bid + ask) / 2) if bid is not None and ask is not None and bid > 0 and ask > 0 else None
            calc_spread = spread_pct(bid, ask)
            parsed_spread = parsed_spread_pct(reasons)
            spread_display = calc_spread if calc_spread != float("inf") else parsed_spread
            quote_ts = first_value(raw, "quote_timestamp", "quote_time", "quote_at")
            evaluation_ts = row_get(row, "created_at")
            quote_age = quote_age_seconds(quote_ts, evaluation_ts)
            lines.extend(
                [
                    "",
                    f"{row_get(row, 'source')} | {row_get(row, 'created_at')}",
                    f"Score/grade: {score_label(optional_float(row_get(row, 'score')))} | {row_get(row, 'grade') or 'n/a'}",
                    f"Market: {raw.get('market_status') or raw.get('market_session_state') or raw.get('regime') or 'n/a'}",
                    f"Bid/Ask/Last: {fmt(bid)} / {fmt(ask)} / {fmt(last_price)}",
                    f"Midpoint: {fmt(midpoint)} Spread: {fmt_abs_spread(bid, ask)} ({fmt(spread_display)}%)",
                    f"Liquidity: {fmt(first_float(raw, 'liquidity_score'))} Volume: {fmt(first_float(raw, 'volume', 'entry_volume'))} "
                    f"RelVol: {fmt(first_float(raw, 'relative_volume'))}",
                    f"Quote ts/age: {quote_ts or 'n/a'} / {seconds_label(quote_age)}",
                    f"Source/feed: {raw.get('data_source') or raw.get('source') or 'n/a'} / {raw.get('data_feed') or raw.get('feed') or 'n/a'}",
                    "Quote flags: "
                    f"stale={stale_label(raw, quote_age)} missing_bid={bid is None} missing_ask={ask is None} "
                    f"bid<=0={bid is not None and bid <= 0} ask<=0={ask is not None and ask <= 0} "
                    f"ask<bid={bid is not None and ask is not None and ask < bid}",
                    f"Risk decision: {risk_decision(reasons)}",
                    f"Risk reasons: {short_reasons(reasons)}",
                    legacy_forensics_label(bid, ask, quote_ts),
                ]
            )
        return "\n".join(lines).strip()

    def forensics_rows(self, ticker: str, limit: int) -> list[dict[str, Any]]:
        rejected = self.db.fetch_all(
            """
            SELECT created_at, ticker, final_score AS score, computed_grade AS grade,
                   why_not_trade, rejection_reasons_json, raw_metrics_json,
                   'rejected_candidate' AS source
            FROM rejected_candidate_diagnostics
            WHERE UPPER(ticker) = UPPER(?)
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            [ticker, limit],
        )
        accepted = self.db.fetch_all(
            """
            SELECT created_at, ticker, score, grade, telegram_note AS why_not_trade,
                   '[]' AS rejection_reasons_json, raw_metrics_json,
                   spread_at_entry, relative_volume, entry_volume,
                   'accepted_signal' AS source
            FROM signal_diagnostics
            WHERE UPPER(ticker) = UPPER(?)
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            [ticker, limit],
        )
        rows: list[dict[str, Any]] = []
        for row in [*rejected, *accepted]:
            payload = dict(row)
            raw = decode_json_dict(payload.get("raw_metrics_json"))
            for key in ("spread_at_entry", "relative_volume", "entry_volume"):
                if payload.get(key) is not None:
                    raw.setdefault(key, payload.get(key))
            payload["raw_metrics_json"] = raw
            rows.append(payload)
        rows.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
        return rows[:limit]

    def thresholds(self) -> AuditThresholds:
        return AuditThresholds(
            min_score_b_effective=max(float(self.settings.min_score_b), MIN_B_ALERT_SCORE_FLOOR),
            min_score_a=float(self.settings.min_score_a),
            min_score_a_plus=float(self.settings.min_score_a_plus),
            min_score_a_plus_plus=float(self.settings.min_score_a_plus_plus),
        )

    def score_based_grade(self, score: Optional[float]) -> str:
        if score is None:
            return "n/a"
        thresholds = self.thresholds()
        if score >= thresholds.min_score_a_plus_plus:
            return "A++ Signal"
        if score >= thresholds.min_score_a_plus:
            return "A+ Signal"
        if score >= thresholds.min_score_a:
            return "A Signal"
        if score >= thresholds.min_score_b_effective:
            return "B Watch Alert"
        return "C Risky/Early Alert"

    def rejection_reasons(self, row: Any) -> list[str]:
        reasons = decode_json_list(row_get(row, "rejection_reasons_json"))
        why = row_get(row, "why_not_trade")
        if why:
            reasons.extend(part.strip() for part in str(why).split(";") if part.strip())
        return list(dict.fromkeys(str(item) for item in reasons if item))

    @staticmethod
    def market_health(raw: dict[str, Any]) -> str:
        spy = raw.get("spy_trend") or raw.get("spy_state")
        qqq = raw.get("qqq_trend") or raw.get("qqq_state")
        if spy == "bull" or qqq == "bull":
            return "healthy"
        if spy or qqq:
            return "not healthy"
        return "n/a"

    @staticmethod
    def final_acceptance_status(displayed: str, reasons: list[str]) -> str:
        if displayed in {"A++ Signal", "A+ Signal", "A Signal"} and not reasons:
            return "Accepted A/A+/A++"
        if displayed == "B Watch Alert" and not reasons:
            return "Strong B Experimental"
        return "rejected"

    @staticmethod
    def policy_eligible_grade(score_grade: str, displayed: str, reasons: list[str], gate: str) -> str:
        if risk_decision(reasons) == "REJECTED":
            return "rejected by risk engine"
        if is_policy_or_market_gate(reasons, gate):
            return displayed if displayed == "B Watch Alert" else "rejected by alert/market policy"
        return displayed or score_grade

    @staticmethod
    def consistency_flag(score_grade: str, displayed: str, reasons: list[str], gate: str) -> str:
        if score_grade == displayed:
            return "SCORE_GRADE_MATCH"
        if is_policy_or_market_gate(reasons, gate):
            return "INTENTIONAL_POLICY_DOWNGRADE"
        if displayed and displayed != "n/a":
            return "REPORTING_STAGE_MISMATCH"
        return "UNKNOWN"

    @staticmethod
    def downgrade_trigger(score_grade: str, displayed: str, reasons: list[str], gate: str) -> str:
        if score_grade == displayed:
            return "none"
        if is_policy_or_market_gate(reasons, gate):
            return gate or short_reasons(reasons)
        return "n/a / no explicit downgrade stage stored"


def decode_json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if not value:
        return []
    try:
        decoded = json.loads(str(value))
        return decoded if isinstance(decoded, list) else [decoded]
    except (TypeError, json.JSONDecodeError):
        return [str(value)]


def decode_json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        decoded = json.loads(str(value))
        return decoded if isinstance(decoded, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}


def row_get(row: Any, key: str, default: Any = None) -> Any:
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        return row[key]
    except Exception:
        return default


def display_gate(gate: Any, blocking_conditions: list[Any]) -> str:
    for condition in blocking_conditions:
        if isinstance(condition, dict) and condition.get("condition_key") == gate:
            return SignalDiagnosticsRecorder.failure_display(condition)
    return str(gate or "unknown")


def is_policy_or_market_gate(reasons: list[str], gate: str) -> bool:
    text = " ".join([gate or "", *reasons]).lower()
    markers = [
        "spy/qqq",
        "market",
        "regime",
        "grade below",
        "b needs",
        "strong-watch",
        "alert policy",
        "cooldown",
        "maximum",
    ]
    return any(marker in text for marker in markers)


def risk_decision(reasons: list[str]) -> str:
    text = " ".join(reasons).lower()
    if any(marker in text for marker in ("spread too wide", "liquidity score too low", "risk/reward", "risk engine", "max open", "daily loss")):
        return "REJECTED"
    if reasons:
        return "not reached or not stored"
    return "approved/not rejected"


def alert_policy_decision(reasons: list[str], gate: str) -> str:
    if is_policy_or_market_gate(reasons, gate):
        return f"blocked: {gate or short_reasons(reasons)}"
    if reasons:
        return "not reached or not stored"
    return "passed/not rejected"


def first_value(raw: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if raw.get(key) not in (None, ""):
            return raw.get(key)
    return None


def first_float(raw: dict[str, Any], *keys: str) -> Optional[float]:
    value = first_value(raw, *keys)
    try:
        return float(value) if value is not None and value != "" else None
    except (TypeError, ValueError):
        return None


def optional_float(value: Any) -> Optional[float]:
    try:
        return float(value) if value is not None and value != "" else None
    except (TypeError, ValueError):
        return None


def parsed_spread_pct(reasons: list[str]) -> Optional[float]:
    text = " ".join(reasons)
    match = re.search(r"spread too wide:\s*([0-9]+(?:\.[0-9]+)?)%", text, flags=re.IGNORECASE)
    return float(match.group(1)) if match else None


def quote_age_seconds(quote_timestamp: Any, evaluation_timestamp: Any) -> Optional[float]:
    quote_dt = parse_dt(quote_timestamp)
    eval_dt = parse_dt(evaluation_timestamp)
    if quote_dt is None or eval_dt is None:
        return None
    return max((eval_dt - quote_dt).total_seconds(), 0.0)


def parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def stale_label(raw: dict[str, Any], quote_age: Optional[float]) -> str:
    if raw.get("quote_stale") is not None:
        return str(bool(raw.get("quote_stale")))
    if quote_age is None:
        return "n/a"
    return str(quote_age > 900)


def legacy_forensics_label(bid: Optional[float], ask: Optional[float], quote_timestamp: Any) -> str:
    if bid is None or ask is None or not quote_timestamp:
        return "Historical row — raw quote forensics unavailable."
    return "Raw quote forensics available."


def fmt(value: Optional[float]) -> str:
    if value is None:
        return "n/a"
    return f"{float(value):.2f}"


def fmt_abs_spread(bid: Optional[float], ask: Optional[float]) -> str:
    if bid is None or ask is None:
        return "n/a"
    return fmt(ask - bid)


def seconds_label(value: Optional[float]) -> str:
    return "n/a" if value is None else f"{value:.0f}s"


def score_label(value: Optional[float]) -> str:
    return "score n/a" if value is None else f"score {value:.1f}"


def short_reasons(reasons: list[str], limit: int = 2) -> str:
    if not reasons:
        return "none"
    text = "; ".join(reasons[:limit])
    return text if len(text) <= 160 else f"{text[:157]}..."
