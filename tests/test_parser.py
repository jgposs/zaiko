#!/usr/bin/env python3
"""Offline tests for parsing and message handling. No network."""

from _bootstrap import check, report

from zaiko.config import PUSHOVER_LIMIT
from zaiko.notify import chunk_alerts
from zaiko.sites import ADAPTERS, resolve
from zaiko.sites.base import normalize_size
from zaiko.sites.comoli import Comoli
from zaiko.sites.graphpaper import Graphpaper
from zaiko.sites.neighbour import Neighbour

comoli = Comoli()
sizes = comoli.parse_available_sizes
links = comoli.parse_product_links

# ── 1. Documented layout: one size per <p>, one row per colour ──────────
one_per_p = """
<div><p>BLACK</p>
  <p>2<span>/</span></p>
  <p>3<span>/</span></p>
  <p><span class="td_line-through">4</span></p>
</div>
"""
check("one size per <p>, 4 sold out", sizes(one_per_p), ["2", "3"])

# ── 2. Risk layout: every size inside a single <p> ──────────────────────
all_in_one_p = """
<p>1<span>/</span>2<span>/</span><span class="td_line-through">4</span></p>
"""
check("all sizes in one <p>", sizes(all_in_one_p), ["1", "2"])

# ── 3. Sizes sharing ONE text node, with no <span> to split them ────────
# A row with nothing sold out has the fewest span wrappers, so this is the
# most likely shape to meet in the wild — and it used to parse as nothing.
check("two sizes in one text node, slash-separated", sizes("<p>4 / 5</p>"), ["4", "5"])
check("no spaces around the slash", sizes("<p>4/5</p>"), ["4", "5"])
check("single size, bare text node", sizes("<p>4</p>"), ["4"])

# ── 4. Sold out in one colour, available in another ─────────────────────
two_colours = """
<p>BLACK</p>
<p><span class="td_line-through">4</span></p>
<p>WHITE</p>
<p>4<span>/</span></p>
"""
check("size 4 sold out in black, live in white", sizes(two_colours), ["4"])

# ── 5. Sold out vs unreadable — the distinction the whole design rests on ─
all_gone = '<p><span class="td_line-through">2</span></p><p><span class="td_line-through">4</span></p>'
check("everything sold out: in-stock empty", comoli.parse_sizes(all_gone)[0], [])
check("everything sold out: sold-out list populated",
      comoli.parse_sizes(all_gone)[1], ["2", "4"])

for label, page in [
    ("blank page", ""),
    ("interstitial", "<h1>Just a moment...</h1>"),
    ("spec table only", "<div><p>Cotton 100%</p><p>¥132,000</p><p>E03-06002</p></div>"),
    ("prices and dates only", "<p>132000</p><p>2026/08/22</p><p>05018</p>"),
]:
    check(f"unreadable ({label}) reports no size markup at all",
          comoli.parse_sizes(page), ([], []))

# ── 6. Nested markup around the digit ───────────────────────────────────
check("digit nested in <a><em>", sizes('<p><a href="#"><em>3</em></a><span>/</span></p>'), ["3"])

# ── 7. Duplicate sizes across colours collapse ──────────────────────────
check("duplicates collapsed, order kept",
      sizes("<p>4<span>/</span></p><p>4<span>/</span></p><p>5</p>"), ["4", "5"])

# ── 8. Product-link extraction, incl. junk filtering ────────────────────
listing = """
<a href="/mailorder">MAIL ORDER</a>
<a href="/mailorder/form">ORDER FORM</a>
<a href="/mailorder/reversible_jacket_e">REVERSIBLE JACKET <span>COLOR BLACK</span></a>
<a href="/mailorder/reversible_jacket_e?utm=x">dup with query</a>
<a href="/mailorder/thin_cotton_ls_t-shirt_e/">TRAILING SLASH</a>
<a href="/shop">SHOP</a>
"""
found = links(listing)
check("product URLs (form/dupes/non-products filtered)",
      [p.url for p in found],
      ["https://www.comoli.jp/mailorder/reversible_jacket_e",
       "https://www.comoli.jp/mailorder/thin_cotton_ls_t-shirt_e"])
check("first product name", found[0].name, "REVERSIBLE JACKET COLOR BLACK")

# Absolute links in any of the forms a site actually emits must all resolve
# to the same product, and a foreign host must never be followed.
for label, href in [
    ("https + www", "https://www.comoli.jp/mailorder/x"),
    ("https, no www", "https://comoli.jp/mailorder/x"),
    ("http", "http://www.comoli.jp/mailorder/x"),
    ("protocol-relative", "//www.comoli.jp/mailorder/x"),
    ("relative", "/mailorder/x"),
]:
    check(f"link form: {label}",
          [p.url for p in links(f'<a href="{href}">X</a>')],
          ["https://www.comoli.jp/mailorder/x"])
check("foreign host ignored",
      links('<a href="https://evil.example/mailorder/x">X</a>'), [])

# ── 9. display_name ─────────────────────────────────────────────────────
check("colour suffix trimmed",
      comoli.display_name("REVERSIBLE JACKET COLOR BLACK"), "REVERSIBLE JACKET")
check("COLOR inside a word is not a split point",
      comoli.display_name("TRICOLOR KNIT VEST"), "TRICOLOR KNIT VEST")
check("name without COLOR is untouched",
      comoli.display_name("WOOL COAT"), "WOOL COAT")

# ── 10. Size normalisation ──────────────────────────────────────────────
check("underscore folds to hyphen", normalize_size("2_INT"), "2-INT")
check("case and padding folded", normalize_size("  2 int "), "2-INT")
check("already-normal size unchanged", normalize_size("2"), "2")
check("one-size left alone", normalize_size("O/S"), "O/S")

# ── 11. Graphpaper: Shopify variant reading ─────────────────────────────
gp = Graphpaper()

prod = {
    "handle": "gm263-50480", "title": "Wool Ripstop Shirt Jacket",
    "options": [
        {"name": "Color name", "position": 1, "values": ["ASH", "BLACK"]},
        {"name": "Size", "position": 2, "values": ["1", "2"]},
        {"name": "Color", "position": 3, "values": ["ASH", "BLACK"]},
    ],
    "variants": [
        {"option1": "ASH", "option2": "1", "available": True},
        {"option1": "ASH", "option2": "2", "available": False},
        {"option1": "BLACK", "option2": "2", "available": True},
    ],
}
st = gp._stock_for(prod)
check("graphpaper: size live in one colour counts", st.sizes, ["1", "2"])
check("graphpaper builds the product URL", st.url,
      "https://eng.graphpaper-tokyo.com/products/gm263-50480")
check("graphpaper uses the product title", st.name, "Wool Ripstop Shirt Jacket")

check("graphpaper finds Size at position 1",
      gp._stock_for(dict(prod,
          options=[{"name": "Size", "position": 1}, {"name": "Color", "position": 2}],
          variants=[{"option1": "2_INT", "option2": "NAVY", "available": True}])).sizes,
      ["2_INT"])

# A renamed size option used to make the COLOUR the size — which matches no
# target, so the brand would go silent forever with no alarm.
check("graphpaper handles a localised Size option name",
      gp._stock_for(dict(prod,
          options=[{"name": "サイズ", "position": 1}, {"name": "Color", "position": 2}],
          variants=[{"option1": "2_INT", "option2": "NAVY", "available": True}])).sizes,
      ["2_INT"])
check("graphpaper tolerates a string position",
      gp._stock_for(dict(prod,
          options=[{"name": "Size", "position": "1"}],
          variants=[{"option1": "2", "available": True}])).sizes,
      ["2"])

check("graphpaper all-sold-out is empty, not unknown",
      gp._stock_for(dict(prod, variants=[
          {"option1": "ASH", "option2": "2", "available": False}])).sizes, [])

# No identifiable size option is 'unknown', never 'sold out in every size'.
check("graphpaper: unidentifiable size option reports unknown",
      gp._stock_for({"handle": "sock", "title": "SOCKS",
                     "options": [{"name": "Color", "position": 1}],
                     "variants": [{"option1": "BLACK", "available": True}]}).sizes,
      None)
check("graphpaper: missing variants key reports unknown",
      gp._stock_for({"handle": "x", "title": "X",
                     "options": [{"name": "Size", "position": 1}]}).sizes, None)
check("graphpaper: missing handle reports unknown",
      gp._stock_for({"title": "X", "options": [{"name": "Size", "position": 1}],
                     "variants": []}).sizes, None)

check("graphpaper targets normalise", gp.normalized_targets, ("2", "2-INT"))

# ── 11b. Neighbour: the same reader, a market-prefixed store ────────────
nb = Neighbour()

# Shape taken from the live feed: one option, named Size, at position 1.
shirt = {
    "handle": "comoli-mens-comoli-shirt-black-aw26",
    "title": "Comoli Shirt Black",
    "options": [{"name": "Size", "position": 1, "values": ["2", "3", "4"]}],
    "variants": [
        {"option1": "2", "available": False},
        {"option1": "3", "available": False},
        {"option1": "4", "available": True},
    ],
}
st = nb._stock_for(shirt)
check("neighbour reads a single Size option", st.sizes, ["4"])
check("neighbour uses the product title", st.name, "Comoli Shirt Black")

# This store serves products under a market prefix. base_url + "/products/"
# is the inherited default and would send the push to a redirect, so the
# override has to survive: an alert whose link doesn't open the garment is
# most of the alert gone.
check("neighbour keeps the market prefix in product URLs", st.url,
      "https://www.shopneighbour.com/en-us/products/"
      "comoli-mens-comoli-shirt-black-aw26")
check("graphpaper still uses the un-prefixed default",
      gp._stock_for({"handle": "coat-b", "title": "COAT",
                     "options": [{"name": "Size", "position": 1}],
                     "variants": [{"option1": "2", "available": True}]}).url,
      "https://eng.graphpaper-tokyo.com/products/coat-b")

check("neighbour watches 4 and 5", nb.normalized_targets, ("4", "5"))
check("neighbour has its own state key, not comoli's", nb.key != "comoli", True)

# Sold out in every size is a fact; unreadable is not. Same distinction as
# everywhere else, asserted here because this adapter is what will report it.
check("neighbour all-sold-out is empty, not unknown",
      nb._stock_for(dict(shirt, variants=[{"option1": "4", "available": False}])).sizes,
      [])
check("neighbour: no identifiable size option reports unknown",
      nb._stock_for({"handle": "cap", "title": "CAP",
                     "options": [{"name": "Color", "position": 1}],
                     "variants": [{"option1": "BLACK", "available": True}]}).sizes,
      None)

# Footwear (JP scale) and one-size pieces share this collection with the
# garments. Neither can be mistaken for a 4 or a 5, but they must come back
# spelled as the shop spells them rather than folded into the garment scale.
check("neighbour passes footwear sizes through untouched",
      nb._stock_for({"handle": "boot", "title": "Leather Boots",
                     "options": [{"name": "Size", "position": 1}],
                     "variants": [{"option1": "27", "available": True},
                                  {"option1": "27.5", "available": True},
                                  {"option1": "28", "available": False}]}).sizes,
      ["27", "27.5"])
check("neighbour passes Free Size through untouched",
      nb._stock_for({"handle": "scarf", "title": "Silk Scarf",
                     "options": [{"name": "Size", "position": 1}],
                     "variants": [{"option1": "Free Size", "available": True}]}).sizes,
      ["Free Size"])

# ── 11c. A size the feed doesn't actually carry ─────────────────────────
# Shopify sends every variant all three option slots and nulls the unused
# ones, so .get("option2", "") returns None rather than the default when the
# options list names Size at a position the variants don't fill. str(None) is
# the literal "None", which normalises to NONE, matches no target, and reads
# to the engine as a healthy "in stock, nothing in your size" — silence over a
# garment that may be sitting there in a 4. Unknown is the only honest answer.
promised_at_2 = {
    "handle": "sleeping-shirt", "title": "Sleeping Shirt",
    "options": [{"name": "Size", "position": 2}],
    "variants": [{"option1": "4", "option2": None, "available": True},
                 {"option1": "5", "option2": None, "available": True}],
}
check("null at the promised size position reports unknown",
      nb._stock_for(promised_at_2).sizes, None)
check("that product is never reported as a readable size",
      nb._stock_for(promised_at_2).sizes in ([], ["None"], ["NONE"]), False)
check("a blank size on an available variant reports unknown",
      nb._stock_for(dict(promised_at_2, variants=[
          {"option1": "4", "option2": "   ", "available": True}])).sizes,
      None)

# Only *available* variants have to be readable — a sold-out one's size is
# nobody's business, and letting it force unknown would make half the
# catalogue unreadable for no gain.
check("a null size on a sold-out variant doesn't spoil the product",
      nb._stock_for(dict(promised_at_2, variants=[
          {"option1": "x", "option2": None, "available": False},
          {"option1": "x", "option2": "4", "available": True}])).sizes,
      ["4"])
# And the distinction the whole design rests on must survive the fix.
check("no available variants is still sold out, not unknown",
      nb._stock_for(dict(promised_at_2, variants=[
          {"option1": "x", "option2": "4", "available": False}])).sizes,
      [])
check("graphpaper reads unchanged through the same path",
      gp._stock_for({"handle": "coat", "title": "COAT",
                     "options": [{"name": "Size", "position": 2}],
                     "variants": [{"option1": "NAVY", "option2": "2_INT",
                                   "available": True}]}).sizes,
      ["2_INT"])

# ── 12. Pushover chunking ───────────────────────────────────────────────
many = [(f"🔔 Item number {i} — size 4\nhttps://www.comoli.jp/mailorder/item_{i}",
         f"https://www.comoli.jp/mailorder/item_{i}") for i in range(30)]
chunks = chunk_alerts(many)
check("every chunk within Pushover limit",
      all(len(body) <= PUSHOVER_LIMIT for body, _ in chunks), True)
check("no alert lines dropped in chunking",
      sum(body.count("🔔") for body, _ in chunks), 30)
check("more than one chunk was needed", len(chunks) > 1, True)

# Each chunk's tap-through must open something inside that chunk, not always
# the very first alert.
check("every chunk carries a link", all(url for _, url in chunks), True)
check("each chunk links to its own first alert",
      all(url and body.startswith(f"🔔 Item number {url.rsplit('_', 1)[1]} ")
          for body, url in chunks), True)
check("chunks link to different products",
      len({url for _, url in chunks}), len(chunks))

# An over-long line must lose name text, never the URL that makes it useful.
huge = [("🔔 " + "x" * 5000 + "\nhttps://www.comoli.jp/mailorder/item_x",
         "https://www.comoli.jp/mailorder/item_x")]
body = chunk_alerts(huge)[0][0]
check("oversized line truncated within limit", len(body) <= PUSHOVER_LIMIT, True)
check("oversized line keeps its URL intact",
      body.endswith("https://www.comoli.jp/mailorder/item_x"), True)

# ── 13. Adapter registry ────────────────────────────────────────────────
check("comoli is registered", "comoli" in ADAPTERS, True)
check("graphpaper is registered", "graphpaper" in ADAPTERS, True)
check("neighbour is registered", "neighbour-comoli" in ADAPTERS, True)
check("the two COMOLI shops are separate sites",
      len({ADAPTERS["comoli"].key, ADAPTERS["neighbour-comoli"].key}), 2)
check("resolve() with no args returns every site", len(resolve()), len(ADAPTERS))
check("resolve() honours an explicit key",
      [a.key for a in resolve(["graphpaper"])], ["graphpaper"])
try:
    resolve(["nope"])
    check("unknown site key is rejected", False, True)
except SystemExit:
    check("unknown site key is rejected", True, True)

report("All checks passed.")
