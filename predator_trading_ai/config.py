import os
from dataclasses import dataclass, fields
from functools import lru_cache
from typing import Optional, get_args, get_origin

from predator_trading_ai.utils.watchlist import DEFAULT_WATCHLIST


def _load_dotenv(path: str = ".env") -> None:
    paths = [path, "predator_trading_ai/.env"]
    for env_path in paths:
        if not os.path.exists(env_path):
            continue
        with open(env_path, encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _coerce(value: str, annotation):
    origin = get_origin(annotation)
    if origin is not None and type(None) in get_args(annotation):
        annotation = next(arg for arg in get_args(annotation) if arg is not type(None))
    if annotation is bool:
        return value.lower() in {"1", "true", "yes", "on"}
    if annotation is int:
        return int(value)
    if annotation is float:
        return float(value)
    return value


try:
    from pydantic import Field, field_validator
    from pydantic_settings import BaseSettings, SettingsConfigDict

    class Settings(BaseSettings):
        model_config = SettingsConfigDict(
            env_file=(".env", "predator_trading_ai/.env"),
            env_file_encoding="utf-8",
            extra="ignore",
        )

        app_env: str = "development"
        database_url: str = "sqlite:///predator_trading_ai.db"
        log_level: str = "INFO"

        live_trading: bool = False
        require_live_confirmation: bool = True
        live_confirmation_phrase: str = "I_UNDERSTAND_THE_RISK"

        alpaca_api_key: Optional[str] = None
        alpaca_secret_key: Optional[str] = None
        alpaca_paper: bool = True
        alpaca_base_url: str = "https://paper-api.alpaca.markets"
        polygon_api_key: Optional[str] = None
        unusual_whales_api_key: Optional[str] = None

        openai_api_key: Optional[str] = None
        openai_model: str = "gpt-4o-mini"

        reddit_client_id: Optional[str] = None
        reddit_client_secret: Optional[str] = None
        reddit_user_agent: str = "predator-trading-ai/0.1"
        twitter_bearer_token: Optional[str] = None

        telegram_bot_token: Optional[str] = None
        telegram_chat_id: Optional[str] = None
        telegram_chat_id_1: Optional[str] = None
        telegram_chat_id_2: Optional[str] = None

        max_risk_per_trade_pct: float = Field(default=1.0, gt=0, le=10)
        max_daily_loss_pct: float = Field(default=3.0, gt=0, le=20)
        max_open_trades: int = Field(default=3, ge=0, le=50)
        min_confidence: int = Field(default=60, ge=0, le=100)
        min_liquidity_score: int = Field(default=60, ge=0, le=100)
        max_spread_pct: float = Field(default=2.0, gt=0, le=25)
        min_risk_reward: float = Field(default=1.5, gt=0)
        watchlist: str = DEFAULT_WATCHLIST
        loop_interval_seconds: int = Field(default=300, ge=30)
        paper_account_equity: float = Field(default=100_000.0, gt=0)
        retry_attempts: int = Field(default=3, ge=1, le=10)
        retry_base_delay_seconds: float = Field(default=1.0, ge=0)
        watchdog_max_failures: int = Field(default=5, ge=1)
        health_alert_failures: int = Field(default=3, ge=1)
        slippage_pct: float = Field(default=0.075, ge=0, le=1)
        spread_cost_pct: float = Field(default=0.05, ge=0, le=1)
        commission_per_trade: float = Field(default=0.0, ge=0)
        partial_fill_probability: float = Field(default=0.05, ge=0, le=1)
        partial_fill_fraction: float = Field(default=0.5, gt=0, le=1)
        signal_cooldown_seconds: int = Field(default=1800, ge=0)
        institutional_min_score: float = Field(default=72.0, ge=0, le=100)
        bull_regime_min_score: float = Field(default=70.0, ge=0, le=100)
        neutral_regime_min_score: float = Field(default=78.0, ge=0, le=100)
        max_sector_positions: int = Field(default=3, ge=1)
        max_correlation_group_positions: int = Field(default=2, ge=1)
        min_score_a_plus_plus: float = Field(default=75.0, ge=0, le=100)
        min_score_a_plus: float = Field(default=65.0, ge=0, le=100)
        min_score_a: float = Field(default=58.0, ge=0, le=100)
        min_score_b: float = Field(default=50.0, ge=0, le=100)
        min_score_c: float = Field(default=40.0, ge=0, le=100)
        min_score_watch: float = Field(default=50.0, ge=0, le=100)
        enable_b_alerts: bool = True
        enable_c_alerts: bool = True
        enable_watchlist_alerts: bool = True
        alert_cooldown_minutes: int = Field(default=60, ge=0)
        graded_alert_cooldown_seconds: int = Field(default=3600, ge=0)

        @field_validator("live_trading")
        @classmethod
        def live_trading_is_explicit(cls, value: bool) -> bool:
            return bool(value)

        @property
        def sqlite_path(self) -> str:
            if self.database_url.startswith("sqlite:///"):
                return self.database_url.replace("sqlite:///", "", 1)
            return self.database_url

except ModuleNotFoundError:

    @dataclass
    class Settings:
        app_env: str = "development"
        database_url: str = "sqlite:///predator_trading_ai.db"
        log_level: str = "INFO"

        live_trading: bool = False
        require_live_confirmation: bool = True
        live_confirmation_phrase: str = "I_UNDERSTAND_THE_RISK"

        alpaca_api_key: Optional[str] = None
        alpaca_secret_key: Optional[str] = None
        alpaca_paper: bool = True
        alpaca_base_url: str = "https://paper-api.alpaca.markets"
        polygon_api_key: Optional[str] = None
        unusual_whales_api_key: Optional[str] = None

        openai_api_key: Optional[str] = None
        openai_model: str = "gpt-4o-mini"

        reddit_client_id: Optional[str] = None
        reddit_client_secret: Optional[str] = None
        reddit_user_agent: str = "predator-trading-ai/0.1"
        twitter_bearer_token: Optional[str] = None

        telegram_bot_token: Optional[str] = None
        telegram_chat_id: Optional[str] = None
        telegram_chat_id_1: Optional[str] = None
        telegram_chat_id_2: Optional[str] = None

        max_risk_per_trade_pct: float = 1.0
        max_daily_loss_pct: float = 3.0
        max_open_trades: int = 3
        min_confidence: int = 60
        min_liquidity_score: int = 60
        max_spread_pct: float = 2.0
        min_risk_reward: float = 1.5
        watchlist: str = DEFAULT_WATCHLIST
        loop_interval_seconds: int = 300
        paper_account_equity: float = 100_000.0
        retry_attempts: int = 3
        retry_base_delay_seconds: float = 1.0
        watchdog_max_failures: int = 5
        health_alert_failures: int = 3
        slippage_pct: float = 0.075
        spread_cost_pct: float = 0.05
        commission_per_trade: float = 0.0
        partial_fill_probability: float = 0.05
        partial_fill_fraction: float = 0.5
        signal_cooldown_seconds: int = 1800
        institutional_min_score: float = 72.0
        bull_regime_min_score: float = 70.0
        neutral_regime_min_score: float = 78.0
        max_sector_positions: int = 3
        max_correlation_group_positions: int = 2
        min_score_a_plus_plus: float = 75.0
        min_score_a_plus: float = 65.0
        min_score_a: float = 58.0
        min_score_b: float = 50.0
        min_score_c: float = 40.0
        min_score_watch: float = 50.0
        enable_b_alerts: bool = True
        enable_c_alerts: bool = True
        enable_watchlist_alerts: bool = True
        alert_cooldown_minutes: int = 60
        graded_alert_cooldown_seconds: int = 3600

        def __post_init__(self) -> None:
            _load_dotenv()
            for field in fields(self):
                env_key = field.name.upper()
                if env_key in os.environ:
                    setattr(self, field.name, _coerce(os.environ[env_key], field.type))
            self._validate()

        @property
        def sqlite_path(self) -> str:
            if self.database_url.startswith("sqlite:///"):
                return self.database_url.replace("sqlite:///", "", 1)
            return self.database_url

        def _validate(self) -> None:
            if not 0 < self.max_risk_per_trade_pct <= 10:
                raise ValueError("max_risk_per_trade_pct must be between 0 and 10")
            if not 0 < self.max_daily_loss_pct <= 20:
                raise ValueError("max_daily_loss_pct must be between 0 and 20")
            if not 0 <= self.max_open_trades <= 50:
                raise ValueError("max_open_trades must be between 0 and 50")
            if not 0 <= self.min_confidence <= 100:
                raise ValueError("min_confidence must be between 0 and 100")
            if not 0 <= self.min_liquidity_score <= 100:
                raise ValueError("min_liquidity_score must be between 0 and 100")
            if not 0 < self.max_spread_pct <= 25:
                raise ValueError("max_spread_pct must be between 0 and 25")
            if self.min_risk_reward <= 0:
                raise ValueError("min_risk_reward must be positive")
            if self.loop_interval_seconds < 30:
                raise ValueError("loop_interval_seconds must be at least 30")
            if self.paper_account_equity <= 0:
                raise ValueError("paper_account_equity must be positive")
            if self.retry_attempts < 1:
                raise ValueError("retry_attempts must be at least 1")
            if self.watchdog_max_failures < 1:
                raise ValueError("watchdog_max_failures must be at least 1")
            if not 0 <= self.slippage_pct <= 1:
                raise ValueError("slippage_pct must be between 0 and 1")
            if not 0 <= self.spread_cost_pct <= 1:
                raise ValueError("spread_cost_pct must be between 0 and 1")
            if not 0 <= self.partial_fill_probability <= 1:
                raise ValueError("partial_fill_probability must be between 0 and 1")
            if not 0 < self.partial_fill_fraction <= 1:
                raise ValueError("partial_fill_fraction must be between 0 and 1")


@lru_cache
def get_settings() -> Settings:
    return Settings()
