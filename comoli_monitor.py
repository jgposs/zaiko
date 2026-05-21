#!/usr/bin/env python3
"""
COMOLI Size Monitor (Playwright edition)
=========================================
Uses a headless browser to fully render each product page,
then checks computed CSS styles to detect struck-through (sold-out) sizes.

HOW TO USE
----------
1. Install dependencies:
   pip install playwright
   playwright install chromium

2. Set your notification method in the CONFIG section below.

3. Run manually:
   python comoli_monitor.py

4. Schedule via GitHub Actions — see GITHUB_ACTIONS_SETUP.yml
"""

import json
import os
import re
import smtplib
import time
from email.mime.text import MIMEText
from pathlib import Path

from playwright.sync_api import sync_playwright

# ─────────────────────────────────────────────
#  CONFIG — edit these
# ─────────────────────────────────────────────

TARGET_SIZE = "4"
BASE_URL    = "https://www.comoli.jp"
MAILORDER   = f"{BASE_URL}/mailorder"

STATE_FILE = Path(__file__).parent / "comoli_state.json"

# ── Notification method ──────────────────────
NOTIFY_EMAIL    = True
NOTIFY_PUSHOVER = False
NOTIFY_CONSOLE  = False

# ── Email settings ───────────────────────────
EMAIL_SENDER    = "your.gmail@gmail.com"
EMAIL_PASSWORD  = "your-app-password"
EMAIL_RECIPIENT = "your.email@example.com"

# ── Pushover settings ────────────────────────
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
    Render the product page and return sizes that are in stock
    (i.e. visible but NOT struck through / crossed out).

    Comoli marks sold-out sizes with CSS text-decoration: line-through.
    We use Playwright's computed style API to detect this.
    """
    try:
        page.goto(product_url, wait_until="networkidle", timeout=30000)
    except Exception as e:
        print(f"[WARN] Could not load {product_url}: {e}")
        return []

    # Find all elements whose text is purely a number (the size labels)
    # Comoli renders sizes as standalone text nodes like "2", "3", "4"
    size_elements = page.query_selector_all("*")

    in_stock = []

    for el in size_elements:
        try:
            text = el.inner_text().strip()
        except Exception:
            continue

        # Only look at elements that are just a single digit (the size number)
        if not re.match(r"^\d$", text):
            continue
        if int(text) < 1 or int(text) > 5:
            continue

        # Check computed text-decoration for line-through
        try:
            decoration = el.evaluate(
                "el => window.getComputedStyle(el).textDecorationLine"
            )
        except Exception:
            decoration = ""

        if "line-through" in (decoration or ""):
            print(f"  [SOLD OUT] size {text}")
        else:
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

def send_email(subject: str, body: str):
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"]    = EMAIL_SENDER
    msg["To"]      = EMAIL_RECIPIENT
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(EMAIL_SENDER, EMAIL_PASSWORD)
            smtp.sendmail(EMAIL_SENDER, EMAIL_RECIPIENT, msg.as_string())
        print(f"[EMAIL] Sent: {subject}")
    except Exception as e:
        print(f"[EMAIL ERROR] {e}")


def send_pushover(title: str, message: str, url: str = ""):
    import urllib.request, urllib.parse
    try:
        data = urllib.parse.urlencode({
            "token":     PUSHOVER_API_TOKEN,
            "user":      PUSHOVER_USER_KEY,
            "title":     title,
            "message":   message,
            "url":       url,
            "url_title": "View on COMOLI",
        }).encode()
        urllib.request.urlopen("https://api.pushover.net/1/messages.json", data, timeout=10)
        print(f"[PUSHOVER] Sent: {title}")
    except Exception as e:
        print(f"[PUSHOVER ERROR] {e}")


def notify(subject: str, body: str, product_url: str = ""):
    if NOTIFY_CONSOLE:
        print(f"\n{'='*50}\n{subject}\n{body}\n{'='*50}")
    if NOTIFY_EMAIL:
        send_email(f"🇯🇵 COMOLI: {subject}", body)
    if NOTIFY_PUSHOVER:
        send_pushover(f"COMOLI: {subject}", body, product_url)


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

            is_new        = prev_sizes is None
            size4_now     = TARGET_SIZE in sizes
            size4_before  = TARGET_SIZE in (prev_sizes or [])
            size4_appeared = size4_now and not size4_before

            if is_new and size4_now:
                alerts.append({
                    "reason": "New item with your size!",
                    "name": name,
                    "url": url,
                    "sizes": sizes,
                })
                print(f"[NEW+SIZE4] {name} — sizes in stock: {sizes}")
            elif is_new:
                print(f"[NEW] {name} — sizes in stock: {sizes} (no size {TARGET_SIZE})")
            elif size4_appeared:
                alerts.append({
                    "reason": f"Size {TARGET_SIZE} back in stock!",
                    "name": name,
                    "url": url,
                    "sizes": sizes,
                })
                print(f"[SIZE4 APPEARED] {name}")
            else:
                status = f"✓ size {TARGET_SIZE} in stock" if size4_now else f"no size {TARGET_SIZE}"
                print(f"[OK] {name} — {status}")

            state[url] = {"name": name, "sizes": sizes}

        browser.close()

    save_state(state)

    if alerts:
        lines = []
        for a in alerts:
            lines.append(
                f"📦 {a['reason']}\n"
                f"   {a['name']}\n"
                f"   Sizes in stock: {', '.join(a['sizes'])}\n"
                f"   {a['url']}\n"
            )
        body = "\n".join(lines) + f"\nShop: {MAILORDER}"
        subject = f"{len(alerts)} item(s) with size {TARGET_SIZE} in stock at COMOLI"
        notify(subject, body, alerts[0]["url"])
    else:
        print(f"[DONE] No new size-{TARGET_SIZE} alerts this run.")


if __name__ == "__main__":
    run()
