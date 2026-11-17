"""Parsers for amazon.eg search pages and product pages.

Built against the live HTML structure of amazon.eg (captured in tests/fixtures).
All parsing is defensive: unknown structures yield empty results rather than
wrong data.
"""

from __future__ import annotations

import logging
import re
from typing import List, Optional

from bs4 import BeautifulSoup

from .models import ProductPage, UsedOffer

log = logging.getLogger("radar.amazon")

# EGP price text: "EGP 238.95", "EGP238.95", "EGP 1,349.10"
_PRICE_RE = re.compile(r"EGP\s?([\d][\d,]*(?:\.\d{1,2})?)")
# "1 used offer" / "(9 used offers)"
_USED_COUNT_RE = re.compile(r"(\d+)\s+used\s+offers?", re.I)
_CONDITION_RE = re.compile(r"Used\s*[–—-]\s*([A-Za-z]+(?: [A-Za-z]+)?)(?=\s|$)")
_UNAVAILABLE_RE = re.compile(r"currently unavailable", re.I)


def _to_float(text: str) -> Optional[float]:
    m = _PRICE_RE.search(text)
    if not m:
        return None
    digits = m.group(1).replace(",", "")
    try:
        return float(digits)
    except ValueError:
        return None


def _normalize_title(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _price_from(el) -> Optional[float]:
    """Extract a price from a DOM element containing an Amazon price block.

    Tries the hidden a-offscreen text first ("EGP 238.95"), then the visible
    whole+fraction spans ("EGP" "290" "." "00").
    """
    if el is None:
        return None
    off = el.select_one(".a-offscreen")
    if off is not None:
        price = _to_float(off.get_text())
        if price is not None:
            return price
    whole = el.select_one(".a-price-whole")
    frac = el.select_one(".a-price-fraction")
    if whole is not None:
        whole_text = whole.get_text().replace(".", "").strip()
        frac_text = frac.get_text().strip() if frac is not None else ""
        m = re.match(r"[\d,]+", whole_text)
        if m:
            digits = (m.group(0) + ("." + frac_text if frac_text else "")).replace(",", "")
            try:
                return float(digits)
            except ValueError:
                return None
    return _to_float(el.get_text(" ", strip=True))


def parse_search_cards(html: str) -> List[UsedOffer]:
    """Parse a category search page into used-offer candidates.

    Inside the Amazon Resale search scope, a card shows a used offer through
    its "More Buying Choices" block:
        More Buying Choices
        EGP 238.95 (1 used offer)
    Cards without that block have no (visible) used offer and are skipped.
    """
    soup = BeautifulSoup(html, "html.parser")
    offers: List[UsedOffer] = []
    for card in soup.select('div[data-component-type="s-search-result"]'):
        asin = card.get("data-asin")
        if not asin:
            continue
        title_el = card.find("h2")
        title = ""
        if title_el is not None:
            title = _normalize_title(title_el.get("aria-label") or title_el.get_text())
        img = card.select_one("img.s-image")
        image_url = img.get("src") if img is not None else None

        used_price: Optional[float] = None
        used_count = 0
        card_price: Optional[float] = None

        # Own price on the card (may be a used offer shown directly on card).
        own_price_el = card.select_one(".a-price .a-offscreen")
        if own_price_el is not None:
            card_price = _to_float(own_price_el.get_text())

        # "More Buying Choices" secondary-offer block.
        mbc = card.select_one('[data-cy="secondary-offer-recipe"]')
        if mbc is not None:
            mbc_text = mbc.get_text(" ", strip=True)
            count_match = _USED_COUNT_RE.search(mbc_text)
            if count_match:
                used_count = int(count_match.group(1))
                used_price = _to_float(mbc_text)
        if used_count <= 0:
            continue
        if used_price is None:
            used_price = card_price
        if used_price is None:
            continue

        offers.append(UsedOffer(
            asin=asin,
            title=title,
            used_price=used_price,
            used_offer_count=used_count,
            image_url=image_url,
            source_url=f"https://www.amazon.eg/dp/{asin}",
        ))
    log.info("parsed %d used-offer candidates from search page", len(offers))
    return offers


def parse_search_result_count(html: str) -> Optional[int]:
    """Total result count from "1-24 of 9,053 results"."""
    m = re.search(r"of\s+([\d,]+)\s+results?", html)
    if not m:
        return None
    try:
        return int(m.group(1).replace(",", ""))
    except ValueError:
        return None


def parse_product_page(html: str, asin: str, url: str = "") -> Optional[ProductPage]:
    """Parse an amazon.eg product page.

    Extracts (when present):
      - NEW buy-box price (live, the reference for comparisons)
      - list price (crossed-out RRP)
      - lowest used offer: price, condition, seller, dispatch origin, shipping
    """
    soup = BeautifulSoup(html, "html.parser")

    if not soup.select_one("#productTitle"):
        # Not a product page (challenge, empty, 404, dog page...).
        return None

    title = _normalize_title(soup.select_one("#productTitle").get_text())
    img = soup.select_one("#landingImage")
    image_url = None
    if img is not None:
        image_url = img.get("data-old-hires") or img.get("src")

    # --- NEW price -----------------------------------------------------
    # corePriceDisplay_desktop_feature_div is the buy-box price block.  When
    # the buy box itself is a used offer (Amazon Resale product), that block
    # holds the USED price instead - handled below.
    core_price: Optional[float] = None
    core = soup.select_one("#corePriceDisplay_desktop_feature_div .priceToPay")
    if core is None:
        core = soup.select_one("#apex_desktop .priceToPay")
    if core is not None:
        core_price = _price_from(core)
    if core_price is None:
        # Fallback: the accessibility label reads "EGP 290.00 with N percent savings".
        label = soup.select_one("#apex-pricetopay-accessibility-label")
        if label is not None:
            core_price = _to_float(label.get_text())

    buybox_text = ""
    buybox = soup.select_one("#buybox, #buybox_feature_div")
    if buybox is not None:
        buybox_text = buybox.get_text(" ", strip=True)
    buybox_is_used = bool(re.search(r"buy used", buybox_text, re.I)) and \
        not re.search(r"buy new", buybox_text, re.I)

    # --- List price (RRP) ----------------------------------------------
    list_price: Optional[float] = None
    basis = soup.select_one(".basisPrice .a-offscreen")
    if basis is not None:
        list_price = _to_float(basis.get_text())
    if list_price is None:
        lp = re.search(r"List Price:?\s*EGP\s?([\d,]+(?:\.\d+)?)", html)
        if lp:
            try:
                list_price = float(lp.group(1).replace(",", ""))
            except ValueError:
                list_price = None

    # --- Used offer accordion -------------------------------------------
    used_price: Optional[float] = None
    condition: Optional[str] = None
    seller: Optional[str] = None
    dispatched_from: Optional[str] = None
    shipping_text: Optional[str] = None

    used_row = soup.select_one("#usedAccordionRow")
    if used_row is not None:
        row_text = used_row.get_text(" ", strip=True)
        used_price = _price_from(used_row.select_one(".a-price")) or _to_float(row_text)
        cond = _CONDITION_RE.search(row_text)
        if cond:
            condition = f"Used - {cond.group(1).strip()}"
        seller_match = re.search(
            r"Sold by:\s*(.+?)(?=\s*\(?\d+ ratings|\s*Seller rating|\s{2,}|$)", row_text)
        if seller_match:
            seller = _normalize_title(seller_match.group(1))
        dispatch_match = re.search(
            r"Dispatched from:\s*(.+?)(?=\s*Sold by:|\s{2,}|$)", row_text)
        if dispatch_match:
            dispatched_from = _normalize_title(dispatch_match.group(1))
        # Prefer structured delivery metadata when present.
        delivery_el = used_row.select_one("[data-csa-c-delivery-time]")
        if delivery_el is not None:
            price_attr = (delivery_el.get("data-csa-c-delivery-price") or "").strip()
            time_attr = _normalize_title(delivery_el.get("data-csa-c-delivery-time") or "")
            if time_attr:
                prefix = "FREE delivery" if price_attr.upper() == "FREE" else "Delivery"
                shipping_text = f"{prefix} {time_attr}"
        if shipping_text is None:
            ship = re.search(r"((?:FREE |Free )?delivery .{5,60}?)(?=\s{2,}|$|Add to cart)",
                             row_text)
            if ship:
                shipping_text = _normalize_title(ship.group(1))
    elif used_price is None:
        # Some pages show used info in the AOD block only.
        aod = soup.select_one("#all-offers-display")
        if aod is not None:
            used_price = _to_float(aod.get_text(" ", strip=True))

    if buybox_is_used:
        # The buy box IS the used offer; its core price is the used price.
        if used_price is None:
            used_price = core_price
        new_price = None
    else:
        new_price = core_price

    return ProductPage(
        asin=asin,
        title=title,
        image_url=image_url,
        new_price=new_price,
        list_price=list_price,
        used_price=used_price,
        condition=condition,
        seller=seller,
        dispatched_from=dispatched_from,
        shipping_text=shipping_text,
        buybox_is_used=buybox_is_used,
        url=url,
    )
