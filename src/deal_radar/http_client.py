"""Page fetching with robots.txt compliance, block detection and politeness.

Primary fetcher: a real headless Chromium browser (Playwright).  A real
browser is used because Amazon serves 503 / robot-check pages to plain
scripted requests.  A real browser does NOT bypass any protection: it
executes JavaScript and stores cookies exactly like a normal user's
browser would.  We never solve CAPTCHAs - when Amazon serves a challenge
we record it, back off and stop fetching.

Compliance: every URL is checked against amazon.eg/robots.txt before it is
fetched, and disallowed URLs are skipped and logged.
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass
from typing import Optional

import requests

log = logging.getLogger("radar.http")

ROBOTS_URL = "https://www.amazon.eg/robots.txt"

BLOCK_MARKERS = (
    "enter the characters you see below",
    "to discuss automated access",
    "type the characters you see below",
    "api-services-support@amazon.com",
    "robot check",
)


class BlockedError(Exception):
    """Amazon served an anti-bot challenge; fetching must stop/back off."""


class RobotsDisallowed(Exception):
    """The URL is disallowed by robots.txt."""


@dataclass
class Page:
    url: str
    html: str
    status: int
    fetched_at: float

    @property
    def blocked(self) -> bool:
        return any(m in self.html.lower() for m in BLOCK_MARKERS)


class RobotsChecker:
    """Checks URLs against amazon.eg robots.txt (fetched once per run).

    Implements the modern (Google-extended) robots matching semantics:
    the LONGEST matching rule wins; on a tie, disallow wins.  (Python's
    urllib.robotparser predates this and mis-evaluates amazon.eg's
    "Disallow: /- / Allow: /-/en/" combination.)
    """

    def __init__(self, timeout: float = 30.0):
        self._allows: list = []
        self._disallows: list = []
        self._timeout = timeout
        self._loaded = False

    def load(self) -> bool:
        """Fetch and parse robots.txt.  Returns True when loaded."""
        try:
            resp = requests.get(ROBOTS_URL, timeout=self._timeout)
            resp.raise_for_status()
            self._parse(resp.text)
            self._loaded = True
            log.info("robots.txt loaded (%d bytes)", len(resp.text))
            return True
        except Exception as exc:  # noqa: BLE001 - degrade to allow with warning
            # Fail-safe per RFC 9309: if robots.txt is unreachable,
            # default to allowing.  We log loudly so operators can see it.
            log.warning("robots.txt unreachable (%s) - defaulting to ALLOW", exc)
            self._allows, self._disallows = [], []
            self._loaded = True
            return False

    def _parse(self, text: str) -> None:
        """Parse robots.txt for the '*' user-agent group."""
        self._allows, self._disallows = [], []
        in_star_group = False
        for raw_line in text.splitlines():
            line = raw_line.split("#", 1)[0].strip()
            if not line or ":" not in line:
                continue
            key, _, value = line.partition(":")
            key = key.strip().lower()
            value = value.strip()
            if key == "user-agent":
                in_star_group = value == "*"
                continue
            if not in_star_group:
                continue
            if key == "allow" and value:
                self._allows.append(value)
            elif key == "disallow" and value:
                self._disallows.append(value)

    def allowed(self, url: str, user_agent: str = "*") -> bool:
        if not self._loaded:
            self.load()
        if not self._disallows and not self._allows:
            return True
        try:
            from urllib.parse import urlsplit
            split = urlsplit(url)
            path = split.path or "/"
            if not path.endswith("/") and not split.query:
                pass
            path_plus_query = path + (("?" + split.query) if split.query else "")
            # Evaluate both path-only and path+query forms.
            best = 0
            decision = True
            for rule in self._allows + self._disallows:
                rule_pattern = rule
                anchored = rule_pattern.endswith("$")
                if anchored:
                    rule_pattern = rule_pattern[:-1]
                    match = (path_plus_query.startswith(rule_pattern)
                             if rule_pattern else True)
                else:
                    match = path_plus_query.startswith(rule_pattern)
                if not match:
                    continue
                length = len(rule_pattern)
                if length > best:
                    best = length
                    decision = rule not in self._disallows
            return decision
        except Exception:  # noqa: BLE001
            log.warning("robots check failed for %s - defaulting to ALLOW", url)
            return True


class Politeness:
    """Randomized delay between page fetches."""

    def __init__(self, delay_min: float, delay_max: float):
        self.delay_min = max(0.5, delay_min)
        self.delay_max = max(self.delay_min, delay_max)
        self._last: Optional[float] = None

    def wait(self) -> None:
        if self._last is not None:
            elapsed = time.time() - self._last
            remaining = random.uniform(self.delay_min, self.delay_max) - elapsed
            if remaining > 0:
                time.sleep(remaining)
        self._last = time.time()


class PlaywrightFetcher:
    """Fetches pages with a real headless Chromium browser."""

    USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    )

    def __init__(self, politeness: Politeness, robots: RobotsChecker):
        self.politeness = politeness
        self.robots = robots
        self._pw = None
        self._browser = None
        self._context = None
        self._page = None
        self.fetched = 0
        self.blocked = 0

    def start(self) -> None:
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=True)
        self._context = self._browser.new_context(
            user_agent=self.USER_AGENT,
            locale="en-US",
            viewport={"width": 1366, "height": 900},
            timezone_id="Africa/Cairo",
        )
        self._page = self._context.new_page()
        log.info("Playwright browser started")

    def stop(self) -> None:
        for closer in (self._browser, self._pw):
            try:
                if closer:
                    closer.close()
            except Exception:  # noqa: BLE001
                pass
        self._page = None
        log.info("Playwright browser stopped")

    def fetch(self, url: str, timeout_ms: int = 45000) -> Page:
        if not self.robots.allowed(url):
            raise RobotsDisallowed(url)
        self.politeness.wait()
        for attempt in (1, 2):
            try:
                resp = self._page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
                self._page.wait_for_timeout(1500)  # let hydration settle
                html = self._page.content()
                status = resp.status if resp else 0
                page = Page(url=url, html=html, status=status, fetched_at=time.time())
                if self._looks_blocked(page, status):
                    self.blocked += 1
                    if attempt == 2:
                        raise BlockedError(f"Amazon blocked request to {url}")
                    log.warning("Challenge detected (%s) - backing off 20s and retrying", url)
                    time.sleep(20)
                    continue
                self.fetched += 1
                return page
            except BlockedError:
                raise
            except Exception as exc:  # noqa: BLE001 - transient nav errors
                if attempt == 2:
                    raise RuntimeError(f"fetch failed for {url}: {exc}") from exc
                log.warning("fetch error (%s) - retrying once: %s", url, exc)
                time.sleep(10)
        raise RuntimeError(f"fetch failed for {url}")

    @staticmethod
    def _looks_blocked(page: Page, status: int) -> bool:
        if status in (503, 429):
            return True
        return page.blocked


class RequestsFetcher:
    """Fallback fetcher using plain HTTP (kept for environments without
    Playwright).  Amazon often serves challenges to plain requests, so the
    pipeline treats BlockedError as 'this run could not fetch'."""

    def __init__(self, politeness: Politeness, robots: RobotsChecker):
        self.politeness = politeness
        self.robots = robots
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": PlaywrightFetcher.USER_AGENT,
            "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
                       "image/avif,image/webp,*/*;q=0.8"),
            "Accept-Language": "en-US,en;q=0.9",
            "Upgrade-Insecure-Requests": "1",
        })
        self.fetched = 0
        self.blocked = 0

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def fetch(self, url: str, timeout_ms: int = 45000) -> Page:
        if not self.robots.allowed(url):
            raise RobotsDisallowed(url)
        self.politeness.wait()
        try:
            resp = self.session.get(url, timeout=timeout_ms / 1000.0)
            html = resp.text
            page = Page(url=url, html=html, status=resp.status_code, fetched_at=time.time())
            if resp.status_code in (503, 429) or page.blocked:
                self.blocked += 1
                raise BlockedError(f"Amazon blocked request to {url}")
            self.fetched += 1
            return page
        except (BlockedError, RobotsDisallowed):
            raise
        except requests.RequestException as exc:
            raise RuntimeError(f"fetch failed for {url}: {exc}") from exc


def create_fetcher(politeness: Politeness, robots: RobotsChecker):
    """Use Playwright when available; fall back to plain requests."""
    try:
        import playwright  # noqa: F401
        return PlaywrightFetcher(politeness, robots)
    except ImportError:
        log.warning("playwright not installed - falling back to plain HTTP fetcher "
                    "(Amazon may block these requests)")
        return RequestsFetcher(politeness, robots)
