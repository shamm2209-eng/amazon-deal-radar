"""Deal evaluation: price comparison, classification and flagging.

Formula:  Savings % = ((New Price - Used Price) / New Price) * 100

Tiers (from the user's rules):
  >= 50% below NEW price  -> alert
  >= 70% below NEW price  -> high priority alert
  >= 90% below NEW price  -> exceptional alert

Verification is kept explicit and honest:
  verified               - NEW and USED both parsed from a live product page
  potential_price_error  - verified AND savings >= 90% (very likely an error)
  unverified_price       - comparison could not be fully confirmed
  incomplete_product_information - one or both prices missing
"""

from __future__ import annotations

import logging
from typing import Optional

from . import models
from .config import Config
from .models import (Deal, ProductPage, UsedOffer)

log = logging.getLogger("radar.deals")


def compute_savings(new_price: Optional[float], used_price: Optional[float]) -> Optional[float]:
    if new_price is None or used_price is None or new_price <= 0 or used_price < 0:
        return None
    if used_price > new_price:
        return 0.0  # used is more expensive - no saving
    return (new_price - used_price) / new_price * 100.0


def evaluate(offer: UsedOffer, product: Optional[ProductPage], cfg: Config,
             historical: Optional[tuple] = None) -> Deal:
    """Build a fully classified Deal from a discovered offer (and, when
    available, the parsed product page).

    `historical` is an optional (price, timestamp) of the last known NEW
    price recorded by an earlier run - the fallback when today's page shows
    no NEW/list price at all."""
    deal = Deal(offer=offer, product=product)

    used_price = deal.used_price
    reference_price: Optional[float] = None
    reference_type = ""

    if product is not None:
        if product.new_price is not None and not product.buybox_is_used:
            reference_price = product.new_price
            reference_type = "new_price"
        elif product.list_price is not None:
            reference_price = product.list_price
            reference_type = "list_price"
    if reference_price is None and historical is not None and historical[0]:
        reference_price = historical[0]
        reference_type = "last_known_new_price"
        deal.reference_ts = historical[1]
    deal.reference_price = reference_price
    deal.reference_type = reference_type

    deal.savings_pct = compute_savings(reference_price, used_price)

    # ---- verification status -------------------------------------------
    has_both_from_page = (product is not None and product.used_price is not None and
                          product.new_price is not None and not product.buybox_is_used)
    if has_both_from_page:
        deal.verification = models.POTENTIAL_PRICE_ERROR if (
            deal.savings_pct is not None and deal.savings_pct >= cfg.exceptional_threshold
        ) else models.VERIFIED
    elif product is None or product.used_price is None:
        # Only the search-page data is known.
        deal.verification = models.UNVERIFIED
    else:
        deal.verification = models.INCOMPLETE_INFO

    # ---- tier ------------------------------------------------------------
    savings = deal.savings_pct
    if savings is None:
        deal.tier = models.TIER_NONE
    elif savings >= cfg.exceptional_threshold:
        deal.tier = models.TIER_EXCEPTIONAL
    elif savings >= cfg.high_priority_threshold:
        deal.tier = models.TIER_HIGH
    elif savings >= cfg.alert_threshold:
        deal.tier = models.TIER_ALERT
    else:
        deal.tier = models.TIER_NONE

    # Historical comparison: honest label instead of staying silent.
    if reference_type == "last_known_new_price" and \
            deal.verification in (models.UNVERIFIED, models.INCOMPLETE_INFO):
        deal.verification = models.HISTORICAL
        if deal.tier != models.TIER_NONE and not deal.reason:
            deal.reason = ("no NEW price on the page today - compared with the "
                           "last known NEW price recorded by an earlier run")

    # Only verified deals may be labelled as bargains.
    if deal.tier != models.TIER_NONE and deal.verification != models.VERIFIED and \
            deal.verification != models.POTENTIAL_PRICE_ERROR and \
            deal.verification != models.HISTORICAL:
        deal.reason = "savings met threshold but comparison is not fully verified"

    # ---- suspicious absolute-price flag -----------------------------------
    # Flag offers that are implausibly cheap even when the NEW comparison is
    # incomplete (the classic "EGP 800 iPhone" situation).
    if used_price is not None and used_price <= cfg.suspicious_absolute_price:
        title = (deal.title or "").lower()
        if any(k in title for k in cfg.premium_keywords):
            deal.suspicious = True
            if deal.tier == models.TIER_NONE:
                deal.tier = models.TIER_ALERT
                deal.reason = ("used price is implausibly low for this product"
                               " - could be a price error, variant mismatch or scam")
    return deal
