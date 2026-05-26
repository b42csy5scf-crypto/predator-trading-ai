PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ticker TEXT NOT NULL,
    direction TEXT NOT NULL,
    setup_type TEXT NOT NULL,
    entry_zone_low REAL NOT NULL,
    entry_zone_high REAL NOT NULL,
    target_1 REAL NOT NULL,
    target_2 REAL NOT NULL,
    target_3 REAL NOT NULL,
    stop_loss REAL NOT NULL,
    risk_reward REAL NOT NULL,
    confidence REAL NOT NULL,
    expected_win_rate REAL,
    position_size REAL NOT NULL,
    liquidity_score REAL NOT NULL,
    market_regime TEXT NOT NULL,
    reason TEXT NOT NULL,
    do_not_enter_conditions TEXT NOT NULL,
    gpt_explanation TEXT,
    status TEXT NOT NULL DEFAULT 'new'
);

CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ticker TEXT NOT NULL,
    direction TEXT NOT NULL,
    entry_price REAL NOT NULL,
    exit_price REAL,
    stop_loss REAL NOT NULL,
    target_price REAL,
    quantity REAL NOT NULL,
    status TEXT NOT NULL,
    result_r REAL,
    pnl REAL,
    predicted_win_rate REAL,
    actual_result TEXT,
    FOREIGN KEY(signal_id) REFERENCES signals(id)
);

CREATE TABLE IF NOT EXISTS backtest_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    strategy_name TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    ticker TEXT NOT NULL,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    trades INTEGER NOT NULL,
    win_rate REAL NOT NULL,
    profit_factor REAL NOT NULL,
    max_drawdown REAL NOT NULL,
    avg_r_multiple REAL NOT NULL,
    sharpe_ratio REAL,
    params_json TEXT NOT NULL,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS options_flow (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ticker TEXT NOT NULL,
    contract_symbol TEXT,
    option_type TEXT NOT NULL,
    strike REAL,
    expiry TEXT,
    volume INTEGER NOT NULL,
    open_interest INTEGER,
    premium REAL NOT NULL,
    trade_type TEXT NOT NULL,
    call_put_ratio REAL,
    liquidity_score REAL NOT NULL,
    is_unusual INTEGER NOT NULL,
    reason TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sentiment_data (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ticker TEXT NOT NULL,
    source TEXT NOT NULL,
    mentions INTEGER NOT NULL,
    sentiment_score REAL NOT NULL,
    hype_score REAL NOT NULL,
    fear_score REAL NOT NULL,
    pump_risk REAL NOT NULL,
    summary TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS market_regime (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ticker TEXT,
    regime TEXT NOT NULL,
    volatility REAL NOT NULL,
    volume_state TEXT NOT NULL,
    trend_strength REAL NOT NULL,
    is_safe INTEGER NOT NULL,
    reason TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS strategy_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    name TEXT NOT NULL,
    version TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 0,
    config_json TEXT NOT NULL,
    backtest_passed INTEGER NOT NULL DEFAULT 0,
    notes TEXT,
    UNIQUE(name, version)
);

CREATE TABLE IF NOT EXISTS performance_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    period TEXT NOT NULL,
    trades INTEGER NOT NULL,
    win_rate REAL NOT NULL,
    profit_factor REAL NOT NULL,
    max_drawdown REAL NOT NULL,
    avg_r_multiple REAL NOT NULL,
    predicted_vs_actual_delta REAL,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS system_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS health_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    component TEXT NOT NULL,
    status TEXT NOT NULL,
    message TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS shadow_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ticker TEXT NOT NULL,
    status TEXT NOT NULL,
    direction TEXT,
    setup_type TEXT,
    rejection_stage TEXT,
    rejection_reason TEXT,
    regime TEXT NOT NULL,
    regime_reason TEXT NOT NULL,
    score REAL,
    price REAL,
    entry_price REAL,
    target_price REAL,
    stop_loss REAL,
    volume_condition TEXT NOT NULL,
    trend_condition TEXT NOT NULL,
    volatility_condition TEXT NOT NULL,
    correlation_condition TEXT NOT NULL,
    liquidity_score REAL,
    risk_reward REAL,
    outcome TEXT NOT NULL DEFAULT 'pending',
    outcome_checked_at TEXT,
    outcome_r REAL
);

CREATE TABLE IF NOT EXISTS rejected_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    shadow_signal_id INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ticker TEXT NOT NULL,
    rejection_stage TEXT NOT NULL,
    rejection_reason TEXT NOT NULL,
    regime TEXT NOT NULL,
    score REAL,
    price REAL,
    would_have_won INTEGER,
    outcome TEXT NOT NULL DEFAULT 'pending',
    outcome_checked_at TEXT,
    FOREIGN KEY(shadow_signal_id) REFERENCES shadow_signals(id)
);

CREATE TABLE IF NOT EXISTS forward_test_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    period TEXT NOT NULL,
    accepted_signals INTEGER NOT NULL,
    rejected_signals INTEGER NOT NULL,
    accepted_wins INTEGER NOT NULL DEFAULT 0,
    accepted_losses INTEGER NOT NULL DEFAULT 0,
    rejected_would_have_won INTEGER NOT NULL DEFAULT 0,
    top_rejection_reasons TEXT NOT NULL,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS sent_alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ticker TEXT NOT NULL,
    grade TEXT NOT NULL,
    alert_type TEXT NOT NULL,
    score REAL,
    setup_type TEXT,
    regime TEXT,
    message TEXT NOT NULL
);
