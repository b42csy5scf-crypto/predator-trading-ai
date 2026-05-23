from dataclasses import dataclass, field
from typing import Callable, Literal

import numpy as np
import pandas as pd


Direction = Literal["long", "short"]


@dataclass(frozen=True)
class BacktestResult:
    strategy_name: str
    strategy_version: str
    trades: int
    win_rate: float
    profit_factor: float
    max_drawdown: float
    avg_r_multiple: float
    sharpe_ratio: float | None
    reject_strategy: bool = False
    warnings: list[str] = field(default_factory=list)
    monte_carlo: dict[str, float] | None = None


@dataclass(frozen=True)
class ExecutionModel:
    slippage_pct: float = 0.075
    spread_cost_pct: float = 0.05
    commission_per_trade: float = 0.0
    partial_fill_probability: float = 0.05
    partial_fill_fraction: float = 0.5
    seed: int = 7


class Backtester:
    def run_simple_long(
        self,
        bars: pd.DataFrame,
        signal_fn: Callable[[pd.DataFrame, int], bool],
        strategy_name: str = "rule_strategy",
        strategy_version: str = "0.1.0",
        hold_bars: int = 5,
        stop_atr: float = 1.0,
        target_atr: float = 1.5,
        execution: ExecutionModel | None = None,
        min_trades: int = 50,
    ) -> BacktestResult:
        return self.run_directional(
            bars=bars,
            signal_fn=signal_fn,
            direction="long",
            strategy_name=strategy_name,
            strategy_version=strategy_version,
            hold_bars=hold_bars,
            stop_atr=stop_atr,
            target_atr=target_atr,
            execution=execution,
            min_trades=min_trades,
        )

    def run_simple_short(
        self,
        bars: pd.DataFrame,
        signal_fn: Callable[[pd.DataFrame, int], bool],
        strategy_name: str = "short_rule_strategy",
        strategy_version: str = "0.1.0",
        hold_bars: int = 5,
        stop_atr: float = 1.0,
        target_atr: float = 1.5,
        execution: ExecutionModel | None = None,
        min_trades: int = 50,
    ) -> BacktestResult:
        return self.run_directional(
            bars=bars,
            signal_fn=signal_fn,
            direction="short",
            strategy_name=strategy_name,
            strategy_version=strategy_version,
            hold_bars=hold_bars,
            stop_atr=stop_atr,
            target_atr=target_atr,
            execution=execution,
            min_trades=min_trades,
        )

    def run_directional(
        self,
        bars: pd.DataFrame,
        signal_fn: Callable[[pd.DataFrame, int], bool],
        direction: Direction,
        strategy_name: str,
        strategy_version: str,
        hold_bars: int = 5,
        stop_atr: float = 1.0,
        target_atr: float = 1.5,
        execution: ExecutionModel | None = None,
        min_trades: int = 50,
    ) -> BacktestResult:
        if bars.empty or len(bars) < hold_bars + 30:
            return BacktestResult(strategy_name, strategy_version, 0, 0, 0, 0, 0, None, True, ["insufficient bars"])

        execution = execution or ExecutionModel()
        rng = np.random.default_rng(execution.seed)
        r_values: list[float] = []
        equity_curve = [0.0]

        for idx in range(30, len(bars) - hold_bars):
            history = bars.iloc[: idx + 1]
            if not signal_fn(history, idx):
                continue

            raw_entry = float(bars.iloc[idx]["close"])
            atr = float(bars.iloc[idx].get("atr_14", raw_entry * 0.02))
            risk = max(atr * stop_atr, raw_entry * 0.005)
            entry = self._apply_entry_cost(raw_entry, direction, execution)
            stop = entry - risk if direction == "long" else entry + risk
            target = entry + (atr * target_atr) if direction == "long" else entry - (atr * target_atr)
            future = bars.iloc[idx + 1 : idx + hold_bars + 1]
            result = self._simulate_trade(future, entry, stop, target, risk, direction, execution)

            if rng.random() < execution.partial_fill_probability:
                result *= execution.partial_fill_fraction

            r_values.append(result)
            equity_curve.append(equity_curve[-1] + result)

        return self._metrics(strategy_name, strategy_version, r_values, equity_curve, min_trades=min_trades)

    def walk_forward(
        self,
        bars: pd.DataFrame,
        signal_fn: Callable[[pd.DataFrame, int], bool],
        folds: int = 4,
        direction: Direction = "long",
        min_trades: int = 50,
    ) -> list[BacktestResult]:
        if folds < 2:
            raise ValueError("folds must be at least 2")
        if len(bars) < folds * 40:
            raise ValueError("not enough bars for requested walk-forward folds")
        results: list[BacktestResult] = []
        fold_size = len(bars) // folds
        for fold in range(1, folds):
            test = bars.iloc[fold * fold_size : (fold + 1) * fold_size]
            results.append(
                self.run_directional(
                    test,
                    signal_fn,
                    direction=direction,
                    strategy_name="walk_forward",
                    strategy_version=f"wf-{fold}",
                    min_trades=min_trades,
                )
            )
        return results

    def validate_extended_periods(
        self,
        bars: pd.DataFrame,
        signal_fn: Callable[[pd.DataFrame, int], bool],
        date_column: str = "timestamp",
        direction: Direction = "long",
    ) -> dict[str, BacktestResult]:
        if date_column not in bars:
            raise ValueError(f"{date_column} column is required for extended period validation")
        df = bars.copy()
        df[date_column] = pd.to_datetime(df[date_column], utc=True)
        periods = {
            "2020_crash": ("2020-02-01", "2020-04-30"),
            "2022_bear_market": ("2022-01-01", "2022-12-31"),
            "three_to_five_years": (str(df[date_column].dt.date.min()), str(df[date_column].dt.date.max())),
        }
        results = {}
        for name, (start, end) in periods.items():
            sample = df[(df[date_column] >= start) & (df[date_column] <= end)]
            results[name] = self.run_directional(sample, signal_fn, direction, name, "validation")
        return results

    @staticmethod
    def _apply_entry_cost(price: float, direction: Direction, execution: ExecutionModel) -> float:
        adverse_pct = (execution.slippage_pct + execution.spread_cost_pct) / 100
        return price * (1 + adverse_pct) if direction == "long" else price * (1 - adverse_pct)

    @staticmethod
    def _apply_exit_cost(price: float, direction: Direction, execution: ExecutionModel) -> float:
        adverse_pct = (execution.slippage_pct + execution.spread_cost_pct) / 100
        return price * (1 - adverse_pct) if direction == "long" else price * (1 + adverse_pct)

    def _simulate_trade(
        self,
        future: pd.DataFrame,
        entry: float,
        stop: float,
        target: float,
        risk: float,
        direction: Direction,
        execution: ExecutionModel,
    ) -> float:
        result = 0.0
        for _, row in future.iterrows():
            if direction == "long":
                if float(row["low"]) <= stop:
                    result = (self._apply_exit_cost(stop, direction, execution) - entry) / risk
                    break
                if float(row["high"]) >= target:
                    result = (self._apply_exit_cost(target, direction, execution) - entry) / risk
                    break
            else:
                if float(row["high"]) >= stop:
                    result = (entry - self._apply_exit_cost(stop, direction, execution)) / risk
                    break
                if float(row["low"]) <= target:
                    result = (entry - self._apply_exit_cost(target, direction, execution)) / risk
                    break
        if result == 0.0:
            exit_price = float(future.iloc[-1]["close"])
            adjusted_exit = self._apply_exit_cost(exit_price, direction, execution)
            result = (adjusted_exit - entry) / risk if direction == "long" else (entry - adjusted_exit) / risk
        commission_r = execution.commission_per_trade / risk if risk > 0 else 0
        return result - commission_r

    @staticmethod
    def monte_carlo(r_values: list[float], simulations: int = 500, seed: int = 11) -> dict[str, float] | None:
        if not r_values:
            return None
        rng = np.random.default_rng(seed)
        terminal = []
        max_drawdowns = []
        values = np.array(r_values)
        for _ in range(simulations):
            shuffled = rng.permutation(values)
            equity = np.cumsum(shuffled)
            peaks = np.maximum.accumulate(equity)
            terminal.append(float(equity[-1]))
            max_drawdowns.append(float((peaks - equity).max()))
        return {
            "terminal_r_p05": round(float(np.percentile(terminal, 5)), 3),
            "terminal_r_median": round(float(np.percentile(terminal, 50)), 3),
            "max_drawdown_p95": round(float(np.percentile(max_drawdowns, 95)), 3),
        }

    @classmethod
    def _metrics(
        cls,
        strategy_name: str,
        strategy_version: str,
        r_values: list[float],
        equity_curve: list[float],
        min_trades: int,
    ) -> BacktestResult:
        if not r_values:
            return BacktestResult(strategy_name, strategy_version, 0, 0, 0, 0, 0, None, True, ["no trades"])
        wins = [value for value in r_values if value > 0]
        losses = [abs(value) for value in r_values if value < 0]
        gross_profit = sum(wins)
        gross_loss = sum(losses)
        profit_factor = gross_profit / gross_loss if gross_loss else float("inf")
        equity = np.array(equity_curve)
        peaks = np.maximum.accumulate(equity)
        drawdown = peaks - equity
        sharpe = None
        if len(r_values) > 1 and np.std(r_values) > 0:
            sharpe = float(np.mean(r_values) / np.std(r_values) * np.sqrt(252))

        warnings = []
        if len(r_values) < min_trades:
            warnings.append(f"statistically weak sample: {len(r_values)} trades below minimum {min_trades}")
        if len(r_values) < 10 and len(wins) == len(r_values):
            warnings.append("perfect win rate on tiny sample is likely overfit")
        reject_strategy = len(r_values) < min_trades

        return BacktestResult(
            strategy_name=strategy_name,
            strategy_version=strategy_version,
            trades=len(r_values),
            win_rate=round(len(wins) / len(r_values) * 100, 2),
            profit_factor=round(profit_factor, 3) if np.isfinite(profit_factor) else profit_factor,
            max_drawdown=round(float(drawdown.max()), 3),
            avg_r_multiple=round(float(np.mean(r_values)), 3),
            sharpe_ratio=round(sharpe, 3) if sharpe is not None else None,
            reject_strategy=reject_strategy,
            warnings=warnings,
            monte_carlo=cls.monte_carlo(r_values),
        )
