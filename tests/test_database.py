"""Tests for SQLite persistence and duplicate prevention."""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from deal_radar.database import Database


def fresh_db(tmp_path):
    return Database(str(tmp_path / "test_radar.db"))


class TestOffers:
    def test_new_offer_detected_once(self, tmp_path):
        db = fresh_db(tmp_path)
        assert db.upsert_offer("A1", "T1", 100.0, 1, None) is True
        assert db.upsert_offer("A1", "T1", 100.0, 1, None) is False
        assert db.upsert_offer("A2", "T2", 100.0, 1, None) is True
        db.close()

    def test_offer_roundtrip(self, tmp_path):
        db = fresh_db(tmp_path)
        db.upsert_offer("A1", "Title", 238.95, 3, "img.jpg")
        row = db.get_offer("A1")
        assert row["title"] == "Title"
        assert row["used_price"] == 238.95
        assert row["used_offer_count"] == 3
        assert row["image_url"] == "img.jpg"
        db.close()


class TestAlertGating:
    def test_first_alert_sent(self, tmp_path):
        db = fresh_db(tmp_path)
        assert db.should_alert("A1", 100.0, 60.0, 24, 5) is True
        db.close()

    def test_same_price_not_repeated(self, tmp_path):
        db = fresh_db(tmp_path)
        db.record_alert("A1", 100.0, 200.0, 50.0, "alert", "verified")
        assert db.should_alert("A1", 100.0, 50.0, 24, 5) is False
        assert db.should_alert("A1", 101.0, 49.5, 24, 5) is False
        db.close()

    def test_price_drop_realerts(self, tmp_path):
        db = fresh_db(tmp_path)
        db.record_alert("A1", 100.0, 200.0, 50.0, "alert", "verified")
        # dropped by 10% -> alert again
        assert db.should_alert("A1", 90.0, 55.0, 24, 5) is True
        # dropped by only 2% -> stay quiet
        assert db.should_alert("A1", 98.0, 51.0, 24, 5) is False
        db.close()

    def test_savings_improvement_realerts(self, tmp_path):
        db = fresh_db(tmp_path)
        db.record_alert("A1", 100.0, 200.0, 50.0, "alert", "verified")
        # savings improved by 6 points at same price -> alert again
        assert db.should_alert("A1", 100.0, 56.0, 24, 5) is True
        db.close()

    def test_cooldown_after_long_quiet_period(self, tmp_path):
        db = fresh_db(tmp_path)
        old = time.time() - 3 * 86400
        db.conn.execute(
            "INSERT INTO alerts (asin, used_price, new_price, savings_pct,"
            " tier, verification, alerted_at) VALUES (?,?,?,?,?,?,?)",
            ("A1", 100.0, 200.0, 50.0, "alert", "verified", old))
        db.conn.commit()
        # unchanged price after 3 days -> still quiet (no meaningful change)
        assert db.should_alert("A1", 100.0, 50.0, 24, 5) is False
        # tiny 0.1% drop after 3 days -> quiet again
        assert db.should_alert("A1", 99.9, 50.3, 24, 5) is False
        db.close()

    def test_per_asin_isolation(self, tmp_path):
        db = fresh_db(tmp_path)
        db.record_alert("A1", 100.0, 200.0, 50.0, "alert", "verified")
        assert db.should_alert("A2", 100.0, 50.0, 24, 5) is True
        db.close()


class TestHistoryAndRuns:
    def test_price_history(self, tmp_path):
        db = fresh_db(tmp_path)
        db.record_prices("A1", 300.0, 240.0)
        db.record_prices("A1", 290.0, 238.95)
        rows = db.recent_prices("A1")
        assert len(rows) == 2
        assert rows[0]["new_price"] == 290.0  # newest first
        db.close()

    def test_run_lifecycle(self, tmp_path):
        db = fresh_db(tmp_path)
        run_id = db.start_run()
        db.finish_run(run_id, "ok", {"alerts_sent": 2})
        row = db.conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        assert row["status"] == "ok"
        assert row["stats"] == '{"alerts_sent": 2}'
        db.close()
