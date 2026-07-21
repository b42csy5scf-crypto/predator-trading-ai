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

CREATE TABLE IF NOT EXISTS active_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    closed_at TEXT,
    ticker TEXT NOT NULL,
    grade TEXT NOT NULL,
    alert_type TEXT NOT NULL DEFAULT 'trade_candidate',
    direction TEXT NOT NULL DEFAULT 'long',
    entry_zone_low REAL NOT NULL,
    entry_zone_high REAL NOT NULL,
    stop_loss REAL NOT NULL,
    original_stop_loss REAL,
    tp1 REAL NOT NULL,
    tp2 REAL NOT NULL,
    tp3 REAL NOT NULL,
    sent_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    tp1_hit INTEGER NOT NULL DEFAULT 0,
    tp2_hit INTEGER NOT NULL DEFAULT 0,
    tp3_hit INTEGER NOT NULL DEFAULT 0,
    breakeven_active INTEGER NOT NULL DEFAULT 0,
    breakeven_price REAL,
    last_price REAL,
    close_reason TEXT
);

CREATE INDEX IF NOT EXISTS idx_active_signals_ticker_status
ON active_signals(ticker, status);

CREATE TABLE IF NOT EXISTS signal_updates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    active_signal_id INTEGER NOT NULL,
    ticker TEXT NOT NULL,
    update_type TEXT NOT NULL,
    price REAL NOT NULL,
    status TEXT NOT NULL,
    message TEXT NOT NULL,
    UNIQUE(active_signal_id, update_type),
    FOREIGN KEY(active_signal_id) REFERENCES active_signals(id)
);

CREATE TABLE IF NOT EXISTS alert_daily_limits (
    alert_date TEXT NOT NULL,
    ticker TEXT NOT NULL,
    alert_count INTEGER NOT NULL DEFAULT 0,
    highest_grade TEXT,
    highest_grade_rank INTEGER NOT NULL DEFAULT 0,
    last_alert_at TEXT,
    PRIMARY KEY(alert_date, ticker)
);

CREATE TABLE IF NOT EXISTS completed_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    active_signal_id INTEGER UNIQUE,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ticker TEXT NOT NULL,
    grade TEXT NOT NULL,
    alert_type TEXT NOT NULL DEFAULT 'trade_candidate',
    direction TEXT NOT NULL DEFAULT 'long',
    entry_zone_low REAL NOT NULL,
    entry_zone_high REAL NOT NULL,
    entry_price REAL NOT NULL,
    stop_loss REAL NOT NULL,
    tp1 REAL NOT NULL,
    tp2 REAL NOT NULL,
    tp3 REAL NOT NULL,
    outcome TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    opened_at TEXT NOT NULL,
    closed_at TEXT,
    close_price REAL,
    r_multiple REAL NOT NULL DEFAULT 0,
    regime TEXT,
    score REAL,
    stop_loss_reason TEXT
);

CREATE INDEX IF NOT EXISTS idx_completed_trades_ticker_status
ON completed_trades(ticker, status);

CREATE TABLE IF NOT EXISTS signal_diagnostics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    signal_id INTEGER,
    active_signal_id INTEGER,
    ticker TEXT NOT NULL,
    grade TEXT NOT NULL,
    alert_type TEXT NOT NULL DEFAULT 'trade_candidate',
    score REAL NOT NULL,
    entry_zone_low REAL NOT NULL,
    entry_zone_high REAL NOT NULL,
    stop_loss REAL NOT NULL,
    tp1 REAL NOT NULL,
    tp2 REAL NOT NULL,
    tp3 REAL NOT NULL,
    atr REAL,
    stop_distance_pct REAL,
    stop_distance_atr REAL,
    breakout_distance_atr REAL,
    distance_from_ema21_atr REAL,
    distance_from_ema50_atr REAL,
    relative_volume REAL,
    rsi REAL,
    macd_minus_signal REAL,
    spy_trend TEXT,
    qqq_trend TEXT,
    regime TEXT,
    breadth_score REAL,
    sector TEXT,
    telegram_note TEXT,
    git_commit_hash TEXT,
    strategy_version TEXT,
    schema_version TEXT,
    research_dataset_version TEXT,
    config_hash TEXT,
    distance_from_ema21 REAL,
    distance_from_ema50 REAL,
    distance_from_recent_swing_low REAL,
    stop_to_swing_low_distance REAL,
    bars_since_breakout INTEGER,
    entry_open REAL,
    entry_high REAL,
    entry_low REAL,
    entry_close REAL,
    entry_volume REAL,
    previous_open REAL,
    previous_high REAL,
    previous_low REAL,
    previous_close REAL,
    previous_volume REAL,
    spy_state TEXT,
    qqq_state TEXT,
    vix_value REAL,
    spread_at_entry REAL,
    slippage_proxy REAL,
    gap_flag INTEGER,
    minutes_after_market_open REAL,
    day_of_week INTEGER,
    open_positions_count INTEGER,
    open_positions_same_sector INTEGER,
    raw_score REAL,
    setup_grade TEXT,
    eligibility_status TEXT,
    eligibility_stage TEXT,
    block_reason_code TEXT,
    block_reason_display TEXT,
    final_acceptance_status TEXT,
    displayed_grade_legacy TEXT,
    classification_format_version INTEGER NOT NULL DEFAULT 1,
    raw_bid REAL,
    raw_ask REAL,
    bid_size INTEGER,
    ask_size INTEGER,
    last_trade_price REAL,
    midpoint REAL,
    quote_timestamp TEXT,
    evaluation_timestamp TEXT,
    quote_age_seconds REAL,
    quote_source TEXT,
    feed_name TEXT,
    feed_type TEXT,
    nbbo_flag INTEGER,
    feed_native_flag INTEGER,
    spread_absolute REAL,
    spread_percentage REAL,
    spread_formula_version TEXT,
    liquidity_score_at_evaluation REAL,
    liquidity_score_status TEXT,
    raw_volume REAL,
    quote_relative_volume REAL,
    market_session_state TEXT,
    market_status TEXT,
    retry_used INTEGER,
    stale_quote_flag INTEGER,
    missing_bid_flag INTEGER,
    missing_ask_flag INTEGER,
    nonpositive_bid_flag INTEGER,
    nonpositive_ask_flag INTEGER,
    crossed_market_flag INTEGER,
    raw_quote_payload_version TEXT,
    forensics_format_version INTEGER,
    quote_validity_status TEXT,
    quote_validity_reasons TEXT,
    scoring_components_json TEXT NOT NULL,
    raw_metrics_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_signal_diagnostics_created_at
ON signal_diagnostics(created_at);

CREATE TABLE IF NOT EXISTS rejected_candidate_diagnostics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ticker TEXT NOT NULL,
    final_score REAL NOT NULL,
    computed_grade TEXT NOT NULL,
    first_rejection_gate TEXT,
    rejection_reasons_json TEXT NOT NULL,
    conditions_passed_json TEXT NOT NULL,
    conditions_failed_json TEXT NOT NULL,
    diagnostics_format_version INTEGER NOT NULL DEFAULT 1,
    evaluated_conditions_json TEXT NOT NULL DEFAULT '[]',
    passed_conditions_v2_json TEXT NOT NULL DEFAULT '[]',
    failed_conditions_v2_json TEXT NOT NULL DEFAULT '[]',
    blocking_conditions_json TEXT NOT NULL DEFAULT '[]',
    actual_first_blocking_gate TEXT,
    why_not_trade TEXT NOT NULL,
    breakout_distance_atr REAL,
    distance_from_ema21 REAL,
    distance_from_ema50 REAL,
    distance_from_recent_swing_low REAL,
    stop_to_swing_low_distance REAL,
    bars_since_breakout INTEGER,
    entry_open REAL,
    entry_high REAL,
    entry_low REAL,
    entry_close REAL,
    entry_volume REAL,
    previous_open REAL,
    previous_high REAL,
    previous_low REAL,
    previous_close REAL,
    previous_volume REAL,
    gap_flag INTEGER,
    raw_score REAL,
    setup_grade TEXT,
    eligibility_status TEXT,
    eligibility_stage TEXT,
    block_reason_code TEXT,
    block_reason_display TEXT,
    final_acceptance_status TEXT,
    displayed_grade_legacy TEXT,
    classification_format_version INTEGER NOT NULL DEFAULT 1,
    raw_bid REAL,
    raw_ask REAL,
    bid_size INTEGER,
    ask_size INTEGER,
    last_trade_price REAL,
    midpoint REAL,
    quote_timestamp TEXT,
    evaluation_timestamp TEXT,
    quote_age_seconds REAL,
    quote_source TEXT,
    feed_name TEXT,
    feed_type TEXT,
    nbbo_flag INTEGER,
    feed_native_flag INTEGER,
    spread_absolute REAL,
    spread_percentage REAL,
    spread_formula_version TEXT,
    liquidity_score_at_evaluation REAL,
    liquidity_score_status TEXT,
    raw_volume REAL,
    quote_relative_volume REAL,
    market_session_state TEXT,
    market_status TEXT,
    retry_used INTEGER,
    stale_quote_flag INTEGER,
    missing_bid_flag INTEGER,
    missing_ask_flag INTEGER,
    nonpositive_bid_flag INTEGER,
    nonpositive_ask_flag INTEGER,
    crossed_market_flag INTEGER,
    raw_quote_payload_version TEXT,
    forensics_format_version INTEGER,
    quote_validity_status TEXT,
    quote_validity_reasons TEXT,
    raw_metrics_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_rejected_candidate_diagnostics_created_at
ON rejected_candidate_diagnostics(created_at);

CREATE INDEX IF NOT EXISTS idx_rejected_candidate_diagnostics_quote_quality
ON rejected_candidate_diagnostics(ticker, quote_validity_status, spread_percentage);

CREATE INDEX IF NOT EXISTS idx_signal_diagnostics_quote_quality
ON signal_diagnostics(ticker, quote_validity_status, spread_percentage);

CREATE TABLE IF NOT EXISTS signal_outcome_diagnostics (
    active_signal_id INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ticker TEXT NOT NULL,
    grade TEXT NOT NULL,
    alert_type TEXT NOT NULL DEFAULT 'trade_candidate',
    direction TEXT NOT NULL DEFAULT 'long',
    entry_price REAL NOT NULL,
    original_stop_loss REAL NOT NULL,
    risk_per_share REAL NOT NULL,
    max_favorable_price REAL,
    max_adverse_price REAL,
    mfe_r REAL NOT NULL DEFAULT 0,
    mae_r REAL NOT NULL DEFAULT 0,
    current_r REAL NOT NULL DEFAULT 0,
    tp1_hit_at TEXT,
    tp2_hit_at TEXT,
    tp3_hit_at TEXT,
    sl_hit_at TEXT,
    time_to_mfe_seconds REAL,
    time_to_mae_seconds REAL,
    time_to_025r_seconds REAL,
    time_to_050r_seconds REAL,
    time_to_075r_seconds REAL,
    time_to_100r_seconds REAL,
    holding_seconds REAL,
    final_outcome TEXT,
    exit_reason TEXT,
    exit_price REAL,
    exit_timestamp TEXT,
    exit_atr REAL,
    realized_r REAL,
    FOREIGN KEY(active_signal_id) REFERENCES active_signals(id)
);

CREATE INDEX IF NOT EXISTS idx_signal_outcome_diagnostics_updated_at
ON signal_outcome_diagnostics(updated_at);

CREATE TABLE IF NOT EXISTS price_path (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    signal_id INTEGER NOT NULL,
    timestamp TEXT NOT NULL,
    price REAL NOT NULL,
    high REAL,
    low REAL,
    event_type TEXT NOT NULL DEFAULT 'scan',
    FOREIGN KEY(signal_id) REFERENCES active_signals(id)
);

CREATE INDEX IF NOT EXISTS idx_price_path_signal_time
ON price_path(signal_id, timestamp);

CREATE TABLE IF NOT EXISTS universe_snapshot (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    timestamp TEXT NOT NULL,
    symbols_scanned INTEGER NOT NULL,
    symbols_skipped INTEGER NOT NULL,
    api_failures INTEGER NOT NULL,
    missing_market_data INTEGER NOT NULL,
    symbols_successfully_evaluated INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_universe_snapshot_timestamp
ON universe_snapshot(timestamp);

CREATE TABLE IF NOT EXISTS config_snapshots (
    config_hash TEXT PRIMARY KEY,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    config_json TEXT NOT NULL
);
