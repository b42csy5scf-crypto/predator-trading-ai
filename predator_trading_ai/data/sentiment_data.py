from dataclasses import dataclass
from typing import Optional

from predator_trading_ai.config import Settings, get_settings
from predator_trading_ai.utils.logger import setup_logger


@dataclass(frozen=True)
class SentimentRecord:
    ticker: str
    source: str
    mentions: int
    sentiment_score: float
    hype_score: float
    fear_score: float
    pump_risk: float
    summary: str


class RedditSentimentCollector:
    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings = settings or get_settings()
        self.logger = setup_logger(__name__, self.settings.log_level)

    def collect(self, ticker: str, subreddits: tuple[str, ...] = ("stocks", "wallstreetbets")) -> list[str]:
        if not all([self.settings.reddit_client_id, self.settings.reddit_client_secret]):
            self.logger.warning("Reddit credentials missing; sentiment collection offline.")
            return []
        try:
            import praw

            reddit = praw.Reddit(
                client_id=self.settings.reddit_client_id,
                client_secret=self.settings.reddit_client_secret,
                user_agent=self.settings.reddit_user_agent,
            )
            posts: list[str] = []
            for subreddit in subreddits:
                for submission in reddit.subreddit(subreddit).search(ticker, limit=25, sort="new"):
                    posts.append(f"{submission.title}\n{submission.selftext[:500]}")
            return posts
        except Exception as exc:
            self.logger.exception("Failed to collect Reddit sentiment for %s: %s", ticker, exc)
            return []


class TwitterSentimentCollector:
    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings = settings or get_settings()
        self.logger = setup_logger(__name__, self.settings.log_level)

    def collect(self, ticker: str) -> list[str]:
        if not self.settings.twitter_bearer_token:
            self.logger.info("Twitter/X bearer token missing; skipping Twitter/X sentiment.")
            return []
        self.logger.warning("Twitter/X collector placeholder active for %s; add API plan-specific client.", ticker)
        return []
