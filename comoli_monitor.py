#!/usr/bin/env python3
"""
COMOLI Size Monitor
===================
Scrapes comoli.jp/mailorder and notifies you when size 4 is available
on any item — or when new items appear.

HOW TO USE
----------
1. Install dependencies:
   pip install requests beautifulsoup4

2. Set your notification method in the CONFIG section below.
   Option A: Email via Gmail (recommended, free)
   Option B: Pushover app push notification (great for phone alerts)
   Option C: Just print to console / log file

3. Run manually:
   python comoli_monitor.py

4. Schedule it (runs every 30 minutes):
   - Mac/Linux:  crontab -e  →  add:  */30 * * * * /usr/bin/python3 /path/to/comoli_monitor.py
   - Windows:    Use Task Scheduler
   - Free cloud: See GITHUB_ACTIONS_SETUP.md (also in this folder)

STATE FILE
----------
The script saves seen products to comoli_state.json so it only
notifies you about NEW items or size-4 changes since last run.
"""

import json
import os
import re
import smtplib
import time
from email.mime.text import MIMEText
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# ─────────────────────────────────────────────
#  CONFIG — edit these
# ─────────────────────────────────────────────

TARGET_SIZE = "4"          # The size you want
BASE_URL    = "https://www.comoli.jp"
MAILORDER   = f"{BASE_URL}/mailorder"

# State file: tracks what you've already been notified about
STATE_FILE = Path(__file__).parent / "comoli_state.json"

# ── Notification method ──────────────────────
# Set exactly ONE of these to True.

NOTIFY_EMAIL    = True   # Send email via Gmail SMTP
NOTIFY_PUSHOVER = False  # Send push via Pushover app
NOTIFY_CONSOLE  = False  # Just print (useful for testing)

# ── Email settings (if NOTIFY_EMAIL = True) ──
EMAIL_SENDER   = "your.gmail@gmail.com"
EMAIL_PASSWORD = "your-app-password"    # Use a Gmail App Password, not your real password
                                        # https://myaccount.google.com/apppasswords
EMAIL_RECIPIENT = "your.email@example.com"

# ── Pushover settings (if NOTIFY_PUSHOVER = True) ──
PUSHOVER_USER_KEY = "your-pushover-user-key"
PUSHOVER_API_TOKEN = "your-pushover-api-token"

# ─────────────────────────────────────────────
#  SCRAPING
# ─────────────────────────────────────────────

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}


def fetch(url: str) -> BeautifulSoup | None:
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        r.encoding = "utf-8"
        return BeautifulSoup(r.text, "html.parser")
    except Exception as e:
        print(f"[WARN] Could not fetch {url}: {e}")
        return None


def get_product_links() -> list[dict]:
    """Return list of {name, url} from the mailorder listing page."""
    soup = fetch(MAILORDER)
    if not soup:
        return []
    products = []
    for a in soup.select("a[href*='/mailorder/']"):
        href = a.get("href", "")
        if href == "/mailorder" or not href.startswith("/mailorder/"):
            continue
        name_parts = [span.get_text(strip=True) for span in a.find_all(["span", "p"])]
        name = " ".join(name_parts).strip() or a.get_text(strip=True)
        url = BASE_URL + href if href.startswith("/") else href
        if url not in [p["url"] for p in products]:
            products.append({"name": name or href.split("/")[-1], "url": url})
    return products


def get_available_sizes(product_url: str) -> list[str]:
    """
    Visit a product page and return the sizes listed.
    Tries multiple strategies to find sizes since Comoli is a Next.js
    site that may embed data in <script> tags rather than visible HTML.
    """
    soup = fetch(product_url)
    if not soup:
        return []

    # ── Strategy 1: Next.js __NEXT_DATA__ JSON ────────────────────────
    # Next.js embeds all page data as JSON in a <script id="__NEXT_DATA__"> tag
    next_data_tag = soup.find("script", {"id": "__NEXT_DATA__"})
    if next_data_tag and next_data_tag.string:
        try:
            data = json.loads(next_data_tag.string)
            raw = json.dumps(data)
            # Look for size arrays like ["1","2","3","4"] or size fields
            size_arrays = re.findall(r'"sizes?":\s*(\[[^\]]+\])', raw)
            for arr in size_arrays:
                sizes = re.findall(r'"(\d+)"', arr)
                if sizes:
                    return sizes
            # Also look for size near numbers 1-5 in the JSON
            size_match = re.search(r'"[Ss]ize":\s*"(\d+)"', raw)
            if size_match:
                return [size_match.group(1)]
        except Exception:
            pass

    # ── Strategy 2: Any <script> tag containing size data ────────────
    for script in soup.find_all("script"):
        if not script.string:
            continue
        text = script.string
        if "SIZE" not in text.upper():
            continue
        try:
            # Try parsing as JSON if it looks like a data blob
            data = json.loads(text)
            raw = json.dumps(data)
            sizes = re.findall(r'"(\d)"', raw)
            sizes = [s for s in sizes if s in "12345"]
            if sizes:
                return list(dict.fromkeys(sizes))
        except Exception:
            pass
        # Plain text search within script
        size_match = re.search(r'SIZE["\s:,]+([1-5](?:["\s,/]+[1-5])*)', text)
        if size_match:
            return re.findall(r"[1-5]", size_match.group(1))

    # ── Strategy 3: Visible page text near "SIZE" ────────────────────
    text = soup.get_text(" ", strip=True)
    size_match = re.search(r"SIZE\s*([\d\s/]+)", text)
    if size_match:
        raw = size_match.group(1)
        sizes = re.findall(r"\d+", raw)
        # Filter to plausible clothing sizes (1-5 for Comoli)
        sizes = [s for s in sizes if 1 <= int(s) <= 5]
        if sizes:
            return sizes

    # ── Strategy 4: Look for a COLOR/SIZE table pattern ──────────────
    # Comoli pages list sizes like "2/ 3/ 4" after color names
    color_size_match = re.search(
        r"(?:BLACK|WHITE|GRAY|NAVY|CHARCOAL|BROWN|BEIGE)[^\d]*(\d[\d\s/]*)",
        text, re.IGNORECASE
    )
    if color_size_match:
        sizes = re.findall(r"\d+", color_size_match.group(1))
        sizes = [s for s in sizes if 1 <= int(s) <= 5]
        if sizes:
            return sizes

    return []


# ─────────────────────────────────────────────
#  STATE
# ─────────────────────────────────────────────

def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {}  # {product_url: {"name": ..., "sizes": [...]}}


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
    try:
        requests.post("https://api.pushover.net/1/messages.json", data={
            "token":   PUSHOVER_API_TOKEN,
            "user":    PUSHOVER_USER_KEY,
            "title":   title,
            "message": message,
            "url":     url,
            "url_title": "View on COMOLI",
        }, timeout=10)
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
    products = get_product_links()

    if not products:
        print("[WARN] No products found — site may have changed structure.")
        return

    # Debug: print raw HTML snippet of first product to help diagnose size parsing
    first = products[0] if products else None
    if first:
        soup = fetch(first["url"])
        if soup:
            # Show __NEXT_DATA__ presence
            nd = soup.find("script", {"id": "__NEXT_DATA__"})
            print(f"[DEBUG] __NEXT_DATA__ present: {nd is not None}")
            # Show a snippet of visible text around SIZE
            text = soup.get_text(" ", strip=True)
            idx = text.upper().find("SIZE")
            if idx >= 0:
                print(f"[DEBUG] Text around SIZE: ...{text[max(0,idx-30):idx+80]}...")
            else:
                print(f"[DEBUG] 'SIZE' not found in visible text")
            # Show script count
            scripts = soup.find_all("script")
            print(f"[DEBUG] Script tags found: {len(scripts)}")

    alerts = []

    for product in products:
        url  = product["url"]
        name = product["name"]
        time.sleep(1)  # be polite to the server

        sizes = get_available_sizes(url)
        prev  = state.get(url, {})
        prev_sizes = prev.get("sizes", None)

        is_new         = prev_sizes is None
        size4_now      = TARGET_SIZE in sizes
        size4_before   = TARGET_SIZE in (prev_sizes or [])
        size4_appeared = size4_now and not size4_before

        if is_new and size4_now:
            alerts.append({
                "reason": "New item with your size!",
                "name": name,
                "url": url,
                "sizes": sizes,
            })
            print(f"[NEW+SIZE4] {name}")
        elif is_new:
            print(f"[NEW] {name} — sizes: {sizes} (no size {TARGET_SIZE})")
        elif size4_appeared:
            alerts.append({
                "reason": f"Size {TARGET_SIZE} now available!",
                "name": name,
                "url": url,
                "sizes": sizes,
            })
            print(f"[SIZE4 APPEARED] {name}")
        else:
            status = f"✓ size {TARGET_SIZE}" if size4_now else f"no size {TARGET_SIZE}"
            print(f"[OK] {name} — {status}")

        # Update state
        state[url] = {"name": name, "sizes": sizes}

    save_state(state)

    if alerts:
        lines = []
        for a in alerts:
            lines.append(
                f"📦 {a['reason']}\n"
                f"   {a['name']}\n"
                f"   Sizes available: {', '.join(a['sizes'])}\n"
                f"   {a['url']}\n"
            )
        body = "\n".join(lines) + f"\nShop: {MAILORDER}"
        subject = f"{len(alerts)} item(s) with size {TARGET_SIZE} at COMOLI"
        notify(subject, body, alerts[0]["url"])
    else:
        print(f"[DONE] No new size-{TARGET_SIZE} alerts this run.")


if __name__ == "__main__":
    run()
