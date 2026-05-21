#!/usr/bin/env python3
"""
COMOLI Size Monitor (Playwright edition)
=========================================
Uses a headless browser to fully render each product page,
then checks for the "td_line-through" CSS class to detect sold-out sizes.

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

TARGET_SIZE = "4"
BASE_URL    = "https://www.comoli.jp"
MAILORDER   = f"{BASE_URL}/mailorder"

STATE_FILE = Path(__file__).parent / "comoli_state.json"

PUSHOVER_USER_KEY  = "your-pushover-user-key"
PUSHOVER_API_TOKEN = "your-pushover-api-token"

# ─────────────────────────────────────────────
#  SCRAPING
# ─────────────────────────────────────────────

def get_product_links(page) -> list[dict]:
    """Return list of {name, url} from the mailorder listing page."""
    page.goto(MAILORDER, wait_until="networkidle", timeout=30000)
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


def get_available_sizes(page, product_url: str) -> list[str]:
    """
    Render the product page and return sizes that are in stock.

    Comoli marks sold-out sizes with class="td_line-through" on the
    <span> wrapping the size number. In-stock sizes are direct text
    nodes inside <p> elements with no such class.
    """
    try:
        page.goto(product_url, wait_until="networkidle", timeout=30000)
    except Exception as e:
        print(f"[WARN] Could not load {product_url}: {e}")
        return []

    # Sold-out sizes: <span class="td_line-through">4</span>
    sold_out = set()
    for el in page.query_selector_all("span.td_line-through"):
        text = el.inner_text().strip()
        if re.match(r"^\d$", text) and 1 <= int(text) <= 5:
            sold_out.add(text)
            print(f"  [SOLD OUT] size {text}")

    # In-stock sizes are direct text nodes inside <p> elements
    in_stock = []
    for el in page.query_selector_all("p"):
        try:
            text = el.evaluate(
                "el => Array.from(el.childNodes)"
                ".filter(n => n.nodeType === 3)"
                ".map(n => n.textContent.trim())"
                ".join('').trim()"
            )
        except Exception:
            continue
        if not re.match(r"^\d$", text):
            continue
        if int(text) < 1 or int(text) > 5:
            continue
        if text not in sold_out:
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
            "token":     PUSHOVER_API_TOKEN,
            "user":      PUSHOVER_USER_KEY,
            "title":     subject,
            "message":   body,
            "url":       url,
            "url_title": "View on COMOLI",
        }).encode()
        urllib.request.urlopen("https://api.pushover.net/1/messages.json", data, timeout=10)
        print(f"[PUSHOVER] Sent: {subject}")
    except Exception as e:
        print(f"[PUSHOVER ERROR] {e}")


# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────

def run():
    print(f"[INFO] Checking COMOLI mailorder for size {TARGET_SIZE}…")
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
            prev  = state.get(url, {})
            prev_sizes = prev.get("sizes", None)

            is_new         = prev_sizes is None
            size4_now      = TARGET_SIZE in sizes
            size4_before   = TARGET_SIZE in (prev_sizes or [])
            size4_appeared = size4_now and not size4_before

            if is_new and size4_now:
                alerts.append({"name": name, "url": url, "sizes": sizes})
                print(f"[NEW+SIZE4] {name} — sizes in stock: {sizes}")
            elif is_new:
                print(f"[NEW] {name} — sizes in stock: {sizes} (no size {TARGET_SIZE})")
            elif size4_appeared:
                alerts.append({"name": name, "url": url, "sizes": sizes})
                print(f"[SIZE4 APPEARED] {name}")
            else:
                status = f"✓ size {TARGET_SIZE} in stock" if size4_now else f"no size {TARGET_SIZE}"
                print(f"[OK] {name} — {status}")

            state[url] = {"name": name, "sizes": sizes}

        browser.close()

    save_state(state)

    if alerts:
        lines = [f"• {a['name']} (sizes: {', '.join(a['sizes'])})\n  {a['url']}" for a in alerts]
        body = "\n\n".join(lines)
        subject = f"COMOLI: {len(alerts)} item(s) with size {TARGET_SIZE} in stock"
        notify(subject, body, alerts[0]["url"])
    else:
        print(f"[DONE] No new size-{TARGET_SIZE} alerts this run.")


if __name__ == "__main__":
    run()
