"""Graphpaper — eng.graphpaper-tokyo.com (English/international store)

This is a Shopify storefront, which is a gift: `products.json` reports every
variant's stock as a boolean, so there is no HTML to parse and no per-product
request. The whole catalogue comes back in one or two calls.

Size lives in a named option whose position varies by product, so the option
list is read rather than assuming option2. Values are spelled with an
underscore on this store ("2_INT"); the engine's normalize_size folds that
together with "2-INT" and "2 int".
"""

from __future__ import annotations

import json
import time
from typing import Iterator

from .base import SiteAdapter, SiteLooksBroken, SiteUnavailable, Stock

PAGE_SIZE = 250          # Shopify's maximum
MAX_PAGES = 20           # backstop against a pagination bug looping forever

# Shops rename this option; match generously rather than on one exact string.
# Guessing wrong used to record the colour as the size, which matches no
# target and so failed silently and permanently.
SIZE_OPTION_NAMES = ("size", "sizes", "サイズ", "サイズ名")


class Graphpaper(SiteAdapter):
    key = "graphpaper"
    label = "Graphpaper"
    base_url = "https://eng.graphpaper-tokyo.com"
    listing_url = "https://eng.graphpaper-tokyo.com/collections/mens-global"
    target_sizes = ("2", "2-INT")
    accept_language = "en,ja;q=0.8"

    # ── helpers ─────────────────────────────────────────────────
    @staticmethod
    def _size_option_position(product: dict) -> int | None:
        """Which optionN holds the size, from the product's own option list."""
        for option in product.get("options") or []:
            name = str(option.get("name", "")).strip().lower()
            if not any(candidate in name for candidate in SIZE_OPTION_NAMES):
                continue
            position = option.get("position")
            try:
                position = int(position)
            except (TypeError, ValueError):
                continue
            if 1 <= position <= 3:
                return position
        return None

    def _stock_for(self, product: dict) -> Stock:
        handle = str(product.get("handle") or "").strip()
        name = product.get("title") or handle or "(unnamed product)"
        url = f"{self.base_url}/products/{handle}" if handle else ""

        if "variants" not in product or not handle:
            # Shape we don't recognise. Unknown, not empty.
            return Stock(name=name, url=url, sizes=None)

        position = self._size_option_position(product)
        if position is None:
            # No option we can identify as the size. Reporting [] here would
            # read as "sold out in every size" and, if the store ever renames
            # the option, would silence the whole catalogue with no alarm.
            return Stock(name=name, url=url, sizes=None)

        sizes = [
            str(variant.get(f"option{position}", "")).strip()
            for variant in product.get("variants") or []
            if variant.get("available")
        ]
        return Stock(name=name, url=url, sizes=[s for s in sizes if s])

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
