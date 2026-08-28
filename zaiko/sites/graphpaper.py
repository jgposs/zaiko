"""Graphpaper — eng.graphpaper-tokyo.com (English/international store)

A Shopify storefront, so everything mechanical lives in ShopifyCollectionAdapter
and this file is just the identity. Sizes are spelled with an underscore on this
store ("2_INT"); the engine's normalize_size folds that together with "2-INT"
and "2 int", so target_sizes can be written the obvious way.

Garments run 1–3 here and footwear runs 7.5–11, two scales in one feed. A
target of "2" is unambiguous today, but that is luck rather than design — see
the note in docs/ADDING_A_BRAND.md.
"""

from __future__ import annotations

from .shopify import ShopifyCollectionAdapter


class Graphpaper(ShopifyCollectionAdapter):
    key = "graphpaper"
    label = "Graphpaper"
    base_url = "https://eng.graphpaper-tokyo.com"
    listing_url = "https://eng.graphpaper-tokyo.com/collections/mens-global"
    target_sizes = ("2", "2-INT")
    accept_language = "en,ja;q=0.8"
