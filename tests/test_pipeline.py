"""End-to-end pipeline test with a fully mocked fetcher.

Simulates: discovery on category pages -> verification on product pages ->
comparison -> duplicate suppression -> notification.

product_page_cheap.html is an "EGP 800 iPhone vs EGP 30,000 new" scenario
(97.3% savings -> exceptional tier + potential price error).
"""

import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from deal_radar.config import Config
from deal_radar.database import Database
from deal_radar.http_client import BlockedError, Page, RobotsChecker
from deal_radar.pipeline import Pipeline
from deal_radar.telegram import Telegram

FIXTURES = Path(__file__).parent / "fixtures"


class FakeFetcher:
    """Serves the real fixtures as if they were live pages."""

    def __init__(self, search_html, products):
        self.search_html = search_html
        self.products = products      # {asin_suffix_in_url: html}
        self.default_product = (FIXTURES / "product_page.html").read_text(encoding="utf-8")
        self.robots = RobotsChecker()
        self.robots._loaded = True    # empty rules -> allow everything
        self.fetched = 0
        self.urls = []

    def start(self):
        pass

    def stop(self):
        pass

    def fetch(self, url, timeout_ms=45000):
        self.urls.append(url)
        self.fetched += 1
        if "/s?" in url:
            return Page(url, self.search_html, 200, 0.0)
        for asin, html in self.products.items():
            if asin in url:
                return Page(url, html, 200, 0.0)
        return Page(url, self.default_product, 200, 0.0)


def build_pipeline(tmp_path, fetcher, dry_run=True):
    cfg = Config()
    cfg.dry_run = dry_run
    cfg.db_path = str(tmp_path / "pipeline_test.db")
    db = Database(cfg.db_path)
    tg = Telegram("token", "chat", dry_run=dry_run)
    pipeline = Pipeline(cfg, db, tg)
    pipeline.fetcher = fetcher
    return pipeline, db, tg


SEARCH = (FIXTURES / "search_page.html").read_text(encoding="utf-8")
CHEAP = (FIXTURES / "product_page_cheap.html").read_text(encoding="utf-8")
CHEAP_DROP = (FIXTURES / "product_page_cheap_drop.html").read_text(encoding="utf-8")


class TestPipeline:
    def test_full_run_alerts_and_stores(self, tmp_path):
        fetcher = FakeFetcher(SEARCH, {"B0F549C7WZ": CHEAP})
        pipeline, db, tg = build_pipeline(tmp_path, fetcher)
        stats = pipeline.run()

        assert stats.candidates == 3
        assert stats.compared == 3
        # The "iPhone for EGP 800 vs EGP 30,000 new" candidate qualifies
        # (97.3% savings, exceptional tier).
        assert stats.alerts_sent == 1
        assert stats.alerts_sent == db.alerts_today()
        # Alert details recorded.
        row = db.conn.execute(
            "SELECT * FROM alerts WHERE asin = 'B0F549C7WZ'").fetchone()
        assert row["tier"] == "exceptional"
        assert row["verification"] == "potential_price_error"
        assert abs(row["savings_pct"] - 97.33) < 0.1

    def test_no_repeat_for_unchanged_offers(self, tmp_path):
        fetcher = FakeFetcher(SEARCH, {"B0F549C7WZ": CHEAP})
        pipeline, db, tg = build_pipeline(tmp_path, fetcher)
        stats1 = pipeline.run()
        assert stats1.alerts_sent == 1
        stats2 = pipeline.run()
        assert stats2.alerts_sent == 0
        assert stats2.suppressed_duplicates >= 1

    def test_price_drop_realerts(self, tmp_path):
        fetcher = FakeFetcher(SEARCH, {"B0F549C7WZ": CHEAP})
        pipeline, db, tg = build_pipeline(tmp_path, fetcher)
        pipeline.run()
        # Price drops from EGP 800 to EGP 560 (30% lower) -> must alert again.
        fetcher.products["B0F549C7WZ"] = CHEAP_DROP
        stats = pipeline.run()
        assert stats.alerts_sent >= 1

    def test_blocked_run_reports(self, tmp_path):
        class BlockedFetcher(FakeFetcher):
            def fetch(self, url, timeout_ms=45000):
                raise BlockedError("blocked")

        fetcher = BlockedFetcher(SEARCH, {})
        pipeline, db, tg = build_pipeline(tmp_path, fetcher, dry_run=False)
        with mock.patch.object(Telegram, "send_health") as health:
            pipeline.run()
            assert health.call_count == 1
        db_row = db.conn.execute(
            "SELECT status FROM runs ORDER BY id DESC LIMIT 1").fetchone()
        assert db_row["status"] == "blocked"
