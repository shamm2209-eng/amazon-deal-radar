"""Tests for Telegram message building and sending (fully mocked HTTP)."""

import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from deal_radar import models
from deal_radar.models import ProductPage, UsedOffer
from deal_radar.telegram import Telegram, build_caption, fmt_egp


def make_deal(savings=51.4, tier=models.TIER_ALERT,
              verification=models.VERIFIED):
    offer = UsedOffer(asin="B091J95N68", title="iLOCK Extension cord",
                      used_price=238.95, used_offer_count=1,
                      image_url="https://m.media-amazon.com/images/I/x.jpg",
                      source_url="https://www.amazon.eg/dp/B091J95N68")
    product = ProductPage(asin="B091J95N68", title="iLOCK Extension cord",
                          image_url="https://m.media-amazon.com/images/I/hi.jpg",
                          new_price=490.0, list_price=326.39, used_price=238.95,
                          condition="Used - Like New", seller="Amazon Resale",
                          dispatched_from="Amazon",
                          shipping_text="FREE delivery Tomorrow, 18 November")
    return models.Deal(offer=offer, product=product, reference_price=490.0,
                       reference_type="new_price", savings_pct=savings,
                       tier=tier, verification=verification)


class TestFormatting:
    def test_egp_formatting(self):
        assert fmt_egp(None) == "—"
        assert fmt_egp(290.0) == "EGP 290"
        assert fmt_egp(238.95) == "EGP 238.95"
        assert fmt_egp(30000.0) == "EGP 30,000"

    def test_caption_contains_required_fields(self):
        caption = build_caption(make_deal())
        for needle in [
            "iLOCK Extension cord",
            "EGP 238.95",            # used price
            "EGP 490",               # new price
            "51%",                   # savings
            "Used - Like New",       # condition
            "Amazon Resale",         # seller
            "FREE delivery",         # shipping
            "B091J95N68",            # ASIN
            "https://www.amazon.eg/dp/B091J95N68",  # direct link
            "Cairo time",            # discovery time
            "Verified deal",         # verification status
        ]:
            assert needle in caption, f"missing: {needle}"

    def test_exceptional_gets_warning(self):
        caption = build_caption(make_deal(savings=95.0,
                                          tier=models.TIER_EXCEPTIONAL,
                                          verification=models.POTENTIAL_PRICE_ERROR))
        assert "EXCEPTIONAL" in caption
        assert "price error" in caption

    def test_html_escaped(self):
        deal = make_deal()
        deal.product.title = "Plug <b>&</b> Play"
        caption = build_caption(deal)
        assert "&lt;b&gt;&amp;&lt;/b&gt;" in caption


class TestSending:
    def test_send_deal_uses_sendphoto(self):
        tg = Telegram("token", "chat", dry_run=False)
        with mock.patch("deal_radar.telegram.requests.post") as post:
            post.return_value.json.return_value = {"ok": True,
                                                   "result": {"message_id": 42}}
            mid = tg.send_deal(make_deal())
        assert mid == 42
        method, payload = post.call_args
        assert "/bottoken/sendPhoto" in str(method)
        assert payload["json"]["photo"].startswith("https://m.media-amazon.com")

    def test_photo_failure_falls_back_to_text(self):
        tg = Telegram("token", "chat", dry_run=False)
        responses = [
            mock.Mock(json=lambda: {"ok": False, "description": "bad image"}),
            mock.Mock(json=lambda: {"ok": True, "result": {"message_id": 7}}),
        ]
        with mock.patch("deal_radar.telegram.requests.post",
                        side_effect=responses):
            mid = tg.send_deal(make_deal())
        assert mid == 7
        assert tg.failed == 1

    def test_dry_run_never_sends(self):
        tg = Telegram("token", "chat", dry_run=True)
        with mock.patch("deal_radar.telegram.requests.post") as post:
            mid = tg.send_deal(make_deal())
        assert mid is None
        assert post.call_count == 0

    def test_unconfigured_bot_skips_silently(self):
        tg = Telegram(None, None, dry_run=False)
        assert tg.send_deal(make_deal()) is None

    def test_health_message(self):
        tg = Telegram("token", "chat", dry_run=False)
        with mock.patch("deal_radar.telegram.requests.post") as post:
            post.return_value.json.return_value = {"ok": True,
                                                   "result": {"message_id": 1}}
            tg.send_health("test")
        assert "/bottoken/sendMessage" in str(post.call_args)
