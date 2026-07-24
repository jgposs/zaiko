#!/usr/bin/env python3
"""
COMOLI Size Monitor (Playwright edition)
=========================================
Uses a headless browser to fully render each product page,
then checks for the "td_line-through" CSS class to detect sold-out sizes.

Alerts when a target size *becomes* available — a new arrival, or a restock.
While a size stays in stock it is not re-announced on every run.

HOW TO USE
----------
1. Install dependencies:
   pip install playwright
   playwright install chromium

2. Set your Pushover credentials in the CONFIG section below.

3. Run manually:
   python comoli_monitor.py

4. Schedule via GitHub Actions — see GITHUB_ACTIONS_SETUP.yml
"""

import json
import re
import urllib.parse
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

# ─────────────────────────────────────────────
#  CONFIG — edit these
# ─────────────────────────────────────────────

TARGET_SIZES = ["4", "5"]
BASE_URL     = "https://www.comoli.jp"
MAILORDER    = f"{BASE_URL}/mailorder"

STATE_FILE = Path(__file__).parent / "comoli_state.json"

PUSHOVER_USER_KEY  = "your-pushover-user-key"
PUSHOVER_API_TOKEN = "your-pushover-api-token"

# ─────────────────────────────────────────────
#  SCRAPING
# ─────────────────────────────────────────────

def get_product_links(page) -> list[dict]:
    """Return list of {name, url} from the mailorder listing page."""
    page.goto(MAILORDER, wait_until="domcontentloaded", timeout=60000)
    links = page.query_selector_all("a[href*='/mailorder/']")
    products = []
    seen = set()
    for a in links:
        href = a.get_attribute("href") or ""
        if href == "/mailorder" or not href.startswith("/mailorder/"):
            continue
        url = BASE_URL + href
        if url in seen:
            continue
        seen.add(url)
        name = a.inner_text().strip().replace("\n", " ")
        products.append({"name": name, "url": url})
    return products


def get_available_sizes(page, product_url: str):
    """
    Render the product page and return the list of sizes that are in stock.

    Returns None if the page could not be loaded. That is deliberately
    different from returning [] — "we failed to check" must not be recorded
    as "everything sold out", or the next successful run would look like a
    restock and fire a false alert.

    Each size sits in its own <p>, one row of sizes per colour:

        <p>2<span>/</span></p>                          -> in stock
        <p><span class="td_line-through">4</span></p>   -> sold out

    We therefore check for the strike-through *inside each <p>*. Checking
    page-wide would be wrong: if size 4 is sold out in black but available
    in white, a page-wide check would hide the white one.
    """
    try:
        page.goto(product_url, wait_until="domcontentloaded", timeout=60000)
    except Exception as e:
        print(f"[WARN] Could not load {product_url}: {e}")
        return None

    in_stock = []
    for el in page.query_selector_all("p"):
        crossed = el.query_selector("span.td_line-through")
        if crossed:
            text = crossed.inner_text().strip()
            if re.fullmatch(r"\d", text):
                print(f"  [SOLD OUT] size {text}")
            continue

        # Not struck through — read this <p>'s own text (ignoring the "/"
        # separator spans) and keep it if it's a single-digit size.
        try:
            text = el.evaluate(
                "el => Array.from(el.childNodes)"
                ".filter(n => n.nodeType === 3)"
                ".map(n => n.textContent.trim())"
                ".join('').trim()"
            )
        except Exception:
            continue
        if re.fullmatch(r"\d", text) and 1 <= int(text) <= 5:
            in_stock.append(text)

    return list(dict.fromkeys(in_stock))  # deduplicated, order preserved


# ─────────────────────────────────────────────
#  STATE
# ─────────────────────────────────────────────

def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {}


def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False))


# ─────────────────────────────────────────────
#  NOTIFICATIONS
# ─────────────────────────────────────────────

def notify(subject: str, body: str, url: str = ""):
    try:
        data = urllib.parse.urlencode({
            "token":   PUSHOVER_API_TOKEN,
            "user":    PUSHOVER_USER_KEY,
            "title":   subject,
            "message": body,
        }).encode()
        urllib.request.urlopen("https://api.pushover.net/1/messages.json", data, timeout=10)
        print(f"[PUSHOVER] Sent: {subject}")
    except Exception as e:
        print(f"[PUSHOVER ERROR] {e}")


# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────

def run():
    print(f"[INFO] Checking COMOLI mailorder for size {'/'.join(TARGET_SIZES)}…")
    state = load_state()
    alerts = []

    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox"])
        page = browser.new_page()

        products = get_product_links(page)
        if not products:
            print("[WARN] No products found — site may have changed structure.")
            browser.close()
            return

        print(f"[INFO] Found {len(products)} products. Checking each…")

        for product in products:
            url  = product["url"]
            name = product["name"]

            sizes = get_available_sizes(page, url)
            if sizes is None:
                # Couldn't read the page. Leave this product's saved state
                # untouched so a temporary failure can't look like a restock.
                print(f"[SKIP] {name} — could not load, keeping previous state")
                continue

            prev       = state.get(url, {})
            prev_sizes = prev.get("sizes")          # None = never seen before

            available = [s for s in TARGET_SIZES if s in sizes]
            newly     = [s for s in available if s not in (prev_sizes or [])]

            if newly:
                alerts.append({"name": name, "url": url, "sizes": newly})
                how = "new item" if prev_sizes is None else "back in stock"
                print(f"[ALERT] {name} — size {'/'.join(newly)} ({how})")
            elif available:
                print(f"[OK] {name} — size {'/'.join(available)} still in stock")
            else:
                print(f"[OK] {name} — no size {'/'.join(TARGET_SIZES)}")

            state[url] = {"name": name, "sizes": sizes}

        browser.close()

    save_state(state)

    # Sanity check — if we found no sizes at all across every product,
    # the page structure may have changed and the monitor may be broken
    total_sizes = sum(len(v.get("sizes", [])) for v in state.values())
    if total_sizes == 0:
        notify(
            "⚠️ COMOLI monitor may be broken",
            "No sizes found across any product — the site structure may have changed. "
            "Check the GitHub Actions logs.",
        )

    if alerts:
        lines = [
            f"🔔 {a['name'].split('COLOR')[0].strip()} — size {'/'.join(a['sizes'])}\n{a['url']}"
            for a in alerts
        ]
        body = "\n\n".join(lines)
        subject = f"COMOLI: {len(alerts)} item(s) available in size {'/'.join(TARGET_SIZES)}"
        notify(subject, body, alerts[0]["url"])
    else:
        print(f"[DONE] No new size-{'/'.join(TARGET_SIZES)} alerts this run.")


if __name__ == "__main__":
    run()
