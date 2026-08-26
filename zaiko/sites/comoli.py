"""COMOLI — comoli.jp/mailorder

The product pages are server-rendered, so plain HTTP plus an HTML parser is
enough; no headless browser, and a full run takes seconds instead of minutes.
Sold-out sizes are marked with <span class="td_line-through">.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Iterator
from urllib.parse import urlsplit

from bs4 import BeautifulSoup, Tag

from .base import SiteAdapter, SiteLooksBroken, SiteUnavailable, Stock

# A <p> is treated as a size row only if its whole text is digits/slashes.
SIZE_ROW_RE = re.compile(r"^[\d\s/]+$")
SIZE_TOKEN_RE = re.compile(r"^[0-6]$")
# Sizes may share one text node ("4 / 5") or sit in separate ones, depending
# on where the sold-out <span>s fall. Split so both read the same.
TOKEN_SPLIT_RE = re.compile(r"[\s/]+")

# Listing links that are not products.
NOT_PRODUCTS = {"form", "guide", "about", "faq", "law", "privacy", "contact"}

PRODUCT_PREFIX = "/mailorder/"
HOSTS = {"comoli.jp", "www.comoli.jp"}


@dataclass(frozen=True)
class Product:
    """A product found on the listing page."""
    name: str
    url: str


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


def _product_path(href: str) -> str | None:
    """The /mailorder/<slug> path for a product link, or None if it isn't one.

    Handles relative links, protocol-relative ones, and both hosts on either
    scheme — a product only linked in an absolute form used to be skipped
    entirely, and nothing would have reported it missing.
    """
    parts = urlsplit(href)
    if parts.netloc and parts.netloc.lower() not in HOSTS:
        return None

    path = parts.path.rstrip("/")
    if not path.startswith(PRODUCT_PREFIX):
        return None

    slug = path[len(PRODUCT_PREFIX):]
    if not slug or slug.split("/")[0] in NOT_PRODUCTS:
        return None
    return path


class Comoli(SiteAdapter):
    key = "comoli"
    label = "COMOLI"
    base_url = "https://www.comoli.jp"
    listing_url = "https://www.comoli.jp/mailorder"
    target_sizes = ("4", "5")

    def parse_product_links(self, html: str) -> list[Product]:
        soup = BeautifulSoup(html, "html.parser")
        by_url: dict[str, str] = {}

        for a in soup.find_all("a", href=True):
            path = _product_path(a["href"].split("#")[0])
            if path is None:
                continue

            url = self.base_url + path
            name = a.get_text(" ", strip=True) or path[len(PRODUCT_PREFIX):]

            # Same product linked twice (image link + text link). Keep the
            # more descriptive label for the notification. Reassigning an
            # existing key preserves its original position.
            if len(name) > len(by_url.get(url, "")):
                by_url[url] = name

        return [Product(name=name, url=url) for url, name in by_url.items()]

    def parse_sizes(self, html: str) -> tuple[list[str], list[str]]:
        """(in stock, sold out) for one product page.

        Sizes live in <p> elements, one row per colour, each either a bare
        text node (in stock) or wrapped in <span class="td_line-through">
        (sold out). Tokens are checked individually rather than the <p> as a
        whole, so this is correct whether the site puts one size per <p> or
        several in the same one — and a size sold out in one colour but live
        in another is still reported as available.

        Both lists empty means no size markup was recognised at all, which
        the caller must treat as 'could not read', never as 'sold out'.
        """
        soup = BeautifulSoup(html, "html.parser")
        in_stock: list[str] = []
        sold_out: list[str] = []

        for p in soup.find_all("p"):
            row_text = p.get_text(" ", strip=True)
            if not row_text or not SIZE_ROW_RE.match(row_text):
                continue
            for node in p.find_all(string=True):
                bucket = sold_out if _inside_strikethrough(node, p) else in_stock
                for token in TOKEN_SPLIT_RE.split(node.strip()):
                    if SIZE_TOKEN_RE.match(token):
                        bucket.append(token)

        return list(dict.fromkeys(in_stock)), list(dict.fromkeys(sold_out))

    def parse_available_sizes(self, html: str) -> list[str]:
        """Just the in-stock sizes. Cannot distinguish sold out from
        unreadable — use parse_sizes for that."""
        return self.parse_sizes(html)[0]

    # ── the engine's entry point ────────────────────────────────
    def collect(self, session, fetch) -> Iterator[Stock]:
        html = fetch(session, self.listing_url)
        if html is None:
            raise SiteUnavailable("listing page failed to load on every attempt")

        products = self.parse_product_links(html)
        if not products:
            raise SiteLooksBroken("no product links found on the listing page")

        print(f"[INFO] {self.label}: found {len(products)} products. Checking each…")

        for product in products:
            page = fetch(session, product.url)
            if page is None:
                yield Stock(product.name, product.url, None)
                continue

            in_stock, sold_out = self.parse_sizes(page)
            if not in_stock and not sold_out:
                # HTTP 200, but nothing that looks like a size row: an
                # interstitial, a soft 404, a maintenance page, or a redesign.
                # Recording that as "sold out" would fire a fake restock the
                # day it recovers.
                print(f"[SKIP] {product.name} — no size markup on the page")
                yield Stock(product.name, product.url, None)
                continue

            for size in sold_out:
                if size not in in_stock:
                    print(f"  [SOLD OUT] size {size}")

            yield Stock(product.name, product.url, in_stock)
            time.sleep(self.request_delay)

    def display_name(self, name: str) -> str:
        # Word-anchored: a bare split on "COLOR" turns "TRICOLOR KNIT VEST"
        # into "TRI".
        return re.split(r"\bCOLOR\b", name, 1)[0].strip() or name
