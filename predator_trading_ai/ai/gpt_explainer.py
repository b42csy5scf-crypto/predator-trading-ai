from dataclasses import asdict
from typing import Optional

from predator_trading_ai.config import Settings, get_settings
from predator_trading_ai.engines.signal_engine import TradingSignal
from predator_trading_ai.utils.logger import setup_logger


class GPTExplainer:
    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings = settings or get_settings()
        self.logger = setup_logger(__name__, self.settings.log_level)

    def explain(self, signal: TradingSignal, source_data: dict) -> str:
        fallback = self._fallback(signal)
        if not self.settings.openai_api_key:
            self.logger.info("OpenAI API key missing; using deterministic explanation.")
            return fallback
        try:
            from openai import OpenAI

            client = OpenAI(api_key=self.settings.openai_api_key)
            prompt = (
                "Explain this trading signal using only the JSON provided. "
                "Do not invent prices, news, indicators, probabilities, or facts. "
                "Cover: why valid, invalidation, main risks, confidence reasoning.\n\n"
                f"signal={asdict(signal)}\nsource_data={source_data}"
            )
            response = client.responses.create(
                model=self.settings.openai_model,
                input=prompt,
                temperature=0.2,
            )
            return response.output_text.strip()
        except Exception as exc:
            self.logger.exception("GPT explanation failed; using fallback: %s", exc)
            return fallback

    @staticmethod
    def _fallback(signal: TradingSignal) -> str:
        return (
            f"{signal.setup_type} signal for {signal.ticker} is valid because {signal.reason}. "
            f"Invalidation is a move through {signal.stop_loss:.2f} or any listed do-not-enter condition. "
            f"Main risks are liquidity deterioration, regime change, and failure to hold the entry zone. "
            f"Confidence is {signal.confidence:.0f}% from deterministic strategy and risk filters."
        )

