"""Telegram notifications via the official Bot API (free, no account needed
other than your own bot)."""

from __future__ import annotations

import logging
import time
from typing import Optional

import requests

from . import models
from .models import Deal

log = logging.getLogger("radar.telegram")

API_BASE = "https://api.telegram.org"

TIER_EMOJI = {
    models.TIER_EXCEPTIONAL: "🚨",
    models.TIER_HIGH: "🔥",
    models.TIER_ALERT: "💰",
}
STATUS_EMOJI = {
    models.VERIFIED: "✅ Verified deal",
    models.POTENTIAL_PRICE_ERROR: "⚠️ Potential price error",
    models.UNVERIFIED: "❓ Unverified price",
    models.INCOMPLETE_INFO: "🧩 Incomplete product information",
}


def fmt_egp(value: Optional[float]) -> str:
    if value is None:
        return "—"
    if float(value).is_integer():
        return f"EGP {int(value):,}"
    return f"EGP {value:,.2f}"


def build_caption(deal: Deal) -> str:
    tier_emoji = TIER_EMOJI.get(deal.tier, "💰")
    status = STATUS_EMOJI.get(deal.verification, deal.verification)
    discovered = time.strftime("%Y-%m-%d %H:%M Cairo time", time.gmtime(time.time() + 2 * 3600))
    lines = [
        f"{tier_emoji} <b>USED DEAL RADAR</b> {tier_emoji}",
        f"<b>{_escape(deal.title or deal.asin)}</b>",
        "",
        f"<b>Used price:</b> {fmt_egp(deal.used_price)}",
        f"<b>New price:</b> {fmt_egp(deal.new_price)}"
        + (f" ({deal.reference_type.replace('_', ' ')})" if deal.reference_type == "list_price" else ""),
    ]
    if deal.savings_pct is not None:
        lines.append(f"<b>Savings:</b> {deal.savings_pct:.0f}% below new price")
    lines += [
        f"<b>Condition:</b> {_escape(deal.condition or 'not stated')}",
        f"<b>Seller:</b> {_escape(deal.seller or 'not stated')}",
        f"<b>Shipping:</b> {_escape(deal.shipping or 'not stated')}",
        f"<b>ASIN:</b> {deal.asin}",
        f"<b>Status:</b> {status}",
    ]
    if deal.reason:
        lines.append(f"<i>Note: {_escape(deal.reason)}</i>")
    lines += [
        f"<b>Link:</b> {deal.url}",
        f"<i>Discovered: {discovered}</i>",
    ]
    if deal.tier == models.TIER_EXCEPTIONAL:
        lines.insert(1, "⚠️ EXCEPTIONAL — very likely a price error. Check the listing before buying.")
    return "\n".join(lines)


def _escape(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            if text else "")


class Telegram:
    def __init__(self, bot_token: Optional[str], chat_id: Optional[str],
                 dry_run: bool = False, timeout: float = 30.0):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.dry_run = dry_run
        self.timeout = timeout
        self.sent = 0
        self.failed = 0

    def _post(self, method: str, payload: dict) -> Optional[int]:
        if self.dry_run:
            log.info("DRY RUN - would send %s to Telegram", method)
            return None
        if not self.bot_token or not self.chat_id:
            log.warning("Telegram not configured - skipping notification")
            return None
        url = f"{API_BASE}/bot{self.bot_token}/{method}"
        try:
            resp = requests.post(url, json=payload, timeout=self.timeout)
            data = resp.json()
            if not data.get("ok"):
                # Telegram error codes: 403 chat blocked, 429 flood, 400 bad request.
                log.error("Telegram %s failed: %s", method, data.get("description"))
                self.failed += 1
                return None
            self.sent += 1
            result = data.get("result", {})
            return result.get("message_id")
        except requests.RequestException as exc:
            log.error("Telegram %s error: %s", method, exc)
            self.failed += 1
            return None

    def send_deal(self, deal: Deal) -> Optional[int]:
        caption = build_caption(deal)
        image = deal.image_url
        if image:
            message_id = self._post("sendPhoto", {
                "chat_id": self.chat_id,
                "photo": image,
                "caption": caption[:1024],
                "parse_mode": "HTML",
            })
            if message_id is not None:
                return message_id
            log.info("sendPhoto failed - falling back to text message")
        return self._post("sendMessage", {
            "chat_id": self.chat_id,
            "text": caption[:4096],
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        })

    def send_health(self, text: str) -> None:
        self._post("sendMessage", {
            "chat_id": self.chat_id,
            "text": text[:4096],
            "disable_web_page_preview": True,
        })
