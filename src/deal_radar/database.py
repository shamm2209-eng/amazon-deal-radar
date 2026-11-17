"""SQLite persistence: offers, alerts, price history, run stats.

Provides duplicate-prevention so notifications are sent only for NEW
qualifying offers or meaningful qualifying price changes.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

log = logging.getLogger("radar.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS offers (
    asin            TEXT PRIMARY KEY,
    title           TEXT,
    used_price      REAL,
    used_offer_count INTEGER,
    image_url       TEXT,
    new_price       REAL,
    condition       TEXT,
    seller          TEXT,
    shipping        TEXT,
    first_seen      REAL NOT NULL,
    last_seen       REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS alerts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    asin        TEXT NOT NULL,
    used_price  REAL,
    new_price   REAL,
    savings_pct REAL,
    tier        TEXT,
    verification TEXT,
    message_id  INTEGER,
    alerted_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_alerts_asin ON alerts(asin, alerted_at);

CREATE TABLE IF NOT EXISTS price_history (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    asin    TEXT NOT NULL,
    ts      REAL NOT NULL,
    new_price REAL,
    used_price REAL
);
CREATE INDEX IF NOT EXISTS idx_history_asin ON price_history(asin, ts);

CREATE TABLE IF NOT EXISTS runs (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    started  REAL NOT NULL,
    finished REAL,
    status   TEXT,
    stats    TEXT
);
"""


@dataclass
class LastAlert:
    asin: str
    used_price: float
    savings_pct: Optional[float]
    alerted_at: float


class Database:
    def __init__(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # ---- offers --------------------------------------------------------
    def upsert_offer(self, asin: str, title: str, used_price, used_count,
                     image_url) -> bool:
        """Insert or update an offer.  Returns True if it was NEW (first seen)."""
        now = time.time()
        row = self.conn.execute(
            "SELECT asin FROM offers WHERE asin = ?", (asin,)).fetchone()
        if row is None:
            self.conn.execute(
                "INSERT INTO offers (asin, title, used_price, used_offer_count,"
                " image_url, first_seen, last_seen) VALUES (?,?,?,?,?,?,?)",
                (asin, title, used_price, used_count, image_url, now, now))
            self.conn.commit()
            return True
        self.conn.execute(
            "UPDATE offers SET title=?, used_price=?, used_offer_count=?,"
            " image_url=?, last_seen=? WHERE asin=?",
            (title, used_price, used_count, image_url, now, asin))
        self.conn.commit()
        return False

    def update_product_info(self, asin: str, new_price, condition, seller,
                            shipping) -> None:
        """Store verified product-page details on the offer row."""
        self.conn.execute(
            "UPDATE offers SET new_price=?, condition=?, seller=?, shipping=?"
            " WHERE asin=?", (new_price, condition, seller, shipping, asin))
        self.conn.commit()

    def get_offer(self, asin: str) -> Optional[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM offers WHERE asin = ?", (asin,)).fetchone()

    # ---- alerts --------------------------------------------------------
    def last_alert(self, asin: str) -> Optional[LastAlert]:
        row = self.conn.execute(
            "SELECT asin, used_price, savings_pct, alerted_at FROM alerts"
            " WHERE asin = ? ORDER BY alerted_at DESC LIMIT 1", (asin,)).fetchone()
        if row is None:
            return None
        return LastAlert(asin=row["asin"], used_price=row["used_price"] or 0.0,
                         savings_pct=row["savings_pct"], alerted_at=row["alerted_at"])

    def record_alert(self, asin: str, used_price, new_price, savings_pct,
                     tier: str, verification: str, message_id=None) -> None:
        self.conn.execute(
            "INSERT INTO alerts (asin, used_price, new_price, savings_pct,"
            " tier, verification, message_id, alerted_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (asin, used_price, new_price, savings_pct, tier, verification,
             message_id, time.time()))
        self.conn.commit()

    def should_alert(self, asin: str, used_price: float, savings_pct,
                     cooldown_hours: float, realert_drop_pct: float) -> bool:
        """Duplicate prevention.

        Alert when:
          - the ASIN was never alerted, OR
          - the used price dropped >= realert_drop_pct since the last alert, OR
          - savings improved by >= realert_drop_pct points, OR
          - the cooldown elapsed AND the price changed meaningfully.
        Suppress otherwise (unchanged offers never re-notify).
        """
        last = self.last_alert(asin)
        if last is None:
            return True
        if not last.used_price:
            return True
        drop_pct = (last.used_price - used_price) / last.used_price * 100.0
        if drop_pct >= realert_drop_pct:
            return True
        if savings_pct is not None and last.savings_pct is not None:
            if savings_pct - last.savings_pct >= realert_drop_pct:
                return True
        if time.time() - last.alerted_at >= cooldown_hours * 3600:
            # Cooldown elapsed but price essentially unchanged -> stay quiet.
            return drop_pct >= 0.5 or bool(
                savings_pct is not None and last.savings_pct is not None and
                savings_pct - last.savings_pct >= 0.5)
        return False

    # ---- price history ---------------------------------------------------
    def record_prices(self, asin: str, new_price, used_price) -> None:
        self.conn.execute(
            "INSERT INTO price_history (asin, ts, new_price, used_price)"
            " VALUES (?,?,?,?)", (asin, time.time(), new_price, used_price))
        self.conn.commit()

    def recent_prices(self, asin: str, limit: int = 20):
        return self.conn.execute(
            "SELECT ts, new_price, used_price FROM price_history"
            " WHERE asin = ? ORDER BY ts DESC LIMIT ?", (asin, limit)).fetchall()

    # ---- runs --------------------------------------------------------------
    def start_run(self) -> int:
        cur = self.conn.execute(
            "INSERT INTO runs (started, status) VALUES (?, 'running')",
            (time.time(),))
        self.conn.commit()
        return int(cur.lastrowid)

    def finish_run(self, run_id: int, status: str, stats: dict) -> None:
        self.conn.execute(
            "UPDATE runs SET finished = ?, status = ?, stats = ? WHERE id = ?",
            (time.time(), status, json.dumps(stats), run_id))
        self.conn.commit()

    def alerts_today(self) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM alerts WHERE alerted_at > ?",
            (time.time() - 86400,)).fetchone()
        return int(row["n"])
