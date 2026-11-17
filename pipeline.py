"""Pipeline: one full monitoring run.

  1. Load config + database.
  2. Load robots.txt; every fetch is checked against it.
  3. Discovery: crawl category pages of the Amazon Resale (used) storefront.
  4. Verify: fetch product pages of the most promising candidates.
  5. Compare + classify (savings %, tiers, verification, suspicious flag).
  6. Duplicate prevention via SQLite.
  7. Send Telegram alerts for qualifying deals; health summary on problems.
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field
from typing import List, Optional

from .amazon import parse_product_page, parse_search_cards
from .config import Config, category_search_urls
from .database import Database
from .deals import evaluate
from .http_client import (BlockedError, Politeness, RobotsChecker,
                          RobotsDisallowed, create_fetcher)
from .models import Deal
from .telegram import Telegram

log = logging.getLogger("radar.pipeline")


@dataclass
class RunStats:
    started: float = field(default_factory=time.time)
    categories_fetched: int = 0
    categories_blocked: int = 0
    candidates: int = 0
    product_pages_fetched: int = 0
    product_pages_blocked: int = 0
    compared: int = 0
    alerts_sent: int = 0
    new_products_sent: int = 0
    suppressed_duplicates: int = 0
    errors: int = 0

    def as_dict(self) -> dict:
        return {
            "duration_s": round(time.time() - self.started, 1),
            "categories_fetched": self.categories_fetched,
            "categories_blocked": self.categories_blocked,
            "candidates": self.candidates,
            "product_pages_fetched": self.product_pages_fetched,
            "product_pages_blocked": self.product_pages_blocked,
            "compared": self.compared,
            "alerts_sent": self.alerts_sent,
            "new_products_sent": self.new_products_sent,
            "suppressed_duplicates": self.suppressed_duplicates,
            "errors": self.errors,
        }


class Pipeline:
    def __init__(self, cfg: Config, db: Database, tg: Telegram):
        self.cfg = cfg
        self.db = db
        self.tg = tg
        self.politeness = Politeness(cfg.fetch_delay_min, cfg.fetch_delay_max)
        self.robots = RobotsChecker()
        self.fetcher = create_fetcher(self.politeness, self.robots)
        self.stats = RunStats()

    # ------------------------------------------------------------------
    def run(self) -> RunStats:
        self.stats = RunStats()
        run_id = self.db.start_run()
        status = "ok"
        try:
            self.robots.load()
            self.fetcher.start()
            candidates = self._discover()
            self.stats.candidates = len(candidates)
            deals = self._verify_and_compare(candidates)
            self.stats.compared = len(deals)
            self._notify(deals)
        except BlockedError as exc:
            log.error("Amazon blocked the run: %s", exc)
            status = "blocked"
            self._health_message_blocked()
        except Exception as exc:  # noqa: BLE001
            log.exception("run failed: %s", exc)
            status = "error"
            self.stats.errors += 1
            self._health_message_error(str(exc))
        finally:
            try:
                self.fetcher.stop()
            except Exception:  # noqa: BLE001
                pass
            self.db.finish_run(run_id, status, self.stats.as_dict())
        log.info("run finished: %s", self.stats.as_dict())
        return self.stats

    # ------------------------------------------------------------------
    def _discover(self) -> List:
        """Crawl category search pages of the Resale storefront."""
        urls = category_search_urls(pages=self.cfg.discovery_pages_per_category)
        # Shuffle a little so runs do not always hammer the same category first.
        random.shuffle(urls)
        seen: set = set()
        candidates: List[UsedOffer] = []
        for name, url in urls:
            try:
                page = self.fetcher.fetch(url)
            except RobotsDisallowed:
                log.warning("robots.txt disallows %s - skipped", url)
                continue
            except BlockedError:
                self.stats.categories_blocked += 1
                if self.stats.categories_blocked >= 3:
                    raise  # give up early; health message will explain
                continue
            except RuntimeError as exc:
                log.warning("category page failed: %s", exc)
                self.stats.errors += 1
                continue
            self.stats.categories_fetched += 1
            offers = parse_search_cards(page.html)
            for offer in offers:
                if offer.asin in seen:
                    continue
                seen.add(offer.asin)
                offer.discovered_at = time.time()
                is_new = self.db.upsert_offer(
                    offer.asin, offer.title, offer.used_price,
                    offer.used_offer_count, offer.image_url)
                candidates.append(offer)
                if is_new:
                    log.info("new used offer: %s | %s | %s",
                             offer.asin, offer.used_price, offer.title[:60])
                    if (self.cfg.notify_new_products
                            and not self.cfg.dry_run):
                        # One message per brand-new Resale listing.  The short
                        # sleep keeps us far below Telegram rate limits.
                        self.tg.send_new_product(offer)
                        self.stats.new_products_sent += 1
                        time.sleep(0.6)
            if len(candidates) >= self.cfg.max_candidates_per_run * 4:
                break
        log.info("discovery complete: %d unique candidates", len(candidates))
        return candidates

    # ------------------------------------------------------------------
    def _verify_and_compare(self, candidates: List) -> List[Deal]:
        """Fetch product pages for the most promising candidates and evaluate."""
        # Prioritise: cheapest absolute price and largest used-offer counts,
        # plus offers whose savings can only be judged on the product page.
        def rank(o):
            price = o.used_price or 10 ** 9
            return (0 if price <= self.cfg.suspicious_absolute_price else 1, price)
        candidates = sorted(candidates, key=rank)[: self.cfg.max_candidates_per_run]

        deals: List[Deal] = []
        for offer in candidates:
            url = f"https://www.amazon.eg/-/en/dp/{offer.asin}"
            product = None
            try:
                page = self.fetcher.fetch(url)
            except RobotsDisallowed:
                log.warning("robots.txt disallows %s - skipped", url)
                continue
            except BlockedError:
                self.stats.product_pages_blocked += 1
                if self.stats.product_pages_blocked >= 5:
                    raise
                continue
            except RuntimeError as exc:
                log.warning("product page failed for %s: %s", offer.asin, exc)
                self.stats.errors += 1
                continue
            self.stats.product_pages_fetched += 1
            product = parse_product_page(page.html, offer.asin, url)
            if product is None:
                log.info("no product page content for %s (likely delisted)", offer.asin)
                continue
            self.db.record_prices(offer.asin, product.new_price, product.used_price)
            self.db.update_product_info(offer.asin, product.new_price,
                                        product.condition, product.seller,
                                        product.shipping_text)
            if product.title:
                self.db.upsert_offer(offer.asin, product.title, product.used_price,
                                     offer.used_offer_count, product.image_url)
            historical = self.db.last_known_new_price(
                offer.asin, self.cfg.historical_ref_max_days)
            deal = evaluate(offer, product, self.cfg, historical)
            deals.append(deal)
        return deals

    # ------------------------------------------------------------------
    def _notify(self, deals: List[Deal]) -> None:
        for deal in deals:
            if not deal.should_notify:
                continue
            if not self.db.should_alert(
                    deal.asin, deal.used_price or 0.0, deal.savings_pct,
                    self.cfg.alert_cooldown_hours, self.cfg.realert_price_drop_pct):
                self.stats.suppressed_duplicates += 1
                continue
            message_id = self.tg.send_deal(deal)
            if message_id is None and not self.cfg.dry_run:
                # Delivery failed (bad token, network hiccup...).  Do NOT
                # record it as alerted, so the next run retries this deal
                # instead of silently skipping it forever.
                log.warning("Telegram delivery failed for %s - will retry next run",
                            deal.asin)
                continue
            self.db.record_alert(
                deal.asin, deal.used_price, deal.new_price, deal.savings_pct,
                deal.tier, deal.verification, message_id)
            self.stats.alerts_sent += 1
            log.info("alert sent: %s tier=%s savings=%s",
                     deal.asin, deal.tier, deal.savings_pct)

    # ------------------------------------------------------------------
    def _send_health_once(self, text: str, key: str = "__health__",
                          cooldown_hours: float = 6.0) -> None:
        """Send a health warning, but at most once per cooldown window so a
        long block does not spam Telegram every scheduled run."""
        last = self.db.last_alert(key)
        if last is not None and time.time() - last.alerted_at < cooldown_hours * 3600:
            log.info("health message suppressed (sent recently)")
            return
        self.tg.send_health(text)
        self.db.record_alert(key, None, None, None, "health", "health", None)

    def _health_message_blocked(self) -> None:
        if self.cfg.dry_run:
            return
        self._send_health_once(
            "🛑 <b>Deal Radar: Amazon is blocking automated access right now.</b>\n"
            "No alerts could be fetched this run. This usually clears on its own.\n"
            "The next scheduled run will try again. No prices were compared.")

    def _health_message_error(self, detail: str) -> None:
        if self.cfg.dry_run:
            return
        self._send_health_once(
            f"⚠️ <b>Deal Radar run had errors.</b>\n{detail[:300]}\n"
            f"Pages fetched: {self.stats.categories_fetched + self.stats.product_pages_fetched}. "
            "See the Actions log for details.")
