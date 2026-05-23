import re
from collections import Counter

from predator_trading_ai.data.sentiment_data import SentimentRecord
from predator_trading_ai.utils.validators import clamp


class SentimentAnalyzer:
    positive_words = {"breakout", "moon", "bullish", "beat", "calls", "strong", "buy", "upside"}
    negative_words = {"crash", "bearish", "miss", "puts", "weak", "sell", "fraud", "downside"}
    hype_words = {"moon", "squeeze", "yolo", "rocket", "gamma", "shorts"}
    fear_words = {"crash", "panic", "halt", "rug", "lawsuit", "bankruptcy"}

    def analyze(self, ticker: str, source: str, texts: list[str]) -> SentimentRecord:
        if not texts:
            return SentimentRecord(ticker, source, 0, 0.0, 0.0, 0.0, 0.0, "No sentiment data available")
        tokens = re.findall(r"[a-zA-Z$]+", " ".join(texts).lower())
        counts = Counter(tokens)
        mentions = sum(1 for token in tokens if token in {ticker.lower(), f"${ticker.lower()}"})
        pos = sum(counts[word] for word in self.positive_words)
        neg = sum(counts[word] for word in self.negative_words)
        hype = sum(counts[word] for word in self.hype_words)
        fear = sum(counts[word] for word in self.fear_words)
        total = max(pos + neg, 1)
        sentiment = (pos - neg) / total
        hype_score = clamp(hype / max(len(texts), 1) * 20, 0, 100)
        fear_score = clamp(fear / max(len(texts), 1) * 20, 0, 100)
        pump_risk = clamp((hype_score * 0.7) + (max(mentions - 20, 0) * 2), 0, 100)
        summary = f"{mentions} ticker mentions, sentiment {sentiment:.2f}, hype {hype_score:.0f}, fear {fear_score:.0f}"
        return SentimentRecord(ticker, source, mentions, sentiment, hype_score, fear_score, pump_risk, summary)

