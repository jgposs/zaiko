"""HTTP fetching with retries. Site-agnostic."""

from __future__ import annotations

import time

import requests

from .config import MAX_RETRIES, REQUEST_TIMEOUT, USER_AGENT

# Retrying these is pointless — the answer will not change today.
PERMANENT_STATUSES = {400, 401, 403, 404, 405, 410, 451}


def make_session(adapter) -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": USER_AGENT,
        "Accept-Language": adapter.accept_language,
    })
    return s


def _decoded(response: requests.Response) -> str:
    """Text, decoded with an encoding we actually trust.

    requests defaults a charset-less text/html response to ISO-8859-1 (per the
    old HTTP spec), which turns Japanese product names into mojibake in both
    the push and the committed state file. When the header didn't specify a
    charset, let the content decide instead.
    """
    content_type = response.headers.get("Content-Type", "")
    if "charset=" not in content_type.lower():
        response.encoding = response.apparent_encoding
    return response.text


def fetch(session: requests.Session, url: str) -> str | None:
    """Return page text, or None if it could not be fetched.

    None is deliberately different from an empty page: 'we failed to check'
    must not be recorded as 'everything sold out', or the next successful
    run would look like a restock and fire a false alert.
    """
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = session.get(url, timeout=REQUEST_TIMEOUT)
            if r.status_code == 200:
                return _decoded(r)

            print(f"[WARN] {url} -> HTTP {r.status_code} (attempt {attempt})")

            if r.status_code in PERMANENT_STATUSES:
                return None                     # don't burn retries on it

            if r.status_code == 429:
                # Honour the server's own backoff rather than hammering it.
                wait = r.headers.get("Retry-After")
                try:
                    delay = min(60, int(wait)) if wait else 5 * attempt
                except ValueError:
                    delay = 5 * attempt
                if attempt < MAX_RETRIES:
                    print(f"[WARN] rate limited; waiting {delay}s")
                    time.sleep(delay)
                continue

        except requests.RequestException as e:
            print(f"[WARN] {url} -> {type(e).__name__}: {e} (attempt {attempt})")

        if attempt < MAX_RETRIES:
            time.sleep(2 * attempt)
    return None
