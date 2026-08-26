#!/usr/bin/env python3
"""Offline tests for parsing and message handling. No network."""

import _bootstrap
from _bootstrap import check, report

from zaiko.notify import chunk_lines
from zaiko.config import PUSHOVER_LIMIT
from zaiko.sites.comoli import Comoli

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

# ── 3. Sold out in one colour, available in another ─────────────────────
two_colours = """
<p>BLACK</p>
<p><span class="td_line-through">4</span></p>
<p>WHITE</p>
<p>4<span>/</span></p>
"""
check("size 4 sold out in black, live in white", sizes(two_colours), ["4"])

# ── 4. Everything sold out ──────────────────────────────────────────────
all_gone = """
<p><span class="td_line-through">2</span></p>
<p><span class="td_line-through">4</span></p>
"""
check("everything sold out", sizes(all_gone), [])

# ── 5. Page with no size markup at all (structure changed) ──────────────
no_sizes = "<div><p>Cotton 100%</p><p>¥132,000</p><p>E03-06002</p></div>"
check("no sizes present", sizes(no_sizes), [])

# ── 6. Prices / dates must not be mistaken for sizes ────────────────────
noise = "<p>132000</p><p>2026/08/22</p><p>05018</p>"
check("prices and dates ignored", sizes(noise), [])

# ── 7. Nested markup around the digit ───────────────────────────────────
nested = '<p><a href="#"><em>3</em></a><span>/</span></p>'
check("digit nested in <a><em>", sizes(nested), ["3"])

# ── 8. Duplicate sizes across colours collapse ──────────────────────────
dupes = "<p>4<span>/</span></p><p>4<span>/</span></p><p>5</p>"
check("duplicates collapsed, order kept", sizes(dupes), ["4", "5"])

# ── 9. Product-link extraction, incl. junk filtering ────────────────────
listing = """
<a href="/mailorder">MAIL ORDER</a>
<a href="/mailorder/form">ORDER FORM</a>
<a href="/mailorder/reversible_jacket_e">REVERSIBLE JACKET <span>COLOR BLACK</span></a>
<a href="/mailorder/reversible_jacket_e?utm=x">dup with query</a>
<a href="/mailorder/thin_cotton_ls_t-shirt_e/">TRAILING SLASH</a>
<a href="https://www.comoli.jp/mailorder/absolute_link_e">ABSOLUTE</a>
<a href="/shop">SHOP</a>
"""
found = links(listing)
check("product URLs (form/dupes/non-products filtered)",
      [p.url for p in found],
      ["https://www.comoli.jp/mailorder/reversible_jacket_e",
       "https://www.comoli.jp/mailorder/thin_cotton_ls_t-shirt_e",
       "https://www.comoli.jp/mailorder/absolute_link_e"])
check("first product name", found[0].name, "REVERSIBLE JACKET COLOR BLACK")
check("display name trims the colour suffix",
      comoli.display_name(found[0].name), "REVERSIBLE JACKET")

# ── 10. Pushover chunking stays under the cap ───────────────────────────
many = [f"🔔 Item number {i} — size 4\nhttps://www.comoli.jp/mailorder/item_{i}"
        for i in range(30)]
chunks = chunk_lines(many)
check("every chunk within Pushover limit",
      all(len(c) <= PUSHOVER_LIMIT for c in chunks), True)
check("no alert lines dropped in chunking",
      sum(c.count("🔔") for c in chunks), 30)
check("oversized single line is truncated, not dropped",
      all(len(c) <= PUSHOVER_LIMIT for c in chunk_lines(["x" * 5000])), True)

# ── 11. Alert logic: new / restock / steady / gone ──────────────────────
def newly(prev, current, targets=("4", "5")):
    available = [s for s in targets if s in current]
    return [s for s in available if s not in (prev or [])]

check("never seen before, size 4 live -> alert", newly(None, ["2", "4"]), ["4"])
check("already had 4, still 4 -> silent", newly(["2", "4"], ["2", "4"]), [])
check("4 gone then back -> alert", newly(["2"], ["2", "4"]), ["4"])
check("4 sold out -> silent", newly(["2", "4"], ["2"]), [])
check("non-target size appears -> silent", newly(["4"], ["3", "4"]), [])

# ── 12. Size normalisation ──────────────────────────────────────────────
from zaiko.sites.base import normalize_size

check("underscore folds to hyphen", normalize_size("2_INT"), "2-INT")
check("case and padding folded", normalize_size("  2 int "), "2-INT")
check("already-normal size unchanged", normalize_size("2"), "2")
check("one-size left alone", normalize_size("O/S"), "O/S")

# ── 13. Graphpaper: Shopify variant reading ─────────────────────────────
from zaiko.sites.graphpaper import Graphpaper

gp = Graphpaper()

# Size is option2 here, and the sold-out variant must not count.
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
check("graphpaper size 2 live in one colour counts", st.sizes, ["1", "2"])
check("graphpaper builds the product URL", st.url,
      "https://eng.graphpaper-tokyo.com/products/gm263-50480")
check("graphpaper uses the product title", st.name, "Wool Ripstop Shirt Jacket")

# Size option in a different position must still be found.
moved = dict(prod, options=[
    {"name": "Size", "position": 1, "values": ["2_INT"]},
    {"name": "Color", "position": 2, "values": ["NAVY"]},
], variants=[{"option1": "2_INT", "option2": "NAVY", "available": True}])
check("graphpaper finds Size at position 1", gp._stock_for(moved).sizes, ["2-INT"])

# Everything sold out reads as empty, never as unknown.
gone = dict(prod, variants=[{"option1": "ASH", "option2": "2", "available": False}])
check("graphpaper all-sold-out is empty, not None", gp._stock_for(gone).sizes, [])

# No named Size option: fall back to the last segment of the variant title.
noopt = {"handle": "sock", "title": "SOCKS", "options": [
            {"name": "Color", "position": 1, "values": ["BLACK"]}],
         "variants": [{"title": "BLACK / O/S", "available": True}]}
check("graphpaper falls back to the variant title", gp._stock_for(noopt).sizes, ["O/S"])

check("graphpaper targets normalise", gp.normalized_targets, ("2", "2-INT"))

# ── 14. Adapter registry ────────────────────────────────────────────────
from zaiko.sites import ADAPTERS, resolve

check("comoli is registered", "comoli" in ADAPTERS, True)
check("graphpaper is registered", "graphpaper" in ADAPTERS, True)
check("resolve() with no args returns every site",
      len(resolve()), len(ADAPTERS))
check("resolve() honours an explicit key",
      [a.key for a in resolve(["graphpaper"])], ["graphpaper"])
try:
    resolve(["nope"])
    check("unknown site key is rejected", False, True)
except SystemExit:
    check("unknown site key is rejected", True, True)

report("All checks passed.")
