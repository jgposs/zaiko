#!/usr/bin/env python3
"""End-to-end run() tests with the network stubbed out. No real requests."""

import tempfile
from pathlib import Path

import _bootstrap
from _bootstrap import check, report

import json

from zaiko import config, runner
from zaiko.sites import ADAPTERS
from zaiko.sites.base import Product, SiteAdapter, Stock
from zaiko.sites.base import SiteUnavailable
from zaiko.sites.comoli import Comoli
from zaiko.sites.graphpaper import Graphpaper
from zaiko.state import load_state

# ── Harness ─────────────────────────────────────────────────────────────
LISTING = """
<a href="/mailorder/jacket">REVERSIBLE JACKET COLOR BLACK</a>
<a href="/mailorder/tee">THIN COTTON TEE COLOR WHITE</a>
<a href="/mailorder/form">ORDER FORM</a>
"""

PAGES = {
    "https://www.comoli.jp/mailorder": LISTING,
    # size 4 available, 5 sold out
    "https://www.comoli.jp/mailorder/jacket":
        '<p>2<span>/</span></p><p>4<span>/</span></p>'
        '<p><span class="td_line-through">5</span></p>',
    # nothing we care about
    "https://www.comoli.jp/mailorder/tee":
        '<p>1<span>/</span></p><p>2</p>',
}

sent: list[tuple] = []
runner.notify = lambda title, message, url="", url_title="", priority=0: \
    sent.append((title, message))
runner.fetch = lambda session, url: PAGES.get(url)

comoli = Comoli()
comoli.request_delay = 0

tmp = Path(tempfile.mkdtemp())
config.STATE_FILE = tmp / "state.json"


def run_comoli() -> int:
    """One full run of just the Comoli adapter, through the real run()."""
    sent.clear()
    return runner.run(site_keys=["comoli"])


# Drive the registry explicitly: each phase below registers exactly the
# adapters it means to exercise, so a real adapter can't wander into an
# unrelated assertion.
ADAPTERS.clear()
ADAPTERS["comoli"] = comoli          # the zero-delay instance

# ── Run 1: first ever run — size 4 on the jacket should alert ───────────
rc = run_comoli()
check("run 1 exit code", rc, 0)
check("run 1 sent exactly one notification", len(sent), 1)
check("run 1 alert names the jacket", "REVERSIBLE JACKET" in sent[0][1], True)
check("run 1 alert is for size 4", "size 4" in sent[0][1], True)
check("run 1 did not alert on the tee", "TEE" in sent[0][1], False)
check("run 1 title carries the brand", sent[0][0].startswith("COMOLI:"), True)
check("state file written", config.STATE_FILE.exists(), True)
check("state is keyed by site", list(load_state()), ["comoli"])

# ── Run 2: nothing changed — must stay silent ───────────────────────────
rc = run_comoli()
check("run 2 exit code", rc, 0)
check("run 2 is silent (no re-announcement)", len(sent), 0)

# ── Run 3: size 5 restocks on the jacket ────────────────────────────────
PAGES["https://www.comoli.jp/mailorder/jacket"] = \
    "<p>2<span>/</span></p><p>4<span>/</span></p><p>5</p>"
rc = run_comoli()
check("run 3 alerts on the restock", len(sent), 1)
check("run 3 alerts for size 5 only", "size 5" in sent[0][1], True)

# ── Run 4: a page fails to load — must NOT be treated as sold out ───────
real_pages = dict(PAGES)
runner.fetch = lambda session, url: None if url.endswith("/jacket") else real_pages.get(url)
rc = run_comoli()
check("run 4 stays silent when a page fails", len(sent), 0)
saved = load_state()["comoli"]["https://www.comoli.jp/mailorder/jacket"]["sizes"]
check("run 4 preserved the failed product's sizes", saved, ["2", "4", "5"])

# ── Run 5: page recovers unchanged — must not look like a restock ───────
runner.fetch = lambda session, url: real_pages.get(url)
rc = run_comoli()
check("run 5 no false restock after recovery", len(sent), 0)

# ── Run 6: site markup changes — the broken-monitor alarm must fire ─────
for key in list(real_pages):
    if key != "https://www.comoli.jp/mailorder":
        real_pages[key] = "<div>no sizes here any more</div>"
rc = run_comoli()
check("run 6 exit code signals failure", rc, 1)
check("run 6 fired the broken-monitor alarm", len(sent), 1)
check("run 6 alarm text", "may be broken" in sent[0][0], True)

# ── Run 7: listing structure changes — alarm, and no silent success ─────
real_pages["https://www.comoli.jp/mailorder"] = "<div>nothing</div>"
rc = run_comoli()
check("run 7 exit code signals failure", rc, 1)
check("run 7 alarmed on empty listing", "may be broken" in sent[0][0], True)

# ── Run 8: site unreachable entirely ────────────────────────────────────
runner.fetch = lambda session, url: None
rc = run_comoli()
check("run 8 exit code signals failure", rc, 1)
check("run 8 alarmed on unreachable site", "unreachable" in sent[0][0], True)

# ── Multi-site: a second brand, and failure isolation ───────────────────
OTHER = {
    "https://example-brand.jp/shop": '<a href="/p/coat">WOOL COAT</a>',
    "https://example-brand.jp/p/coat": "<p>M</p><p>L</p>",
}


class ExampleBrand(SiteAdapter):
    key = "example"
    label = "EXAMPLE"
    base_url = "https://example-brand.jp"
    listing_url = "https://example-brand.jp/shop"
    target_sizes = ("M", "L")
    request_delay = 0

    def collect(self, session, fetch):
        listing = fetch(session, self.listing_url)
        if listing is None:
            raise SiteUnavailable("shop page failed")
        url = "https://example-brand.jp/p/coat"
        page = fetch(session, url)
        if page is None:
            yield Stock("WOOL COAT", url, None)
            return
        yield Stock("WOOL COAT", url,
                    [t for t in ("M", "L") if f"<p>{t}</p>" in page])


ADAPTERS["example"] = ExampleBrand()
config.STATE_FILE = tmp / "multi.json"

ALL = dict(PAGES)
ALL["https://www.comoli.jp/mailorder"] = LISTING
ALL["https://www.comoli.jp/mailorder/jacket"] = "<p>4<span>/</span></p>"
ALL.update(OTHER)
runner.fetch = lambda session, url: ALL.get(url)

sent.clear()
rc = runner.run()
check("multi-site run is healthy", rc, 0)
check("multi-site alerted for both brands", len(sent), 2)
check("both brands present in titles",
      sorted(t.split(":")[0] for t, _ in sent), ["COMOLI", "EXAMPLE"])
check("state holds both sites", sorted(load_state()), ["comoli", "example"])
check("per-brand sizes respected",
      "size M/L" in sent[[t.startswith("EXAMPLE") for t, _ in sent].index(True)][0],
      True)

# One brand going down must not stop the other, and must not lose its state.
def only_comoli_fails(session, url):
    return None if "comoli.jp" in url else ALL.get(url)


OTHER["https://example-brand.jp/p/coat"] = "<p>M</p>"   # L sells out, M steady
runner.fetch = only_comoli_fails
ALL.update(OTHER)
sent.clear()
rc = runner.run()
check("a dead site makes the run unhealthy", rc, 1)
check("the dead site alarmed once", sum("unreachable" in t for t, _ in sent), 1)
state = load_state()
check("healthy site still updated its state",
      state["example"]["https://example-brand.jp/p/coat"]["sizes"], ["M"])
check("dead site kept its previous state",
      state["comoli"]["https://www.comoli.jp/mailorder/jacket"]["sizes"], ["4"])

# A crashing adapter is contained too.
class Exploding(SiteAdapter):
    key = "boom"
    label = "BOOM"
    listing_url = "https://boom.example/shop"
    target_sizes = ("1",)
    request_delay = 0

    def collect(self, session, fetch):
        raise RuntimeError("adapter blew up")
        yield  # pragma: no cover - makes this a generator


ADAPTERS["boom"] = Exploding()
ALL["https://boom.example/shop"] = "<html></html>"
runner.fetch = lambda session, url: ALL.get(url)
sent.clear()
rc = runner.run()
check("a crashing adapter makes the run unhealthy", rc, 1)
check("the crash was reported", sum("crashed" in t for t, _ in sent), 1)
check("other sites still ran despite the crash",
      "example" in load_state() and "comoli" in load_state(), True)


# ── Seed mode: record current stock silently, then stay quiet ───────────
del ADAPTERS["boom"]
del ADAPTERS["example"]
FRESH = {
    "https://www.comoli.jp/mailorder": LISTING,
    "https://www.comoli.jp/mailorder/jacket": "<p>4<span>/</span></p><p>5</p>",
    "https://www.comoli.jp/mailorder/tee": "<p>1<span>/</span></p>",
}
runner.fetch = lambda session, url: FRESH.get(url)
config.STATE_FILE = tmp / "seeded.json"

sent.clear()
rc = runner.run(seed=True)
check("seed run is healthy", rc, 0)
check("seed run pushes nothing", len(sent), 0)
check("seed run still wrote state",
      load_state()["comoli"]["https://www.comoli.jp/mailorder/jacket"]["sizes"],
      ["4", "5"])

sent.clear()
rc = runner.run()
check("run after seeding is silent", len(sent), 0)

# ...but a genuine restock after seeding still alerts.
FRESH["https://www.comoli.jp/mailorder/tee"] = "<p>1<span>/</span></p><p>4</p>"
sent.clear()
rc = runner.run()
check("restock after seeding still alerts", len(sent), 1)
check("that alert is the tee in size 4", "TEE" in sent[0][1] and "size 4" in sent[0][1], True)


# ── Graphpaper: the Shopify JSON path, end to end ──────────────────────
del ADAPTERS["comoli"]
gp = Graphpaper()
gp.request_delay = 0
ADAPTERS["graphpaper"] = gp
config.STATE_FILE = tmp / "gp.json"

GP_FEED = {"products": [
    {"handle": "jacket-a", "title": "WOOL RIPSTOP SHIRT JACKET",
     "options": [{"name": "Color name", "position": 1, "values": ["ASH"]},
                 {"name": "Size", "position": 2, "values": ["1", "2"]}],
     "variants": [{"option1": "ASH", "option2": "1", "available": True},
                  {"option1": "ASH", "option2": "2", "available": False}]},
    {"handle": "coat-b", "title": "STAND COLLAR COAT",
     "options": [{"name": "Color name", "position": 1, "values": ["NAVY"]},
                 {"name": "Size", "position": 2, "values": ["2_INT"]}],
     "variants": [{"option1": "NAVY", "option2": "2_INT", "available": True}]},
]}

GP_URL = ("https://eng.graphpaper-tokyo.com/collections/mens-global"
          "/products.json?limit=250&page=1")


def gp_fetch(session, url):
    return json.dumps(GP_FEED) if url == GP_URL else None


runner.fetch = gp_fetch
sent.clear()
rc = runner.run()
check("graphpaper run is healthy", rc, 0)
check("graphpaper alerted once", len(sent), 1)
check("graphpaper alerted on the 2_INT coat", "STAND COLLAR COAT" in sent[0][1], True)
check("graphpaper did not alert on the sold-out size 2 jacket",
      "SHIRT JACKET" in sent[0][1], False)
check("graphpaper state keyed by product URL",
      sorted(load_state()["graphpaper"]),
      ["https://eng.graphpaper-tokyo.com/products/coat-b",
       "https://eng.graphpaper-tokyo.com/products/jacket-a"])

# Size 2 restocks on the jacket.
GP_FEED["products"][0]["variants"][1]["available"] = True
sent.clear()
rc = runner.run()
check("graphpaper alerts when size 2 restocks", len(sent), 1)
check("that alert is the jacket", "SHIRT JACKET" in sent[0][1], True)

sent.clear()
rc = runner.run()
check("graphpaper is silent when nothing changed", len(sent), 0)

# A malformed feed must alarm rather than read as 'everything sold out'.
runner.fetch = lambda session, url: "<html>not json</html>"
sent.clear()
rc = runner.run()
check("graphpaper alarms on a non-JSON feed", rc, 1)
check("graphpaper named it as broken", "may be broken" in sent[0][0], True)
saved = load_state()["graphpaper"]["https://eng.graphpaper-tokyo.com/products/coat-b"]
check("broken feed did not wipe saved stock", saved["sizes"], ["2-INT"])

# A feed that is valid JSON but the wrong shape is equally suspicious.
runner.fetch = lambda session, url: json.dumps({"items": []})
sent.clear()
rc = runner.run()
check("graphpaper alarms on an unexpected JSON shape", rc, 1)

# Site down entirely.
runner.fetch = lambda session, url: None
sent.clear()
rc = runner.run()
check("graphpaper alarms when unreachable", rc, 1)
check("graphpaper unreachable text", "unreachable" in sent[0][0], True)

report("All end-to-end checks passed.")
