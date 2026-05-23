import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


@dataclass
class RuntimeState:
    last_scan_time: Optional[str] = None
    active_signals: dict[str, dict[str, Any]] = field(default_factory=dict)
    active_positions: dict[str, dict[str, Any]] = field(default_factory=dict)
    last_telegram_alert: Optional[str] = None
    cooldowns: dict[str, str] = field(default_factory=dict)
    strategy_states: dict[str, dict[str, Any]] = field(default_factory=dict)
    consecutive_failures: int = 0
    safe_mode: bool = False
    safe_mode_reason: Optional[str] = None


class RuntimeStateStore:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else Path(__file__).resolve().parent / "runtime_state.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> RuntimeState:
        if not self.path.exists():
            return RuntimeState()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return RuntimeState(**data)
        except (json.JSONDecodeError, TypeError):
            backup = self.path.with_suffix(".corrupt.json")
            self.path.replace(backup)
            return RuntimeState(safe_mode=True, safe_mode_reason=f"Runtime state was corrupt; backed up to {backup.name}")

    def save(self, state: RuntimeState) -> None:
        tmp_path = self.path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(asdict(state), indent=2, sort_keys=True), encoding="utf-8")
        tmp_path.replace(self.path)

    def mark_scan(self, state: RuntimeState) -> None:
        state.last_scan_time = datetime.now(timezone.utc).isoformat()
        self.save(state)

    def signal_key(self, ticker: str, setup_type: str, direction: str) -> str:
        return f"{ticker}:{setup_type}:{direction}"

    def is_on_cooldown(self, state: RuntimeState, key: str, cooldown_seconds: int) -> bool:
        last = state.cooldowns.get(key)
        if not last:
            return False
        try:
            last_dt = datetime.fromisoformat(last)
        except ValueError:
            return False
        return (datetime.now(timezone.utc) - last_dt).total_seconds() < cooldown_seconds

    def set_cooldown(self, state: RuntimeState, key: str) -> None:
        state.cooldowns[key] = datetime.now(timezone.utc).isoformat()
        self.save(state)
