"""Tests for deal evaluation: savings formula, tiers, verification."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from deal_radar import models
from deal_radar.config import Config
from deal_radar.deals import compute_savings, evaluate
from deal_radar.models import ProductPage, UsedOffer


def make_offer(asin="B091J95N68", title="iLOCK Extension cord",
               used_price=238.95, count=1):
    return UsedOffer(asin=asin, title=title, used_price=used_price,
                     used_offer_count=count, source_url=f"https://www.amazon.eg/dp/{asin}")


def make_product(new_price=290.0, used_price=238.95, list_price=326.39,
                 condition="Used - Like New", title="iLOCK Extension cord",
                 buybox_is_used=False):
    return ProductPage(asin="B091J95N68", title=title, image_url=None,
                       new_price=new_price, list_price=list_price,
                       used_price=used_price, condition=condition,
                       seller="Amazon Resale", dispatched_from="Amazon",
                       shipping_text="FREE delivery Tomorrow, 18 November",
                       buybox_is_used=buybox_is_used)


class TestSavings:
    def test_formula(self):
        # ((30000 - 800) / 30000) * 100 = 97.33%
        s = compute_savings(30000.0, 800.0)
        assert abs(s - 97.3333) < 0.01

    def test_exact_50(self):
        assert compute_savings(1000.0, 500.0) == 50.0

    def test_used_more_expensive(self):
        assert compute_savings(100.0, 120.0) == 0.0

    def test_missing_new_price(self):
        assert compute_savings(None, 100.0) is None

    def test_zero_new_price(self):
        assert compute_savings(0.0, 100.0) is None


class TestTiers:
    def cfg(self):
        return Config()

    def test_below_threshold_no_alert(self):
        # (1000-600)/1000 = 40% -> below the 50% alert threshold
        deal = evaluate(make_offer(used_price=600.0),
                        make_product(new_price=1000.0, used_price=600.0), self.cfg())
        assert deal.tier == models.TIER_NONE
        assert deal.savings_pct == 40.0

    def test_alert_at_50(self):
        # (1000-480)/1000 = 52% -> alert tier
        deal = evaluate(make_offer(used_price=480.0),
                        make_product(new_price=1000.0, used_price=480.0), self.cfg())
        assert deal.tier == models.TIER_ALERT
        assert deal.verification == models.VERIFIED

    def test_high_priority_at_70(self):
        # (1000-250)/1000 = 75% -> high priority
        deal = evaluate(make_offer(used_price=250.0),
                        make_product(new_price=1000.0, used_price=250.0), self.cfg())
        assert deal.tier == models.TIER_HIGH

    def test_exceptional_at_90_becomes_price_error(self):
        # (1000-80)/1000 = 92% -> exceptional + potential price error
        deal = evaluate(make_offer(used_price=80.0),
                        make_product(new_price=1000.0, used_price=80.0), self.cfg())
        assert deal.tier == models.TIER_EXCEPTIONAL
        assert deal.verification == models.POTENTIAL_PRICE_ERROR

    def test_edge_exactly_50(self):
        deal = evaluate(make_offer(used_price=500.0),
                        make_product(new_price=1000.0, used_price=500.0), self.cfg())
        assert deal.tier == models.TIER_ALERT


class TestVerification:
    def test_verified(self):
        deal = evaluate(make_offer(), make_product(), Config())
        assert deal.verification == models.VERIFIED

    def test_unverified_without_product_page(self):
        deal = evaluate(make_offer(), None, Config())
        assert deal.verification == models.UNVERIFIED
        assert deal.tier == models.TIER_NONE  # never alert unverified comparisons

    def test_incomplete_when_new_missing(self):
        product = make_product(new_price=None, buybox_is_used=False)
        deal = evaluate(make_offer(), product, Config())
        assert deal.verification == models.INCOMPLETE_INFO

    def test_unverified_never_labelled_bargain(self):
        """Unverified, non-premium products must never be alerted."""
        deal = evaluate(make_offer(used_price=10.0), None, Config())
        assert deal.verification == models.UNVERIFIED
        assert deal.tier == models.TIER_NONE
        assert not deal.should_notify

    def test_unverified_premium_low_price_is_suspicious_flag(self):
        """The 'EGP 800 iPhone' case: comparison incomplete, but the offer is
        flagged as suspicious so the user can check it themselves."""
        offer = make_offer(title="Apple iPhone 15 Pro Max 256GB", used_price=800.0)
        deal = evaluate(offer, None, Config())
        assert deal.verification == models.UNVERIFIED
        assert deal.tier == models.TIER_ALERT
        assert deal.suspicious is True
        assert "implausibly low" in deal.reason


class TestSuspicious:
    def test_premium_keyword_low_price_flagged(self):
        offer = make_offer(title="Apple iPhone 15 Pro Max 256GB", used_price=800.0)
        product = make_product(new_price=None, used_price=800.0,
                               title="Apple iPhone 15 Pro Max 256GB")
        deal = evaluate(offer, product, Config())
        assert deal.suspicious is True
        assert deal.should_notify  # flagged even though comparison incomplete
        assert "implausibly low" in deal.reason

    def test_normal_product_not_flagged(self):
        offer = make_offer(title="Phone case silicone", used_price=15.0)
        product = make_product(new_price=None, used_price=15.0,
                               title="Phone case silicone")
        deal = evaluate(offer, product, Config())
        assert deal.suspicious is False


class TestReference:
    def test_list_price_fallback(self):
        product = make_product(new_price=None, list_price=326.39,
                               buybox_is_used=True)
        deal = evaluate(make_offer(), product, Config())
        assert deal.reference_price == 326.39
        assert deal.reference_type == "list_price"
        # savings = (326.39-238.95)/326.39 = 26.8% -> below threshold
        assert deal.tier == models.TIER_NONE
