"""COMOLI — comoli.jp/mailorder

The product pages are server-rendered, so plain HTTP plus an HTML parser is
enough; no headless browser, and a full run takes seconds instead of minutes.
Sold-out sizes are marked with <span class="td_line-through">.
"""

from __future__ import annotations

import re
import time
from typing import Iterator

from bs4 import BeautifulSoup, Tag

from .base import (Product, SiteAdapter, SiteLooksBroken, SiteUnavailable,
                   Stock, normalize_size)

# A <p> is treated as a size row only if its whole text is digits/slashes.
SIZE_ROW_RE = re.compile(r"^[\d\s/]+$")
SIZE_TOKEN_RE = re.compile(r"^[0-6]$")

# Listing links that are not products.
NOT_PRODUCTS = {"form", "guide", "about", "faq", "law", "privacy", "contact"}


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


class Comoli(SiteAdapter):
    key = "comoli"
    label = "COMOLI"
    base_url = "https://www.comoli.jp"
    listing_url = "https://www.comoli.jp/mailorder"
    target_sizes = ("4", "5")

    def parse_product_links(self, html: str) -> list[Product]:
        soup = BeautifulSoup(html, "html.parser")
        products: list[Product] = []
        by_url: dict[str, str] = {}
        order: list[str] = []

        for a in soup.find_all("a", href=True):
            href = a["href"].split("?")[0].split("#")[0].rstrip("/")
            if href.startswith(self.base_url):
                href = href[len(self.base_url):]      # absolute links count too
            if not href.startswith("/mailorder/"):
                continue
            slug = href[len("/mailorder/"):]
            if not slug or slug.split("/")[0] in NOT_PRODUCTS:
                continue

            url = self.base_url + href
            name = a.get_text(" ", strip=True) or slug

            if url in by_url:
                # Same product linked twice (image link + text link). Keep the
                # more descriptive label for the notification.
                if len(name) > len(by_url[url]):
                    by_url[url] = name
                continue

            by_url[url] = name
            order.append(url)

        products = [Product(name=by_url[u], url=u) for u in order]
        return products

    def parse_available_sizes(self, html: str) -> list[str]:
        """Sizes live in <p> elements, one row per colour. Each size is either
        a bare text node (in stock) or wrapped in <span class="td_line-through">
        (sold out). Each size token is checked individually rather than the <p>
        as a whole, so this is correct whether the site puts one size per <p> or
        several in the same <p> — and a size sold out in one colour but live in
        another is still reported as available.
        """
        soup = BeautifulSoup(html, "html.parser")
        in_stock: list[str] = []
        sold_out: list[str] = []

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
            sizes = [normalize_size(s) for s in self.parse_available_sizes(page)]
            yield Stock(product.name, product.url, sizes)
            time.sleep(self.request_delay)

    def display_name(self, name: str) -> str:
        return name.split("COLOR")[0].strip() or name
