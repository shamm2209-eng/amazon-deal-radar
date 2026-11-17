"""Tests for robots.txt compliance, politeness and block detection."""

import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from deal_radar.http_client import (BlockedError, Page, Politeness,
                                    RequestsFetcher, RobotsChecker,
                                    RobotsDisallowed)

REAL_ROBOTS = (Path(__file__).parent / "fixtures" / "real_robots.txt").read_text(
    encoding="utf-8")


class TestRobots:
    def _checker_with_real_robots(self) -> RobotsChecker:
        checker = RobotsChecker()
        checker._parse(REAL_ROBOTS)
        checker._loaded = True
        return checker

    def test_offer_listing_is_disallowed(self):
        checker = self._checker_with_real_robots()
        # Amazon's robots.txt explicitly disallows /gp/offer-listing.
        assert checker.allowed("https://www.amazon.eg/gp/offer-listing/B091J95N68") is False

    def test_search_and_product_pages_allowed(self):
        checker = self._checker_with_real_robots()
        assert checker.allowed(
            "https://www.amazon.eg/-/en/s?srs=27972173031&language=en") is True
        assert checker.allowed("https://www.amazon.eg/-/en/dp/B091J95N68") is True

    def test_unreachable_robots_defaults_to_allow(self):
        checker = RobotsChecker()
        with mock.patch("deal_radar.http_client.requests.get",
                        side_effect=Exception("network down")):
            loaded = checker.load()
        assert loaded is False
        assert checker.allowed("https://www.amazon.eg/anything") is True


class TestPoliteness:
    def test_no_wait_on_first_fetch(self):
        p = Politeness(2.5, 6.0)
        start = __import__("time").time()
        p.wait()
        assert __import__("time").time() - start < 0.5

    def test_waits_between_fetches(self):
        p = Politeness(0.6, 0.8)
        p.wait()
        start = __import__("time").time()
        p.wait()
        assert __import__("time").time() - start >= 0.5


class TestBlockDetection:
    def test_blocked_markers(self):
        page = Page(url="u", html="<html>Enter the characters you see below</html>",
                    status=200, fetched_at=0)
        assert page.blocked is True

    def test_normal_page_not_blocked(self):
        page = Page(url="u", html="<html><body>Products</body></html>",
                    status=200, fetched_at=0)
        assert page.blocked is False


class TestRequestsFetcher:
    def _fetcher(self):
        politeness = Politeness(0.5, 0.5)
        robots = RobotsChecker()
        robots._parser = None
        robots._loaded = True
        return RequestsFetcher(politeness, robots)

    def test_503_raises_blocked(self):
        f = self._fetcher()
        resp = mock.Mock(status_code=503, text="<html>service unavailable</html>")
        with mock.patch.object(f.session, "get", return_value=resp):
            with pytest.raises(BlockedError):
                f.fetch("https://www.amazon.eg/s")

    def test_captcha_page_raises_blocked(self):
        f = self._fetcher()
        resp = mock.Mock(status_code=200,
                         text="<html>Enter the characters you see below</html>")
        with mock.patch.object(f.session, "get", return_value=resp):
            with pytest.raises(BlockedError):
                f.fetch("https://www.amazon.eg/s")

    def test_disallowed_url_raises(self):
        f = self._fetcher()
        f.robots._allows = []
        f.robots._disallows = ["/gp/offer-listing"]
        f.robots._loaded = True
        with pytest.raises(RobotsDisallowed):
            f.fetch("https://www.amazon.eg/gp/offer-listing/X")
