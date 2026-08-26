"""Pushover delivery and message chunking."""

from __future__ import annotations

import os

import requests

from .config import PUSHOVER_LIMIT

PUSHOVER_ENDPOINT = "https://api.pushover.net/1/messages.json"


def notify(title: str, message: str, url: str = "", url_title: str = "",
           priority: int = 0) -> None:
    """Send one Pushover push. Credentials are read at call time so a test
    or a local run can change the environment without reimporting."""
    user_key = os.environ.get("PUSHOVER_USER_KEY", "")
    api_token = os.environ.get("PUSHOVER_API_TOKEN", "")

    if not (user_key and api_token):
        print(f"[PUSHOVER SKIPPED - no credentials] {title}\n{message}")
        return

    payload = {
        "token": api_token,
        "user": user_key,
        "title": title,
        "message": message,
        "priority": priority,
    }
    if url:
        payload["url"] = url
        payload["url_title"] = url_title or "Open product page"

    try:
        r = requests.post(PUSHOVER_ENDPOINT, data=payload, timeout=15)
        if r.status_code == 200:
            print(f"[PUSHOVER] Sent: {title}")
        else:
            print(f"[PUSHOVER ERROR] HTTP {r.status_code}: {r.text[:200]}")
    except requests.RequestException as e:
        print(f"[PUSHOVER ERROR] {type(e).__name__}: {e}")


def chunk_lines(lines: list[str], limit: int = PUSHOVER_LIMIT) -> list[str]:
    """Group alert lines into messages that fit Pushover's size cap."""
    chunks, current = [], ""
    for line in lines:
        if len(line) > limit:
            line = line[: limit - 1] + "…"
        candidate = f"{current}\n\n{line}" if current else line
        if len(candidate) > limit:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks
