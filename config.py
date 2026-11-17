"""Configuration from environment variables (GitHub Secrets / .env)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List, Optional


# The Amazon Resale (used / warehouse) storefront node on amazon.eg.
RESALE_NODE = "27972173031"

def _real_categories() -> List[tuple]:
    """Category nodes captured from the live Amazon Resale storefront."""
    return [
        ("Amazon Devices & Accessories", "18024174031"),
        ("Automotive", "18017874031"),
        ("Baby Products", "18017931031"),
        ("Beauty", "18017988031"),
        ("Books", "18018045031"),
        ("Electronics", "18018102031"),
        ("Fashion", "18018165031"),
        ("Garden", "18019023031"),
        ("Grocery", "18020637031"),
        ("Health & Household", "18021875031"),
        ("Home & Kitchen", "18021933031"),
        ("Industrial & Scientific", "18022047031"),
        ("Office Products", "18022275031"),
        ("Sports & Outdoors", "18022446031"),
        ("Tools & Home Improvement", "18021990031"),
        ("Toys", "18022503031"),
        ("Video Games", "18022560031"),
    ]


def category_search_urls(pages: int = 1) -> List[tuple]:
    """Build category search URLs inside the Amazon Resale node.

    These URLs are allowed by amazon.eg/robots.txt (the /-/en/ prefix is
    explicitly allowed; search pages are not disallowed).
    """
    urls = []
    for name, node in _real_categories():
        for page in range(1, pages + 1):
            url = (
                "https://www.amazon.eg/-/en/s"
                f"?srs={RESALE_NODE}&bbn={RESALE_NODE}"
                f"&rh=n%3A{RESALE_NODE}%2Cn%3A{node}&dc&language=en"
                f"&s=popularity-rank&page={page}"
            )
            urls.append((name, url))
    return urls


@dataclass
class Config:
    telegram_bot_token: Optional[str] = os.getenv("TELEGRAM_BOT_TOKEN")
    telegram_chat_id: Optional[str] = os.getenv("TELEGRAM_CHAT_ID")

    # Alert thresholds in percent below the NEW price.
    alert_threshold: float = float(os.getenv("ALERT_THRESHOLD", "50"))
    high_priority_threshold: float = float(os.getenv("HIGH_PRIORITY_THRESHOLD", "70"))
    exceptional_threshold: float = float(os.getenv("EXCEPTIONAL_THRESHOLD", "90"))

    # Send a lightweight message (title + used price + link) for every
    # product that appears on Amazon Resale for the first time, even if it
    # is not a discounted deal. Set NOTIFY_NEW_PRODUCTS=0 to turn this off.
    notify_new_products: bool = os.getenv(
        "NOTIFY_NEW_PRODUCTS", "1").strip().lower() in ("1", "true", "yes")

    # Re-alert when the used price dropped by at least this much since the
    # last alert for the same ASIN.
    realert_price_drop_pct: float = float(os.getenv("REALERT_PRICE_DROP_PCT", "5"))
    alert_cooldown_hours: float = float(os.getenv("ALERT_COOLDOWN_HOURS", "24"))

    # Politeness / budget.
    fetch_delay_min: float = float(os.getenv("FETCH_DELAY_MIN", "2.5"))
    fetch_delay_max: float = float(os.getenv("FETCH_DELAY_MAX", "6.0"))
    discovery_pages_per_category: int = int(os.getenv("DISCOVERY_PAGES_PER_CATEGORY", "2"))
    max_candidates_per_run: int = int(os.getenv("MAX_CANDIDATES_PER_RUN", "30"))

    # How old a remembered NEW price may be and still be used as the
    # comparison reference when today's page shows no NEW/list price.
    historical_ref_max_days: float = float(os.getenv("HISTORICAL_REF_MAX_DAYS", "45"))

    # Storage / misc.
    db_path: str = os.getenv("RADAR_DB_PATH", "data/radar.db")
    dry_run: bool = os.getenv("DRY_RUN", "").strip().lower() in ("1", "true", "yes")
    log_file: str = os.getenv("RADAR_LOG_FILE", "data/radar.log")

    # Titles containing these premium keywords get flagged as suspicious when
    # their used price is implausibly low in absolute terms (e.g. an iPhone
    # for EGP 800) even if the NEW comparison is incomplete.
    premium_keywords: List[str] = field(default_factory=lambda: [
        "iphone", "ipad", "macbook", "apple watch", "airpods", "galaxy s",
        "galaxy z", "galaxy note", "playstation", "ps5", "ps4", "xbox",
        "nintendo switch", "surface pro", "thinkpad", "razer", "legion",
        "vivobook", "mac mini", "imac", "kindle",
    ])
    suspicious_absolute_price: float = float(os.getenv("SUSPICIOUS_ABSOLUTE_PRICE", "3000"))

    def telegram_ready(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_chat_id)
