"""Graphpaper — eng.graphpaper-tokyo.com (English/international store)

This is a Shopify storefront, which is a gift: `products.json` reports every
variant's stock as a boolean, so there is no HTML to parse and no per-product
request. The whole catalogue comes back in one or two calls.

Size lives in a named "Size" option whose position varies by product, so the
option list is read rather than assuming option2. Values are spelled with an
underscore on this store ("2_INT"); normalize_size folds that together with
"2-INT" and "2 int" so target_sizes can be written the obvious way.
"""

import json
import time
from typing import Iterator

from .base import (SiteAdapter, SiteLooksBroken, SiteUnavailable, Stock,
                   normalize_size)

PAGE_SIZE = 250          # Shopify's maximum
MAX_PAGES = 20           # backstop against a pagination bug looping forever


class Graphpaper(SiteAdapter):
    key = "graphpaper"
    label = "Graphpaper"
    base_url = "https://eng.graphpaper-tokyo.com"
    listing_url = "https://eng.graphpaper-tokyo.com/collections/mens-global"
    target_sizes = ("2", "2-INT")
    request_delay = 0.4
    accept_language = "en,ja;q=0.8"

    # ── helpers ─────────────────────────────────────────────────
    @staticmethod
    def _size_option_position(product: dict) -> int | None:
        """Which optionN holds the size, from the product's own option list."""
        for option in product.get("options") or []:
            if str(option.get("name", "")).strip().lower() == "size":
                position = option.get("position")
                if isinstance(position, int) and 1 <= position <= 3:
                    return position
        return None

    def _stock_for(self, product: dict) -> Stock:
        handle = product.get("handle") or ""
        name = product.get("title") or handle
        url = f"{self.base_url}/products/{handle}"

        position = self._size_option_position(product)
        sizes: list[str] = []
        for variant in product.get("variants") or []:
            if not variant.get("available"):
                continue
            raw = variant.get(f"option{position}") if position else None
            if raw is None:
                # No named Size option (accessories, one-size goods). Fall back
                # to the variant title's last segment, which is where Shopify
                # puts the final option. Split on the " / " separator Shopify
                # joins with, not a bare slash — sizes like "O/S" contain one.
                title = variant.get("title") or ""
                raw = title.split(" / ")[-1] if title else ""
            raw = str(raw).strip()
            if raw:
                sizes.append(normalize_size(raw))

        return Stock(name=name, url=url, sizes=list(dict.fromkeys(sizes)))

    # ── the engine's entry point ────────────────────────────────
    def collect(self, session, fetch) -> Iterator[Stock]:
        total = 0

        for page in range(1, MAX_PAGES + 1):
            url = (f"{self.listing_url}/products.json"
                   f"?limit={PAGE_SIZE}&page={page}")
            raw = fetch(session, url)

            if raw is None:
                if page == 1:
                    raise SiteUnavailable("products.json failed on every attempt")
                # Later pages failing is a partial read, not a dead site. Stop
                # here; everything already yielded still counts, and the
                # products we never reached simply keep their previous state.
                print(f"[WARN] {self.label}: page {page} failed; "
                      f"stopping after {total} products.")
                break

            try:
                data = json.loads(raw)
            except json.JSONDecodeError as e:
                raise SiteLooksBroken(f"products.json was not valid JSON ({e})")

            if not isinstance(data, dict) or "products" not in data:
                raise SiteLooksBroken("products.json had an unexpected shape")

            products = data.get("products") or []
            if not products:
                break

            for product in products:
                total += 1
                yield self._stock_for(product)

            if len(products) < PAGE_SIZE:
                break
            time.sleep(self.request_delay)

        if total == 0:
            raise SiteLooksBroken("products.json returned no products at all")

        print(f"[INFO] {self.label}: read {total} products.")
