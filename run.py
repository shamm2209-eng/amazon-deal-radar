#!/usr/bin/env python
"""Deal Radar entry point.

Usage:
  python run.py                # one monitoring run (used by GitHub Actions)
  python run.py --dry-run      # full run, but do not send Telegram messages
  python run.py --limit 5      # cap how many product pages are verified
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

# Make "src" importable when run from a plain checkout.
sys.path.insert(0, str(Path(__file__).parent / "src"))

from deal_radar.config import Config            # noqa: E402
from deal_radar.database import Database        # noqa: E402
from deal_radar.pipeline import Pipeline        # noqa: E402
from deal_radar.telegram import Telegram        # noqa: E402


def setup_logging(cfg: Config) -> None:
    handlers = [logging.StreamHandler(sys.stdout)]
    try:
        Path(cfg.log_file).parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(cfg.log_file, encoding="utf-8"))
    except OSError:
        pass
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Amazon Egypt Deal Radar")
    parser.add_argument("--dry-run", action="store_true",
                        help="do everything except sending Telegram messages")
    parser.add_argument("--limit", type=int, default=None,
                        help="max product pages to verify this run")
    args = parser.parse_args()

    cfg = Config()
    if args.dry_run:
        cfg.dry_run = True
    if args.limit is not None:
        cfg.max_candidates_per_run = args.limit

    # Quiet, readable logs; file logging when possible.
    setup_logging(cfg)
    log = logging.getLogger("radar")

    if not cfg.telegram_ready() and not cfg.dry_run:
        log.warning("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set - running"
                    " in dry-run mode automatically")
        cfg.dry_run = True

    db = Database(cfg.db_path)
    tg = Telegram(cfg.telegram_bot_token, cfg.telegram_chat_id, dry_run=cfg.dry_run)
    pipeline = Pipeline(cfg, db, tg)
    stats = pipeline.run()

    log.info("Summary: %s", stats.as_dict())
    return 0


if __name__ == "__main__":
    sys.exit(main())
