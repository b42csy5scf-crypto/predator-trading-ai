from predator_trading_ai.state.runtime_state import RuntimeStateStore


def test_runtime_state_persists_cooldowns(tmp_path) -> None:
    store = RuntimeStateStore(tmp_path / "runtime_state.json")
    state = store.load()
    key = store.signal_key("AAPL", "breakout", "long")
    assert store.is_on_cooldown(state, key, 60) is False
    store.set_cooldown(state, key)

    loaded = store.load()
    assert store.is_on_cooldown(loaded, key, 60) is True
