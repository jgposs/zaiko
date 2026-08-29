"""KENT — wearkent.com

A targeted watch rather than a catalogue crawl: a named list of
(product, colour) pairs, currently one. The store is Shopify, so the paging
and feed validation come from ShopifyCollectionAdapter; what is specific here
is that a product is not the unit being watched.

Two things about this shop that the obvious implementation gets wrong:

**Colour is half the identity.** Products carry Size *and* Color options, and
`target_sizes` only knows about one dimension. Reading sizes across all
colours would report "M is in stock" the day Sand/M returns, fire an alert for
a colour that wasn't wanted, and — far worse — record M as available, so when
Charcoal Black/M finally restocks there is no change to detect and nothing is
sent. Silence on the one thing being waited for. Each colour is therefore its
own Stock with its own state entry, exactly as COMOLI's per-colour URLs are.

**The endpoint that matches the product URL is a trap.** `/products/<handle>
.json` returns variants with no `available` key at all, so every variant reads
as unavailable, every product reports `[]`, and the site goes quiet forever
with nothing to alarm on. The root `/products.json` carries `available`
properly and is what the base class reads. Verified against the live store,
August 2026; don't "simplify" this to the per-product endpoint.
"""

from __future__ import annotations

from typing import Iterator

from .base import Stock
from .shopify import SIZE_OPTION_NAMES, ShopifyCollectionAdapter

COLOUR_OPTION_NAMES = ("color", "colour", "color name", "カラー")


def _option_position(product: dict, candidates: tuple[str, ...]) -> int | None:
    """Which optionN holds this attribute, from the product's own option list."""
    for option in product.get("options") or []:
        if not isinstance(option, dict):
            continue
        name = str(option.get("name", "")).strip().lower()
        if not any(c in name for c in candidates):
            continue
        try:
            position = int(option.get("position"))
        except (TypeError, ValueError):
            continue
        if 1 <= position <= 3:
            return position
    return None


def _values_of(product: dict, position: int) -> list[str]:
    """The declared values of the option at `position`, as the shop spells them."""
    for option in product.get("options") or []:
        if isinstance(option, dict) and option.get("position") == position:
            return [str(v) for v in (option.get("values") or [])]
    return []


class Kent(ShopifyCollectionAdapter):
    key = "kent"
    label = "KENT"
    base_url = "https://www.wearkent.com"
    # The whole-store feed, not a collection: 31 products in one request.
    listing_url = "https://www.wearkent.com"
    target_sizes = ("M",)
    accept_language = "en"

    # (product handle, colour exactly as the shop spells it). Add a line to
    # watch another; anything not listed here is read past and not tracked.
    watching: tuple[tuple[str, str], ...] = (
        ("mens-organic-cotton-classic-boxer", "Charcoal Black"),
    )

    def _stocks_for(self, product: dict) -> Iterator[Stock]:
        handle = str(product.get("handle") or "").strip()
        colours = [c for h, c in self.watching if h == handle]
        if not colours:
            return                          # not something we watch

        title = product.get("title") or handle
        size_pos = _option_position(product, SIZE_OPTION_NAMES)
        colour_pos = _option_position(product, COLOUR_OPTION_NAMES)

        for colour in colours:
            # One entry per colour, so a restock in one can never be recorded
            # as, or masked by, stock in another.
            name = f"{title} ({colour})"
            url = f"{self.base_url}/products/{handle}#{colour.replace(' ', '-')}"

            if size_pos is None or colour_pos is None or "variants" not in product:
                # The shape changed under us. Unknown, never "sold out".
                yield Stock(name=name, url=url, sizes=None)
                continue

            declared = [v.strip().casefold() for v in _values_of(product, colour_pos)]
            if colour.casefold() not in declared:
                # The colour we watch is not among the ones this product
                # offers any more — renamed, or dropped. Reporting [] would
                # say "sold out in every size" about something that no longer
                # exists under that name, and we would wait forever on a
                # colour the shop stopped using.
                yield Stock(name=name, url=url, sizes=None)
                continue

            sizes = []
            unreadable = False
            for variant in product.get("variants") or []:
                if not isinstance(variant, dict) or not variant.get("available"):
                    continue
                if str(variant.get(f"option{colour_pos}") or "").strip().casefold() \
                        != colour.casefold():
                    continue                # a different colour's stock

                raw = variant.get(f"option{size_pos}")
                if raw is None or not str(raw).strip():
                    unreadable = True       # see the note in shopify.py
                    break
                sizes.append(str(raw).strip())

            # An empty list here is a real answer — this colour is sold out in
            # every size — and must stay distinct from the unknowns above.
            yield Stock(name=name, url=url, sizes=None if unreadable else sizes)
