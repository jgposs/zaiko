#!/usr/bin/env python3
"""Offline tests for the COMOLI monitor's parsing logic. No network."""

import comoli_monitor as m

failures = []


def check(label, got, want):
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'}  {label}\n        got={got!r} want={want!r}")
    if not ok:
        failures.append(label)


# ── 1. Documented layout: one size per <p>, one row per colour ──────────
one_per_p = """
<div><p>BLACK</p>
  <p>2<span>/</span></p>
  <p>3<span>/</span></p>
  <p><span class="td_line-through">4</span></p>
</div>
"""
check("one size per <p>, 4 sold out", m.parse_available_sizes(one_per_p), ["2", "3"])

# ── 2. Risk layout: every size inside a single <p> ──────────────────────
all_in_one_p = """
<p>1<span>/</span>2<span>/</span><span class="td_line-through">4</span></p>
"""
check("all sizes in one <p>", m.parse_available_sizes(all_in_one_p), ["1", "2"])

# ── 3. Sold out in one colour, available in another ─────────────────────
two_colours = """
<p>BLACK</p>
<p><span class="td_line-through">4</span></p>
<p>WHITE</p>
<p>4<span>/</span></p>
"""
check("size 4 sold out in black, live in white", m.parse_available_sizes(two_colours), ["4"])

# ── 4. Everything sold out ──────────────────────────────────────────────
all_gone = """
<p><span class="td_line-through">2</span></p>
<p><span class="td_line-through">4</span></p>
"""
check("everything sold out", m.parse_available_sizes(all_gone), [])

# ── 5. Page with no size markup at all (structure changed) ──────────────
no_sizes = "<div><p>Cotton 100%</p><p>¥132,000</p><p>E03-06002</p></div>"
check("no sizes present", m.parse_available_sizes(no_sizes), [])

# ── 6. Prices / dates must not be mistaken for sizes ────────────────────
noise = "<p>132000</p><p>2026/08/22</p><p>05018</p>"
check("prices and dates ignored", m.parse_available_sizes(noise), [])

# ── 7. Nested markup around the digit ───────────────────────────────────
nested = '<p><a href="#"><em>3</em></a><span>/</span></p>'
check("digit nested in <a><em>", m.parse_available_sizes(nested), ["3"])

# ── 8. Duplicate sizes across colours collapse ──────────────────────────
dupes = "<p>4<span>/</span></p><p>4<span>/</span></p><p>5</p>"
check("duplicates collapsed, order kept", m.parse_available_sizes(dupes), ["4", "5"])

# ── 9. Product-link extraction, incl. junk filtering ────────────────────
listing = """
<a href="/mailorder">MAIL ORDER</a>
<a href="/mailorder/form">ORDER FORM</a>
<a href="/mailorder/reversible_jacket_e">REVERSIBLE JACKET <span>COLOR BLACK</span></a>
<a href="/mailorder/reversible_jacket_e?utm=x">dup with query</a>
<a href="/mailorder/thin_cotton_ls_t-shirt_e/">TRAILING SLASH</a>
<a href="/shop">SHOP</a>
"""
links = m.parse_product_links(listing)
check("product URLs (form/dupes/non-products filtered)",
      [p["url"] for p in links],
      ["https://www.comoli.jp/mailorder/reversible_jacket_e",
       "https://www.comoli.jp/mailorder/thin_cotton_ls_t-shirt_e"])
check("first product name", links[0]["name"], "REVERSIBLE JACKET COLOR BLACK")

# ── 10. Pushover chunking stays under the cap ───────────────────────────
many = [f"🔔 Item number {i} — size 4\nhttps://www.comoli.jp/mailorder/item_{i}" for i in range(30)]
chunks = m.chunk_lines(many)
check("every chunk within Pushover limit", all(len(c) <= m.PUSHOVER_LIMIT for c in chunks), True)
check("no alert lines dropped in chunking",
      sum(c.count("🔔") for c in chunks), 30)

single_huge = ["x" * 5000]
check("oversized single line is truncated, not dropped",
      all(len(c) <= m.PUSHOVER_LIMIT for c in m.chunk_lines(single_huge)), True)

# ── 11. Alert logic: new / restock / steady / gone ───────────────────────
def newly(prev, current, targets=("4", "5")):
    available = [s for s in targets if s in current]
    return [s for s in available if s not in (prev or [])]

check("never seen before, size 4 live -> alert", newly(None, ["2", "4"]), ["4"])
check("already had 4, still 4 -> silent", newly(["2", "4"], ["2", "4"]), [])
check("4 gone then back -> alert", newly(["2"], ["2", "4"]), ["4"])
check("4 sold out -> silent", newly(["2", "4"], ["2"]), [])
check("non-target size appears -> silent", newly(["4"], ["3", "4"]), [])

print()
if failures:
    print(f"{len(failures)} FAILING: {failures}")
    raise SystemExit(1)
print("All checks passed.")
