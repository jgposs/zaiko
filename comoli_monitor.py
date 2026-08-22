#!/usr/bin/env python3
"""
COMOLI Size Monitor
===================
Checks the COMOLI mail order pages for target sizes and sends a Pushover
alert when a size *becomes* available (a new arrival, or a restock).
While a size stays in stock it is not re-announced on every run.

The product pages are server-rendered, so this uses plain HTTP + an HTML
parser rather than a headless browser. That makes a full run take seconds
instead of minutes.

Sold-out sizes are marked on the page with <span class="td_line-through">.

HOW TO USE
----------
1. pip install requests beautifulsoup4
2. Export credentials (or edit the fallbacks below):
     export PUSHOVER_USER_KEY=...
     export PUSHOVER_API_TOKEN=...
3. python comoli_monitor.py
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup, Tag

# ─────────────────────────────────────────────
#  CONFIG
# ─────────────────────────────────────────────
TARGET_SIZES = ["4", "5"]
BASE_URL = "https://www.comoli.jp"
MAILORDER = f"{BASE_URL}/mailorder"

STATE_FILE = Path(__file__).resolve().parent / "comoli_state.json"

PUSHOVER_USER_KEY = os.environ.get("PUSHOVER_USER_KEY", "")
PUSHOVER_API_TOKEN = os.environ.get("PUSHOVER_API_TOKEN", "")

# Listing links that are not products.
NOT_PRODUCTS = {"form", "guide", "about", "faq", "law", "privacy", "contact"}

# Be a polite client: identify as a normal browser and pause between hits.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
REQUEST_TIMEOUT = 20      # seconds
REQUEST_DELAY = 0.4       # seconds between product pages
MAX_RETRIES = 3

# A <p> is treated as a size row only if its whole text is digits/slashes.
SIZE_ROW_RE = re.compile(r"^[\d\s/]+$")
SIZE_TOKEN_RE = re.compile(r"^[0-6]$")

PUSHOVER_LIMIT = 900      # chars; Pushover's hard cap is 1024


# ─────────────────────────────────────────────
#  HTTP
# ─────────────────────────────────────────────
def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": USER_AGENT,
        "Accept-Language": "ja,en;q=0.8",
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


# ─────────────────────────────────────────────
#  PARSING
# ─────────────────────────────────────────────
def parse_product_links(html: str) -> list[dict]:
    """Return [{name, url}] for each product on the mailorder listing."""
    soup = BeautifulSoup(html, "html.parser")
    products: list[dict] = []
    by_url: dict[str, dict] = {}

    for a in soup.find_all("a", href=True):
        href = a["href"].split("?")[0].split("#")[0].rstrip("/")
        if href.startswith(BASE_URL):
            href = href[len(BASE_URL):]          # absolute links count too
        if not href.startswith("/mailorder/"):
            continue
        slug = href[len("/mailorder/"):]
        if not slug or slug.split("/")[0] in NOT_PRODUCTS:
            continue

        url = BASE_URL + href
        name = a.get_text(" ", strip=True) or slug

        if url in by_url:
            # Same product linked twice (image link + text link). Keep the
            # more descriptive label for the notification.
            if len(name) > len(by_url[url]["name"]):
                by_url[url]["name"] = name
            continue

        entry = {"name": name, "url": url}
        by_url[url] = entry
        products.append(entry)
    return products


def _inside_strikethrough(node, stop_at) -> bool:
    """True if this text node sits inside a <span class="td_line-through">."""
    parent = node.parent
    while isinstance(parent, Tag):
        if parent.name == "span" and "td_line-through" in (parent.get("class") or []):
            return True
        if parent is stop_at:
            return False
        parent = parent.parent
    return False


def parse_available_sizes(html: str) -> list[str]:
    """Return the sizes that are in stock on a product page.

    Sizes live in <p> elements, one row per colour. Each size is either a
    bare text node (in stock) or wrapped in <span class="td_line-through">
    (sold out). This checks each size token individually rather than the
    <p> as a whole, so it is correct whether the site puts one size per <p>
    or several sizes in the same <p> — and a size that is sold out in one
    colour but available in another is still reported as available.
    """
    soup = BeautifulSoup(html, "html.parser")
    in_stock, sold_out = [], []

    for p in soup.find_all("p"):
        row_text = p.get_text(" ", strip=True)
        if not row_text or not SIZE_ROW_RE.match(row_text):
            continue
        for node in p.find_all(string=True):
            token = node.strip()
            if not SIZE_TOKEN_RE.match(token):
                continue
            if _inside_strikethrough(node, p):
                sold_out.append(token)
            else:
                in_stock.append(token)

    for size in dict.fromkeys(sold_out):
        if size not in in_stock:
            print(f"  [SOLD OUT] size {size}")

    return list(dict.fromkeys(in_stock))


# ─────────────────────────────────────────────
#  STATE
# ─────────────────────────────────────────────
def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except json.JSONDecodeError as e:
            print(f"[WARN] State file unreadable ({e}); starting fresh.")
    return {}


def save_state(state: dict) -> None:
    # sort_keys keeps the committed diff stable, so `git log comoli_state.json`
    # reads as a clean history of what actually changed and when.
    STATE_FILE.write_text(
        json.dumps(state, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    )


# ─────────────────────────────────────────────
#  NOTIFICATIONS
# ─────────────────────────────────────────────
def notify(title: str, message: str, url: str = "", priority: int = 0) -> None:
    if not (PUSHOVER_USER_KEY and PUSHOVER_API_TOKEN):
        print(f"[PUSHOVER SKIPPED — no credentials] {title}\n{message}")
        return
    payload = {
        "token": PUSHOVER_API_TOKEN,
        "user": PUSHOVER_USER_KEY,
        "title": title,
        "message": message,
        "priority": priority,
    }
    if url:
        payload["url"] = url
        payload["url_title"] = "Open on COMOLI"
    try:
        r = requests.post(
            "https://api.pushover.net/1/messages.json", data=payload, timeout=15
        )
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


# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────
def run() -> int:
    target_label = "/".join(TARGET_SIZES)
    print(f"[INFO] Checking COMOLI mailorder for size {target_label}…")

    session = make_session()
    state = load_state()
    alerts: list[dict] = []
    checked = failed = sizes_seen = 0

    listing = fetch(session, MAILORDER)
    if listing is None:
        notify(
            "⚠️ COMOLI monitor could not reach the site",
            "The mailorder listing page failed to load on every attempt. "
            "Check the GitHub Actions logs.",
            priority=0,
        )
        return 1

    products = parse_product_links(listing)
    if not products:
        notify(
            "⚠️ COMOLI monitor may be broken",
            "No product links found on the mailorder page — the site "
            "structure may have changed. Check the GitHub Actions logs.",
            priority=0,
        )
        return 1

    print(f"[INFO] Found {len(products)} products. Checking each…")

    try:
        for product in products:
            url, name = product["url"], product["name"]
            html = fetch(session, url)

            if html is None:
                # Leave this product's saved state untouched so a temporary
                # failure can't look like a restock next time.
                failed += 1
                print(f"[SKIP] {name} — could not load, keeping previous state")
                continue

            checked += 1
            sizes = parse_available_sizes(html)
            sizes_seen += len(sizes)

            prev = state.get(url, {})
            prev_sizes = prev.get("sizes")                 # None = never seen
            available = [s for s in TARGET_SIZES if s in sizes]
            newly = [s for s in available if s not in (prev_sizes or [])]

            if newly:
                how = "new item" if prev_sizes is None else "back in stock"
                alerts.append({"name": name, "url": url, "sizes": newly})
                print(f"[ALERT] {name} — size {'/'.join(newly)} ({how})")
            elif available:
                print(f"[OK] {name} — size {'/'.join(available)} still in stock")
            else:
                print(f"[OK] {name} — no size {target_label}")

            # Only stamp a time when stock actually moved, so the state file
            # (and its git history) records real changes rather than heartbeats.
            changed_at = prev.get("changed_at")
            if prev_sizes != sizes:
                changed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            state[url] = {"name": name, "sizes": sizes, "changed_at": changed_at}
            time.sleep(REQUEST_DELAY)
    finally:
        save_state(state)

    # Structure sanity check, scoped to THIS run — if we read pages fine but
    # found no sizes anywhere, the markup probably changed under us.
    if checked and sizes_seen == 0:
        notify(
            "⚠️ COMOLI monitor may be broken",
            f"Loaded {checked} product pages but found no sizes on any of "
            "them — the site structure may have changed. Check the GitHub "
            "Actions logs.",
        )
        return 1

    if failed and checked == 0:
        notify(
            "⚠️ COMOLI monitor could not read any product",
            f"All {failed} product pages failed to load. Check the logs.",
        )
        return 1

    if alerts:
        lines = [
            f"🔔 {a['name'].split('COLOR')[0].strip()} — size {'/'.join(a['sizes'])}\n{a['url']}"
            for a in alerts
        ]
        chunks = chunk_lines(lines)
        for i, body in enumerate(chunks, 1):
            suffix = f" ({i}/{len(chunks)})" if len(chunks) > 1 else ""
            notify(
                f"COMOLI: {len(alerts)} item(s) in size {target_label}{suffix}",
                body,
                url=alerts[0]["url"],
                priority=1,          # high priority: these sell out fast
            )
    else:
        print(f"[DONE] No new size-{target_label} alerts this run.")

    print(f"[DONE] {checked} checked, {failed} failed, {len(alerts)} alert(s).")
    return 0


if __name__ == "__main__":
    sys.exit(run())
