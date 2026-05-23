from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class WatchlistEntry:
    ticker: str
    sector: str
    correlation_group: str


ELITE_WATCHLIST: tuple[WatchlistEntry, ...] = (
    WatchlistEntry("AAPL", "Technology", "mega_cap_tech"),
    WatchlistEntry("MSFT", "Technology", "mega_cap_tech"),
    WatchlistEntry("NVDA", "Technology", "ai_semis"),
    WatchlistEntry("AVGO", "Technology", "ai_semis"),
    WatchlistEntry("ORCL", "Technology", "enterprise_software"),
    WatchlistEntry("CRM", "Technology", "enterprise_software"),
    WatchlistEntry("AMD", "Technology", "ai_semis"),
    WatchlistEntry("ADBE", "Technology", "enterprise_software"),
    WatchlistEntry("CSCO", "Technology", "networking"),
    WatchlistEntry("IBM", "Technology", "enterprise_software"),
    WatchlistEntry("NOW", "Technology", "enterprise_software"),
    WatchlistEntry("QCOM", "Technology", "ai_semis"),
    WatchlistEntry("TXN", "Technology", "semis_quality"),
    WatchlistEntry("AMAT", "Technology", "semi_equipment"),
    WatchlistEntry("LRCX", "Technology", "semi_equipment"),
    WatchlistEntry("JPM", "Financials", "money_center_banks"),
    WatchlistEntry("BAC", "Financials", "money_center_banks"),
    WatchlistEntry("WFC", "Financials", "money_center_banks"),
    WatchlistEntry("GS", "Financials", "investment_banks"),
    WatchlistEntry("MS", "Financials", "investment_banks"),
    WatchlistEntry("V", "Financials", "payments"),
    WatchlistEntry("MA", "Financials", "payments"),
    WatchlistEntry("AXP", "Financials", "payments"),
    WatchlistEntry("LLY", "Healthcare", "large_pharma"),
    WatchlistEntry("JNJ", "Healthcare", "large_pharma"),
    WatchlistEntry("UNH", "Healthcare", "managed_care"),
    WatchlistEntry("ABBV", "Healthcare", "large_pharma"),
    WatchlistEntry("MRK", "Healthcare", "large_pharma"),
    WatchlistEntry("TMO", "Healthcare", "life_science_tools"),
    WatchlistEntry("ABT", "Healthcare", "med_devices"),
    WatchlistEntry("ISRG", "Healthcare", "med_devices"),
    WatchlistEntry("AMZN", "Consumer", "mega_cap_consumer"),
    WatchlistEntry("COST", "Consumer", "quality_retail"),
    WatchlistEntry("HD", "Consumer", "home_improvement"),
    WatchlistEntry("MCD", "Consumer", "restaurants"),
    WatchlistEntry("NKE", "Consumer", "consumer_brands"),
    WatchlistEntry("SBUX", "Consumer", "restaurants"),
    WatchlistEntry("LOW", "Consumer", "home_improvement"),
    WatchlistEntry("BKNG", "Consumer", "travel_platforms"),
    WatchlistEntry("XOM", "Industrials/Energy", "integrated_energy"),
    WatchlistEntry("CVX", "Industrials/Energy", "integrated_energy"),
    WatchlistEntry("COP", "Industrials/Energy", "upstream_energy"),
    WatchlistEntry("GE", "Industrials/Energy", "aerospace_industrials"),
    WatchlistEntry("CAT", "Industrials/Energy", "industrial_cyclicals"),
    WatchlistEntry("RTX", "Industrials/Energy", "aerospace_defense"),
    WatchlistEntry("PLD", "Real Estate", "industrial_reits"),
    WatchlistEntry("AMT", "Real Estate", "tower_reits"),
    WatchlistEntry("EQIX", "Real Estate", "data_center_reits"),
    WatchlistEntry("WELL", "Real Estate", "healthcare_reits"),
    WatchlistEntry("PSA", "Real Estate", "storage_reits"),
)

EXPECTED_SECTOR_COUNTS = {
    "Technology": 15,
    "Financials": 8,
    "Healthcare": 8,
    "Consumer": 8,
    "Industrials/Energy": 6,
    "Real Estate": 5,
}

DEFAULT_WATCHLIST = ",".join(entry.ticker for entry in ELITE_WATCHLIST)
SECTOR_BY_TICKER = {entry.ticker: entry.sector for entry in ELITE_WATCHLIST}
CORRELATION_GROUP_BY_TICKER = {entry.ticker: entry.correlation_group for entry in ELITE_WATCHLIST}


def parse_watchlist(raw_watchlist: str) -> list[str]:
    return [ticker.strip().upper() for ticker in raw_watchlist.split(",") if ticker.strip()]


def validate_watchlist(tickers: Iterable[str]) -> list[str]:
    parsed = [ticker.upper() for ticker in tickers]
    issues: list[str] = []
    duplicates = sorted({ticker for ticker in parsed if parsed.count(ticker) > 1})
    if duplicates:
        issues.append(f"duplicate tickers: {', '.join(duplicates)}")
    missing_metadata = [ticker for ticker in parsed if ticker not in SECTOR_BY_TICKER]
    if missing_metadata:
        issues.append(f"missing metadata: {', '.join(missing_metadata)}")
    return issues


def sector_counts(tickers: Iterable[str]) -> dict[str, int]:
    counts = {sector: 0 for sector in EXPECTED_SECTOR_COUNTS}
    for ticker in tickers:
        sector = SECTOR_BY_TICKER.get(ticker.upper())
        if sector:
            counts[sector] += 1
    return counts
