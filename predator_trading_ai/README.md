# Predator Trading AI

Predator Trading AI is a market intelligence, signal, backtesting, paper-trading, and alert system. It is intentionally not a fully autonomous live trading bot.

Live trading is off by default:

```env
LIVE_TRADING=false
```

Even if enabled, live trading requires explicit confirmation through `LIVE_CONFIRMATION_PHRASE`, and the risk engine must approve the setup. The system must not trade when data is missing, liquidity is poor, spreads are too wide, confidence is low, max daily loss is reached, or the market regime is unsafe.

## Setup

```bash
cd predator_trading_ai
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python -m predator_trading_ai.main
pytest predator_trading_ai/tests
```

Set API keys only in `.env`. Do not hardcode secrets.

## Core Flow

1. `data/market_data.py` fetches Alpaca bars and calculates VWAP, ATR, RSI, EMA, MACD, and volume averages.
2. `data/options_data.py` wraps Polygon options access and detects unusual options flow using premium, volume/OI, trade type, liquidity, and spread filters.
3. `ai/sentiment_analyzer.py` scores Reddit/Twitter text for sentiment, hype, fear, pump risk, and unusual mentions.
4. `engines/regime_detector.py` classifies trend day, choppy, high volatility, low volume, news-driven, normal, and no-trade regimes.
5. `engines/strategy_engine.py` creates deterministic setup candidates for breakout, reversal, and momentum continuation.
6. `engines/risk_engine.py` approves or rejects each setup using account, liquidity, spread, confidence, daily loss, open trade, and risk/reward rules.
7. `engines/signal_engine.py` builds the alert format with entry zone, targets, stop, expected backtest win rate, position size, liquidity score, regime, reason, and do-not-enter conditions.
8. `ai/gpt_explainer.py` uses GPT only to explain approved signals from provided data. It must not invent facts or make final trade decisions.
9. `engines/backtester.py` supports historical testing, walk-forward slices, win rate, profit factor, max drawdown, average R, and Sharpe.
10. `engines/paper_trader.py` logs paper trades and keeps live trading gated.
11. `engines/learning_engine.py` reviews winners and losers and suggests improvements. It does not activate changes automatically.
12. `alerts/telegram_bot.py` sends alerts and implements `/status`, `/performance`, `/open_trades`, `/last_signals`, `/disable_live`, and `/enable_paper` command handlers.

## Database

SQLite tables are defined in `database/schema.sql`:

- `signals`
- `trades`
- `backtest_results`
- `options_flow`
- `sentiment_data`
- `market_regime`
- `strategy_versions`
- `performance_metrics`

## Safety Notes

GPT is explanation-only. Strategy decisions are numeric and rule-based.

Sentiment is secondary confirmation only. It should never override price action, regime, options liquidity, or risk controls.

Self-learning output is advisory. Any suggested strategy change must become a new strategy version and pass backtesting plus walk-forward testing before activation.

## Reliability Layer

The scanner persists runtime state in `state/runtime_state.json`:

- last scan time
- active signals
- active positions placeholder
- last Telegram alert
- signal cooldowns
- strategy state
- consecutive failures
- safe mode status

The main loop records heartbeat data, writes health events to SQLite, retries transient API failures, and trips safe mode after repeated failures. Safe mode blocks signal scanning and sends a system alert when Telegram is configured.

## Institutional Watchlist

The default monitoring universe is now a curated 50-name institutional watchlist across Technology, Financials, Healthcare, Consumer, Industrials/Energy, and Real Estate. Metadata lives in `utils/watchlist.py`, including sector mapping and correlation groups used by the risk engine.

The strategy engine is intentionally selective. It favors bull-market continuation setups with EMA50/EMA200 alignment, relative volume confirmation, controlled volatility, non-extended entries, breadth confirmation, and regime alignment. This is designed to reduce trade frequency and reject weak setups rather than maximize alerts.

Risk controls include sector caps and correlation-group caps, so crowded exposure such as multiple AI semiconductor names cannot stack unchecked.

## Live Monitoring

Monitoring mode does not execute trades. It scans during regular US market hours only, from 9:30 AM to 4:00 PM ET, every 5 minutes by default.

The default live-monitoring watchlist is intentionally small:

```env
WATCHLIST=SPY,QQQ
LOOP_INTERVAL_SECONDS=300
LIVE_TRADING=false
```

Start monitoring:

```bash
python -m predator_trading_ai.main
```

Dry run one scheduler pass:

```bash
python -m predator_trading_ai.main --once
```

The scanner uses Alpaca first and falls back to yfinance when broker market data is unavailable. Telegram alerts are sent only when a new signal is not on cooldown.

## Forward Testing And Shadow Mode

Forward testing is monitoring-only. It does not place live or paper orders. It scans the full watchlist, sends Telegram only for accepted high-quality signals, and logs rejected setups for later filter-effectiveness analysis.

Run continuously during market hours:

```bash
python -m predator_trading_ai.run_forward_test
```

Run one scheduler pass:

```bash
python -m predator_trading_ai.run_forward_test --once
```

Build today's summary:

```bash
python -m predator_trading_ai.run_forward_test --summary
```

Suppress Telegram while testing:

```bash
python -m predator_trading_ai.run_forward_test --once --no-telegram
```

Shadow mode records:

- accepted signals
- rejected signals
- rejection stage and reason
- regime state
- score
- volume, trend, volatility, and correlation conditions
- price at decision time
- later target/stop outcome when enough future bars are available

Use this before paper trading to answer the important question: are filters improving quality, or are they rejecting trades that would have worked?

## Backtest Safety

Backtests now include execution realism controls:

- slippage
- bid/ask spread cost
- commission
- partial fill simulation
- long and short direction support
- minimum trade threshold
- overfitting warnings
- Monte Carlo reshuffling
- walk-forward validation
- period validation helpers for the 2020 crash and 2022 bear market
