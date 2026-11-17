"""Data models for Deal Radar."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


# Verification statuses (kept deliberately explicit, never pretend an
# unverified offer is a confirmed bargain).
VERIFIED = "verified"
POTENTIAL_PRICE_ERROR = "potential_price_error"
UNVERIFIED = "unverified_price"
INCOMPLETE_INFO = "incomplete_product_information"

# Deal priority tiers.
TIER_NONE = "none"          # below alert threshold - just recorded
TIER_ALERT = "alert"        # >= 50% below new price
TIER_HIGH = "high_priority"  # >= 70% below new price
TIER_EXCEPTIONAL = "exceptional"  # >= 90% below new price


@dataclass
class UsedOffer:
    """A used offer discovered on a category search page."""

    asin: str
    title: str
    used_price: Optional[float]
    used_offer_count: int
    image_url: Optional[str] = None
    source_url: str = ""
    discovered_at: Optional[datetime] = None


@dataclass
class ProductPage:
    """Parsed data from an amazon.eg product page (/dp/ASIN)."""

    asin: str
    title: str
    image_url: Optional[str]
    new_price: Optional[float]          # current buy-box NEW price (live)
    list_price: Optional[float]         # crossed-out RRP / list price, if shown
    used_price: Optional[float]         # lowest used offer shown on the page
    condition: Optional[str]            # e.g. "Used - Like New"
    seller: Optional[str]               # "Sold by: X"
    dispatched_from: Optional[str]
    shipping_text: Optional[str]        # e.g. "FREE delivery Tomorrow, 18 Nov"
    buybox_is_used: bool = False        # True when the buy box itself is the used offer
    url: str = ""


@dataclass
class Deal:
    """A fully evaluated (compared) used offer."""

    offer: UsedOffer
    product: Optional[ProductPage]
    reference_price: Optional[float] = None  # NEW price used for comparison
    reference_type: str = ""                # "new_price" or "list_price"
    savings_pct: Optional[float] = None
    tier: str = TIER_NONE
    verification: str = UNVERIFIED
    suspicious: bool = False
    reason: str = ""

    @property
    def asin(self) -> str:
        return self.offer.asin

    @property
    def title(self) -> str:
        return (self.product.title if self.product and self.product.title
                else self.offer.title)

    @property
    def image_url(self) -> Optional[str]:
        return (self.product.image_url if self.product else None) or self.offer.image_url

    @property
    def used_price(self) -> Optional[float]:
        if self.product and self.product.used_price is not None:
            return self.product.used_price
        return self.offer.used_price

    @property
    def new_price(self) -> Optional[float]:
        return self.reference_price

    @property
    def condition(self) -> Optional[str]:
        return self.product.condition if self.product else None

    @property
    def seller(self) -> Optional[str]:
        return self.product.seller if self.product else None

    @property
    def shipping(self) -> Optional[str]:
        return self.product.shipping_text if self.product else None

    @property
    def url(self) -> str:
        return f"https://www.amazon.eg/dp/{self.asin}"

    @property
    def should_notify(self) -> bool:
        """True when this deal qualifies for a Telegram notification."""
        return self.tier != TIER_NONE
