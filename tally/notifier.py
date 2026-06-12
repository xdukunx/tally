"""
Notifiers for tally.

The engine takes any callable (title, body) -> None. Shipped here:
  - TelegramNotifier: stdlib urllib, no third-party deps
  - StdoutNotifier:   prints; useful for testing and for users who don't
                      want Telegram at all

Config comes from environment / a config file the USER provides. No token,
no chat id, no hostname is ever committed. See examples/config.env.example.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request


def _send_telegram(token: str, chat_id: str, text: str) -> None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode(
        {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    ).encode()
    try:
        urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=15)
    except Exception as e:  # never let a notify failure crash a 14h job
        print(f"[tally] telegram notify failed: {e}", file=sys.stderr)


def telegram_notifier(token: str, chat_id: str):
    def notify(title: str, body: str) -> None:
        _send_telegram(token, chat_id, f"<b>{title}</b>\n{body}")
    return notify


def stdout_notifier():
    def notify(title: str, body: str) -> None:
        print(f"\n[tally] {title}\n{body}\n")
    return notify


def poll_updates(token: str, offset=None, timeout: int = 10) -> list:
    """Long-poll Telegram getUpdates. Returns the raw update list ([] on any
    network error — the daemon must never die because Telegram hiccuped).

    `offset` is the Telegram update-id watermark: pass last_update_id + 1 to
    acknowledge everything seen so far."""
    params = {"timeout": int(timeout)}
    if offset is not None:
        params["offset"] = offset
    url = f"https://api.telegram.org/bot{token}/getUpdates?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=timeout + 15) as r:
            data = json.load(r)
        return data.get("result", []) if data.get("ok") else []
    except Exception as e:
        print(f"[tally] telegram poll failed: {e}", file=sys.stderr)
        return []


def from_env():
    """Build a notifier from environment variables.

    TALLY_TELEGRAM_TOKEN + TALLY_TELEGRAM_CHAT_ID -> Telegram.
    Otherwise -> stdout (so tally always works out of the box).
    """
    token = os.environ.get("TALLY_TELEGRAM_TOKEN")
    chat = os.environ.get("TALLY_TELEGRAM_CHAT_ID")
    if token and chat:
        return telegram_notifier(token, chat)
    return stdout_notifier()
