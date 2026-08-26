"""HTTP fetching with retries. Site-agnostic."""

from __future__ import annotations

import time

import requests

from .config import MAX_RETRIES, REQUEST_TIMEOUT, USER_AGENT


def make_session(adapter=None) -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": USER_AGENT,
        "Accept-Language": getattr(adapter, "accept_language", "ja,en;q=0.8"),
    })
    return s


def fetch(session: requests.Session, url: str) -> str | None:
    """Return page HTML, or None if it could not be fetched.

    None is deliberately different from an empty page: 'we failed to check'
    must not be recorded as 'everything sold out', or the next successful
    run would look like a restock and fire a false alert.
    """
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = session.get(url, timeout=REQUEST_TIMEOUT)
            if r.status_code == 200:
                return r.text
            print(f"[WARN] {url} -> HTTP {r.status_code} (attempt {attempt})")
        except requests.RequestException as e:
            print(f"[WARN] {url} -> {type(e).__name__}: {e} (attempt {attempt})")
        if attempt < MAX_RETRIES:
            time.sleep(2 * attempt)
    return None
