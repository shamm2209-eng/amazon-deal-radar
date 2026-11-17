"""Tests for the Amazon.eg HTML parsers (run against real captured HTML)."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from deal_radar.amazon import (parse_product_page, parse_search_cards,
                               parse_search_result_count)

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class TestSearchCards:
    def test_finds_used_offer_candidates(self):
        offers = parse_search_cards(load("search_page.html"))
        asins = {o.asin for o in offers}
        # These three cards carry a real "More Buying Choices (n used offer)" block.
        assert "B091J95N68" in asins
        assert "B0F7FVY8FS" in asins
        assert "B0F549C7WZ" in asins

    def test_skips_cards_without_used_offers(self):
        offers = parse_search_cards(load("search_page.html"))
        asins = {o.asin for o in offers}
        # Sponsored cards and cards without a used-offer block must not appear.
        assert "B0HRKV7HMV" not in asins
        assert "B0FQWSYP17" not in asins
        assert "B0DYWTWN8R" not in asins

    def test_used_price_and_count_parsed(self):
        offers = {o.asin: o for o in parse_search_cards(load("search_page.html"))}
        offer = offers["B091J95N68"]
        assert offer.used_price == 238.95
        assert offer.used_offer_count == 1
        assert offer.title.startswith("iLOCK Extension cord")
        assert offer.image_url and "m.media-amazon.com" in offer.image_url
        assert offer.source_url == "https://www.amazon.eg/dp/B091J95N68"

    def test_comma_price_parsed(self):
        offers = {o.asin: o for o in parse_search_cards(load("search_page.html"))}
        assert offers["B0F549C7WZ"].used_price == 1136.53
        assert offers["B0F549C7WZ"].used_offer_count == 2

    def test_result_count(self):
        assert parse_search_result_count(load("search_page.html")) == 9053

    def test_empty_page_is_safe(self):
        assert parse_search_cards("<html><body></body></html>") == []


class TestProductPage:
    def test_full_product_page(self):
        page = parse_product_page(load("product_page.html"), "B091J95N68",
                                  "https://www.amazon.eg/dp/B091J95N68")
        assert page is not None
        assert page.title == "iLOCK Extension cord ilock - 16 amp - 250v - 5 m"
        assert page.new_price == 290.00
        assert page.list_price == 326.39
        assert page.used_price == 238.95
        assert page.condition == "Used - Like New"
        assert page.seller == "Amazon Resale"
        assert page.dispatched_from == "Amazon"
        assert page.shipping_text == "FREE delivery Tomorrow, 18 November"
        assert page.buybox_is_used is False
        assert page.image_url and "_AC_SL1500_" in page.image_url

    def test_used_buybox_product(self):
        """When the buy box IS the used offer, the core price must not be
        mistaken for the NEW price."""
        page = parse_product_page(load("product_page_used_buybox.html"), "B091J95N68")
        assert page is not None
        assert page.buybox_is_used is True
        assert page.new_price is None
        assert page.used_price == 238.95
        assert page.list_price == 326.39

    def test_not_a_product_page(self):
        assert parse_product_page("<html><body>Robot Check</body></html>",
                                  "ABC") is None

    def test_unavailable_marker(self):
        html = load("product_page.html").replace(
            "</body>", "<span>Currently unavailable</span></body>")
        page = parse_product_page(html, "B091J95N68")
        assert page is not None  # parser is resilient; availability handled upstream
