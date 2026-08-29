"""Shopify storefronts that expose a collection's `products.json`.

The shared half of what used to live in `graphpaper.py`. A Shopify shop hands
back every variant with an `available` boolean, so there is no HTML to parse
and no per-product request — the whole collection arrives in one or two calls.
Two of the three sites watched here turned out to be Shopify, which is why
this is a base class rather than a copy.

A subclass supplies identity (`key`, `label`, `base_url`, `listing_url`,
`target_sizes`) and nothing else, unless the store's product pages don't sit
at `base_url/products/<handle>` — see `product_url_prefix`.

What this class refuses to do is guess. It returns `None` — 'could not read
it' — for any product whose shape it doesn't recognise, because the
alternative, an empty list, is a claim that the product is sold out in every
size, and that produces a fake restock the day the misreading stops.
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
# target, so the brand would have gone silent forever with nothing to notice.
SIZE_OPTION_NAMES = ("size", "sizes", "サイズ", "サイズ名")


class ShopifyCollectionAdapter(SiteAdapter):
    """Reads `<listing_url>/products.json`, paging until the shop runs out."""

    # Where product pages live, if not `base_url` + "/products". A store with
    # per-market URLs serves them under a prefix ("/en-us/products/<handle>")
    # and the bare form redirects at best — and a push whose link doesn't open
    # the thing it is announcing is barely a push at all.
    product_url_prefix: str = ""

    def product_url(self, handle: str) -> str:
        prefix = self.product_url_prefix or f"{self.base_url}/products"
        return f"{prefix}/{handle}"

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
        url = self.product_url(handle) if handle else ""

        if "variants" not in product or not handle:
            # Shape we don't recognise. Unknown, not empty.
            return Stock(name=name, url=url, sizes=None)

        position = self._size_option_position(product)
        if position is None:
            # No option we can identify as the size. Reporting [] here would
            # read as "sold out in every size" and, if the store ever renames
            # the option, would silence the whole catalogue with no alarm.
            return Stock(name=name, url=url, sizes=None)

        sizes = []
        for variant in product.get("variants") or []:
            if not variant.get("available"):
                continue                    # sold out; its size doesn't matter

            raw = variant.get(f"option{position}")
            if raw is None or not str(raw).strip():
                # The options list promised the size at this position and this
                # variant hasn't got one. Shopify sends every variant all three
                # option slots and nulls the unused ones, so `.get(..., "")`
                # returns None here rather than the default, and str() would
                # turn it into the literal "None" — a size that normalises to
                # NONE, matches no target, and reads to the engine as a
                # confident "in stock, nothing in your size". That is a silent
                # miss on a garment that may well be sitting there in a 4.
                # An available variant whose size we cannot name means we
                # cannot answer for this product at all.
                return Stock(name=name, url=url, sizes=None)

            sizes.append(str(raw).strip())

        # No available variants is a real answer — sold out in every size —
        # and must stay [] rather than becoming unknown.
        return Stock(name=name, url=url, sizes=sizes)

    def _stocks_for(self, product: dict) -> Iterator[Stock]:
        """The Stocks one feed product becomes. One, by default.

        The extension point for a shop where a product is not the unit you
        watch — a garment sold in four colours is four separate answers to
        "is it in stock in an M?", and collapsing them lets one colour's
        restock mask another's. Overriding this is also how a subclass
        watches only part of a catalogue: yield nothing for a product and it
        is simply not tracked.
        """
        yield self._stock_for(product)

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
                yield from self._stocks_for(product)

            if len(products) < PAGE_SIZE:
                break
            time.sleep(self.request_delay)

        if total == 0:
            # Not "the collection is empty" — a collection that is renamed,
            # unpublished or deleted answers HTTP 200 with an empty product
            # list rather than a 404, so this is the only signal that the feed
            # we are watching has stopped existing. Passing it through as
            # "nothing in stock anywhere" would mark the entire brand sold out
            # and then go quiet forever.
            raise SiteLooksBroken("products.json returned no products at all")

        print(f"[INFO] {self.label}: read {total} products.")
