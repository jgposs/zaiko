"""Pushover delivery and message chunking."""

from __future__ import annotations

import os
import time

import requests

from .config import PUSHOVER_LIMIT, PUSHOVER_RETRIES

PUSHOVER_ENDPOINT = "https://api.pushover.net/1/messages.json"


def credentials() -> tuple[str, str]:
    """Read at call time so a test or a local run can change the environment."""
    return (os.environ.get("PUSHOVER_USER_KEY", ""),
            os.environ.get("PUSHOVER_API_TOKEN", ""))


def have_credentials() -> bool:
    return all(credentials())


def notify(title: str, message: str, url: str = "", url_title: str = "",
           priority: int = 0) -> bool:
    """Send one Pushover push. Returns True only if it was accepted.

    The return value matters: an alert that wasn't delivered must not be
    recorded as seen, or it is lost for good.
    """
    user_key, api_token = credentials()
    if not (user_key and api_token):
        print(f"[PUSHOVER SKIPPED - no credentials] {title}\n{message}")
        return False

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

    for attempt in range(1, PUSHOVER_RETRIES + 1):
        try:
            r = requests.post(PUSHOVER_ENDPOINT, data=payload, timeout=15)
            if r.status_code == 200:
                print(f"[PUSHOVER] Sent: {title}")
                return True
            print(f"[PUSHOVER ERROR] HTTP {r.status_code}: {r.text[:200]} "
                  f"(attempt {attempt})")
            # 4xx other than rate limiting won't fix itself on a retry.
            if 400 <= r.status_code < 500 and r.status_code != 429:
                return False
        except requests.RequestException as e:
            print(f"[PUSHOVER ERROR] {type(e).__name__}: {e} (attempt {attempt})")
        if attempt < PUSHOVER_RETRIES:
            time.sleep(2 * attempt)

    return False


def chunk_alerts(alerts: list[tuple[str, str]],
                 limit: int = PUSHOVER_LIMIT) -> list[tuple[str, str]]:
    """Group (line, url) alerts into messages that fit Pushover's size cap.

    Returns (body, url_of_first_alert_in_that_body) so a multi-part alert's
    tap-through opens something that is actually in the part you tapped.
    """
    chunks: list[tuple[str, str]] = []
    current, current_url = "", ""

    for line, url in alerts:
        if len(line) > limit:
            # Truncate from the front of the line, which is the product name;
            # the URL lives on the last line and must survive intact.
            head, _, tail = line.rpartition("\n")
            room = max(0, limit - len(tail) - 2)
            line = (head[:room] + "…\n" + tail) if room else tail[:limit]
        if not current:
            current, current_url = line, url
            continue
        candidate = f"{current}\n\n{line}"
        if len(candidate) > limit:
            chunks.append((current, current_url))
            current, current_url = line, url
        else:
            current = candidate

    if current:
        chunks.append((current, current_url))
    return chunks
