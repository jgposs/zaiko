# Adding a brand

One file in `zaiko/sites/`, one line in the registry. The engine handles state,
change detection, alerting and every failure alarm — so the adapter's only job
is to answer, honestly, "what is in stock right now?"

## The shape

```python
# zaiko/sites/yourbrand.py
from __future__ import annotations           # required: we support Python 3.9

from typing import Iterator

from .base import SiteAdapter, SiteLooksBroken, SiteUnavailable, Stock


class YourBrand(SiteAdapter):
    key = "yourbrand"                        # state.json is keyed by this
    label = "YOUR BRAND"                     # appears in push titles
    base_url = "https://yourbrand.jp"
    listing_url = "https://yourbrand.jp/shop"
    target_sizes = ("M", "L")                # per brand, spelled naturally

    def collect(self, session, fetch) -> Iterator[Stock]:
        listing = fetch(session, self.listing_url)
        if listing is None:
            raise SiteUnavailable("listing failed to load")

        products = ...                       # parse however the site needs
        if not products:
            raise SiteLooksBroken("no products found on the listing")

        for name, url in products:
            page = fetch(session, url)
            if page is None:
                yield Stock(name, url, None)          # unknown, not sold out
                continue
            yield Stock(name, url, sizes_in_stock(page))
```

Register it:

```python
# zaiko/sites/__init__.py
from .yourbrand import YourBrand

ADAPTERS = {a.key: a for a in (Comoli(), Graphpaper(), Neighbour(), YourBrand())}
```

## Check for a JSON feed first

Before writing a single HTML parser, try:

```
curl -s "https://theshop.com/collections/<collection>/products.json?limit=250" | head -c 500
```

If that returns JSON, the shop is Shopify and you are done thinking about
markup: every variant reports `available` as a boolean, and the whole catalogue
arrives in one or two requests instead of one per product. A surprising number
of small brand shops are Shopify — two of the three watched here are.

When it is, **don't write an adapter at all**: subclass
`ShopifyCollectionAdapter` (`sites/shopify.py`), which already does the paging,
the size-option hunting and the three-valued answer. `graphpaper.py` and
`neighbour.py` are both a docstring and six class attributes. Set
`product_url_prefix` if the store serves products under a market prefix
(`/en-us/products/…`) rather than at `base_url/products`.

## The same brand in a second shop

A stockist that carries a brand you already watch is a **separate site key**,
not an extra URL inside the existing adapter. Its state, its failure alarms and
its push title stay its own — merging them would let one shop's sold-out mask
the other's restock, which is the silence this project exists to prevent. Give
the label the shop's name (`COMOLI (Neighbour)`), or an alert tells you nothing
about where to buy it.

Other things worth probing before committing to scraping: `/products.json`
at the site root, a `sitemap.xml`, or an XHR the product page itself makes
(open the network tab — if the size selector is populated by fetch, that
endpoint is your feed).

## The traps, all of which have bitten this repo

**Return `None`, not `[]`, whenever you are unsure.** A page that returns HTTP
200 with no size markup — a soft 404, an interstitial, a redesign — is *not*
"sold out". Comoli got this wrong and would have fired a fake restock for every
product the day the site recovered.

**Prove you can tell "sold out" from "unreadable".** If your parser returns an
empty list for both, you cannot. Comoli's returns `(in_stock, sold_out)` and
treats *both* being empty as unreadable. Find the equivalent signal on your site
before you write `collect`.

**Don't guess which field holds the size.** Graphpaper's Shopify feed names it
in an options list whose position varies per product; matching the exact string
`"Size"` meant a renamed option made the *colour* the size — matching no target,
so the brand would have gone silent forever with nothing to notice it. Read the
site's own metadata, match generously, and return `None` when you can't identify
it.

**Watch for multiple values in one text node.** Comoli sometimes renders sizes
as `<p>4<span>/</span>5</p>` and sometimes as `<p>4 / 5</p>`. The parser handled
the first and silently returned nothing for the second — and the second is the
shape a row with *nothing sold out* takes, i.e. exactly the case you care about.
Split within text nodes.

**Normalise nothing yourself.** Yield sizes as the site spells them. The engine
folds case, spaces, underscores and hyphens. If you normalise in the adapter you
will eventually normalise differently from `target_sizes` and match nothing.

**`.get(key, default)` does not save you from a null.** A JSON feed that
carries a key with a null value returns the null, not your default — Shopify
sends every variant all three option slots and nulls the unused ones. Wrapping
that in `str()` produces the four-character string `"None"`, which is a size as
far as the rest of the code is concerned: it normalises, it matches no target,
and the product reads to the engine as a confident "in stock, nothing in your
size". Silence over something that may be sitting there in your size, from a
run that reports itself healthy. Check the value, not just the key, and return
`None` for the product when an *available* variant's size can't be named.

**A dead feed can answer 200.** Shopify serves a collection that has been
renamed, unpublished or deleted as HTTP 200 with `{"products":[]}` — not a 404.
Nothing about the response says the thing you are watching stopped existing, so
an adapter that trusts the status code records the brand as sold out in every
size and then goes quiet forever, looking healthy the whole time. The base
class treats "no products at all" as `SiteLooksBroken` for exactly this; if you
write your own `collect`, do the same. An empty *later* page is different — that
is just the end of the collection.

**Handle every link form.** Comoli's listing was parsed by string prefix, so
absolute, `http://`, and non-`www` links were silently dropped. Parse the host
and path properly.

**Sizes are per brand and don't share a namespace.** Graphpaper garments run
1–3 and its footwear runs 7.5–11. A target of `2` is unambiguous today, but two
scales in one adapter is a latent collision — worth a thought if you add a brand
where they overlap.

## Verifying it

The sandbox this repo is often edited from cannot reach the brand sites, so
live verification happens on your own machine:

```bash
python3 run.py --site yourbrand --dry-run
```

Sanity-check the product count against what the site actually shows, and open
two or three products to confirm the parsed sizes match. Then, because the new
brand has no history:

```bash
python3 run.py --site yourbrand --seed
```

which records current stock without notifying, so the first real run doesn't
announce the brand's entire catalogue.

## Tests

Both suites are plain scripts — no pytest:

```bash
python3 tests/test_parser.py     # parsing, offline
python3 tests/test_e2e.py        # full runs, network stubbed
```

Add parser tests for the shapes above (sold out, unreadable, multi-value nodes,
whatever your site's quirk is) and, if the adapter has interesting failure
behaviour, an e2e case.

Then **break your own fix and confirm the test fails.** Twice in this repo's
short life a test has passed for the wrong reason — see `docs/DECISIONS.md`.
A green suite means nothing until you've watched it go red.
