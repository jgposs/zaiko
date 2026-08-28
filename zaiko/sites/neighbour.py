"""COMOLI at Neighbour — shopneighbour.com

A second shop for a brand already watched at its own store. COMOLI's own
mailorder and its stockists get separate size runs and sell through at
different rates, so this is registered as its own site with its own state
rather than folded into `comoli`: the same garment in two shops is two
independent answers to "is it in stock in a 4?", and merging them would let
one shop's sold-out mask the other's restock.

Shopify, so the reading is inherited whole. The only things specific to this
store are below.
"""

from __future__ import annotations

from .shopify import ShopifyCollectionAdapter

STORE = "https://www.shopneighbour.com"
# The store serves per-market URLs and the products.json feed only answers
# under one. Kept as a constant because both the feed and the product links
# need it, and a link missing it lands on a redirect rather than the product.
MARKET = "/en-us"


class Neighbour(ShopifyCollectionAdapter):
    key = "neighbour-comoli"
    # The push title has to say which shop, or a restock at Neighbour reads
    # like one at comoli.jp and sends you to the wrong site.
    label = "COMOLI (Neighbour)"
    base_url = STORE
    # One brand's collection, not the whole store: the shop carries dozens of
    # labels whose size 4 means something entirely different.
    listing_url = f"{STORE}{MARKET}/collections/comoli-mens"
    product_url_prefix = f"{STORE}{MARKET}/products"
    target_sizes = ("4", "5")
    accept_language = "en"

    # Notes on this feed, from reading it rather than assuming:
    #
    # Every product carries a single option named "Size", and every vendor in
    # the collection is COMOLI, so there is no filtering to do. If the shop
    # ever drops another label into it we would alert on a garment that isn't
    # COMOLI — noisy, but the failure points the right way: something extra,
    # not silence.
    #
    # Footwear sits in the same collection on the JP scale (27, 27.5, 28) and
    # a few pieces are "Free Size". Neither can collide with a target of 4 or
    # 5, so the two scales coexist safely here — worth re-checking if this
    # adapter is ever pointed at a collection with a 4–5 shoe size in it.
    #
    # The mens collection is what's watched; the shop also has comoli-womens,
    # which is deliberately not registered.
