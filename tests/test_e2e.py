#!/usr/bin/env python3
"""End-to-end run() tests with the network stubbed out. No real requests."""

import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from _bootstrap import check, report

from zaiko import config, runner
from zaiko.sites import ADAPTERS
from zaiko.sites.base import SiteLooksBroken, SiteUnavailable, Stock, SiteAdapter
from zaiko.sites.comoli import Comoli
from zaiko.sites.graphpaper import Graphpaper
from zaiko.state import load_state

# ── Harness ─────────────────────────────────────────────────────────────
LISTING = """
<a href="/mailorder/jacket">REVERSIBLE JACKET COLOR BLACK</a>
<a href="/mailorder/tee">THIN COTTON TEE COLOR WHITE</a>
<a href="/mailorder/form">ORDER FORM</a>
"""
JACKET = "https://www.comoli.jp/mailorder/jacket"
TEE = "https://www.comoli.jp/mailorder/tee"

PAGES = {
    "https://www.comoli.jp/mailorder": LISTING,
    # size 4 available, 5 sold out
    JACKET: '<p>2<span>/</span></p><p>4<span>/</span></p>'
            '<p><span class="td_line-through">5</span></p>',
    # nothing we care about
    TEE: '<p>1<span>/</span></p><p>2</p>',
}

sent: list = []
delivery_ok = [True]          # flip to simulate Pushover refusing


def fake_notify(title, message, url="", url_title="", priority=0):
    sent.append((title, message, url))
    return delivery_ok[0]


runner.notify = fake_notify
runner.have_credentials = lambda: True
runner.fetch = lambda session, url: PAGES.get(url)

comoli = Comoli()
comoli.request_delay = 0

tmp = Path(tempfile.mkdtemp())
config.STATE_FILE = tmp / "state.json"

# Each phase registers exactly the adapters it means to exercise, so a real
# adapter can't wander into an unrelated assertion.
ADAPTERS.clear()
ADAPTERS["comoli"] = comoli


def run_comoli(**kw) -> int:
    sent.clear()
    return runner.run(site_keys=["comoli"], **kw)


def sizes_for(url, site="comoli"):
    return load_state()[site][url]["sizes"]


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
check("state records when it last saw the product",
      "last_seen" in load_state()["comoli"][JACKET], True)

# ── Run 2: nothing changed — must stay silent ───────────────────────────
rc = run_comoli()
check("run 2 exit code", rc, 0)
check("run 2 is silent (no re-announcement)", len(sent), 0)

# The state file must be byte-identical after a run where nothing moved.
# Otherwise every daily run commits a diff touching every product, and the
# git history of state.json — the record of what actually dropped and when —
# becomes unreadable.
#
# The clock has to advance between the two runs or this passes for the wrong
# reason: back-to-back runs land in the same second, so second-resolution
# timestamps match even when they are being rewritten every time.
class AdvancingClock:
    """datetime stand-in for runner: one minute per run."""
    def __init__(self, start):
        self.t = start

    def now(self, tz=None):
        self.t += timedelta(minutes=1)
        return self.t


real_datetime, runner.datetime = runner.datetime, AdvancingClock(
    datetime.now(timezone.utc))
run_comoli()
unchanged = config.STATE_FILE.read_bytes()
run_comoli()
check("an unchanged run leaves the state file byte-identical",
      config.STATE_FILE.read_bytes(), unchanged)

# Same sizes in a different order is not a stock change. Sites don't promise
# a stable variant order, and recording a reshuffle as a change writes noise
# into the history that exists to record real ones.
reordered = dict(PAGES)
reordered[JACKET] = ('<p><span class="td_line-through">5</span></p>'
                     '<p>4<span>/</span></p><p>2<span>/</span></p>')
real_fetch = runner.fetch
runner.fetch = lambda session, url: reordered.get(url)
run_comoli()
check("a reordered size list is not treated as a change",
      config.STATE_FILE.read_bytes(), unchanged)
runner.fetch = real_fetch
runner.datetime = real_datetime

# ── Run 3: size 5 restocks on the jacket ────────────────────────────────
PAGES[JACKET] = "<p>2<span>/</span></p><p>4<span>/</span></p><p>5</p>"
rc = run_comoli()
check("run 3 alerts on the restock", len(sent), 1)
check("run 3 alerts for size 5 only", "size 5" in sent[0][1], True)

# ── Run 4: a page fails to load — must NOT be treated as sold out ───────
real_pages = dict(PAGES)
runner.fetch = lambda session, url: None if url.endswith("/jacket") else real_pages.get(url)
rc = run_comoli()
check("run 4 exit code stays healthy", rc, 0)
check("run 4 stays silent when a page fails", len(sent), 0)
check("run 4 preserved the failed product's sizes", sizes_for(JACKET), ["2", "4", "5"])

# ── Run 5: page recovers unchanged — must not look like a restock ───────
runner.fetch = lambda session, url: real_pages.get(url)
rc = run_comoli()
check("run 5 no false restock after recovery", len(sent), 0)

# ── Run 6: a product page returns 200 but with no size markup ───────────
# This is a soft 404 / interstitial / redesign. It must read as "unknown",
# not as "sold out", or the day it recovers becomes a fake restock.
real_pages[JACKET] = "<div>Just a moment...</div>"
rc = run_comoli()
check("run 6 exit code stays healthy", rc, 0)
check("run 6 is silent on an unreadable page", len(sent), 0)
check("run 6 kept the jacket's sizes", sizes_for(JACKET), ["2", "4", "5"])
real_pages[JACKET] = PAGES[JACKET]
rc = run_comoli()
check("run 6b no fake restock once the page returns", len(sent), 0)

# ── Run 7: the whole site's markup changes ──────────────────────────────
for key in list(real_pages):
    if key != "https://www.comoli.jp/mailorder":
        real_pages[key] = "<div>no sizes here any more</div>"
before_break = dict(load_state()["comoli"])
rc = run_comoli()
check("run 7 exit code signals failure", rc, 1)
check("run 7 fired an alarm", len(sent), 1)
check("run 7 alarm text", "unreadable" in sent[0][0] or "broken" in sent[0][0], True)
# The critical part: a broken run must not overwrite the memory, or the day
# the site comes back every product reads as a restock.
check("run 7 left the state exactly as it was",
      load_state()["comoli"], before_break)

for key in list(real_pages):
    if key != "https://www.comoli.jp/mailorder":
        real_pages[key] = PAGES[key]
rc = run_comoli()
check("run 7b no fake-restock flood after the site recovers", len(sent), 0)
check("run 7b run is healthy again", rc, 0)

# ── Run 8: listing structure changes — alarm, state untouched ───────────
real_pages["https://www.comoli.jp/mailorder"] = "<div>nothing</div>"
before = dict(load_state()["comoli"])
rc = run_comoli()
check("run 8 exit code signals failure", rc, 1)
check("run 8 alarmed on empty listing", "broken" in sent[0][0], True)
check("run 8 preserved state", load_state()["comoli"], before)
real_pages["https://www.comoli.jp/mailorder"] = LISTING

# ── Run 9: site unreachable entirely ────────────────────────────────────
runner.fetch = lambda session, url: None
rc = run_comoli()
check("run 9 exit code signals failure", rc, 1)
check("run 9 alarmed on unreachable site", "unreachable" in sent[0][0], True)
check("run 9 preserved state", load_state()["comoli"], before)
runner.fetch = lambda session, url: real_pages.get(url)

# ── Run 10: most pages unreadable, a few fine — partial degradation ─────
# Used to pass as a healthy run, reporting only the handful it could read.
MANY = {"https://www.comoli.jp/mailorder":
        "".join(f'<a href="/mailorder/p{i}">ITEM {i}</a>' for i in range(10))}
for i in range(10):
    MANY[f"https://www.comoli.jp/mailorder/p{i}"] = "<p>4</p>"
config.STATE_FILE = tmp / "partial.json"
runner.fetch = lambda session, url: MANY.get(url)
rc = run_comoli()
check("run 10 baseline healthy", rc, 0)

# p0 now reads differently; everything else fails. The one product we could
# read must NOT keep its new value, because we don't trust this run.
MANY["https://www.comoli.jp/mailorder/p0"] = "<p>5</p>"
runner.fetch = lambda session, url: (MANY.get(url) if url.endswith(("mailorder", "p0"))
                                     else None)
rc = run_comoli()
check("run 10 alarms when most products fail", rc, 1)
check("run 10 said what went wrong", "unreadable" in sent[0][0], True)
check("run 10 discarded the one product it did read",
      sizes_for("https://www.comoli.jp/mailorder/p0"), ["4"])
MANY["https://www.comoli.jp/mailorder/p0"] = "<p>4</p>"

# ── Run 11: delivery failure must not record the alert as seen ──────────
config.STATE_FILE = tmp / "delivery.json"
runner.fetch = lambda session, url: real_pages.get(url)
delivery_ok[0] = False
rc = run_comoli()
check("run 11 exit code signals failure", rc, 1)
check("run 11 attempted the push", len(sent), 1)
check("run 11 wrote no state for the undelivered alert",
      load_state().get("comoli", {}), {})

delivery_ok[0] = True
rc = run_comoli()
check("run 11b the alert is re-sent once delivery works", len(sent), 1)
check("run 11b names the jacket again", "REVERSIBLE JACKET" in sent[0][1], True)
check("run 11c then goes quiet", (run_comoli(), len(sent)), (0, 0))

# ── Run 11d: an alarm midway must discard what was already staged ──────
# A feed that breaks on page 5 has already handed us four pages of products.
# Recording those and then alarming is the worst of both worlds: the alerts
# are never sent, but the stock is marked as seen, so they never fire again.
class HalfBroken(SiteAdapter):
    key = "half"
    label = "HALF"
    listing_url = "https://half.example/shop"
    target_sizes = ("4",)
    request_delay = 0

    def __init__(self):
        self.break_midway = True

    def collect(self, session, fetch):
        yield Stock("EARLY COAT", "https://half.example/p/early", ["4"])
        if self.break_midway:
            raise SiteLooksBroken("feed changed halfway through")
        yield Stock("LATE COAT", "https://half.example/p/late", ["4"])


half = HalfBroken()
ADAPTERS["half"] = half
config.STATE_FILE = tmp / "half.json"

sent.clear()
rc = runner.run(site_keys=["half"])
check("half-broken run is unhealthy", rc, 1)
check("half-broken run alarmed", "broken" in sent[0][0], True)
check("half-broken run recorded nothing at all", load_state().get("half", {}), {})

half.break_midway = False
sent.clear()
rc = runner.run(site_keys=["half"])
check("once healthy, the run succeeds", rc, 0)
check("the alert that was staged during the broken run is not lost",
      "EARLY COAT" in sent[0][1], True)
check("and the product it never reached is announced too",
      "LATE COAT" in sent[0][1], True)
del ADAPTERS["half"]

# ── Run 12: dry-run pushes nothing and writes no state ──────────────────
config.STATE_FILE = tmp / "dryrun.json"
rc = run_comoli(dry_run=True)
check("dry run is healthy", rc, 0)
check("dry run sends nothing", len(sent), 0)
check("dry run writes no state file", config.STATE_FILE.exists(), False)

# ── Run 13: missing credentials must fail loudly, not silently ──────────
config.STATE_FILE = tmp / "nocreds.json"
runner.have_credentials = lambda: False
rc = run_comoli()
check("missing credentials fails the run", rc, 1)
check("missing credentials writes no state", config.STATE_FILE.exists(), False)
check("dry run still works without credentials", run_comoli(dry_run=True), 0)
check("seeding still works without credentials", run_comoli(seed=True), 0)
runner.have_credentials = lambda: True

# ── Run 14: an unreadable state file must not be overwritten ────────────
config.STATE_FILE = tmp / "corrupt.json"
config.STATE_FILE.write_text('{"comoli": {"a": ', encoding="utf-8")
rc = run_comoli()
check("corrupt state fails the run", rc, 1)
check("corrupt state raised an alarm", "unreadable" in sent[0][0], True)
check("corrupt state file was left alone",
      config.STATE_FILE.read_text(encoding="utf-8"), '{"comoli": {"a": ')

# ── Run 15: a flood is capped instead of pushing dozens of times ────────
config.STATE_FILE = tmp / "flood.json"
FLOOD = {"https://www.comoli.jp/mailorder":
         "".join(f'<a href="/mailorder/f{i}">FLOOD ITEM NUMBER {i}</a>'
                 for i in range(300))}
for i in range(300):
    FLOOD[f"https://www.comoli.jp/mailorder/f{i}"] = "<p>4</p>"
runner.fetch = lambda session, url: FLOOD.get(url)
rc = run_comoli()
check("flood run is healthy", rc, 0)
check("flood is capped, not one push per chunk",
      len(sent) <= config.MAX_PUSHES_PER_SITE + 1, True)
check("flood says how much was held back",
      any("held back" in title for title, _, _ in sent), True)

# ── Run 16: products long gone are forgotten, and can return as new ─────
config.STATE_FILE = tmp / "prune.json"
runner.fetch = lambda session, url: real_pages.get(url)
run_comoli()
state = load_state()
old = (datetime.now(timezone.utc)
       - timedelta(days=config.STATE_TTL_DAYS + 5)).isoformat(timespec="seconds")
state["comoli"]["https://www.comoli.jp/mailorder/ancient"] = {
    "name": "LONG GONE COAT", "sizes": ["4"], "changed_at": old, "last_seen": old,
}
config.STATE_FILE.write_text(json.dumps(state), encoding="utf-8")

rc = run_comoli()
check("stale product is forgotten",
      "https://www.comoli.jp/mailorder/ancient" in load_state()["comoli"], False)
check("products seen this run are kept", JACKET in load_state()["comoli"], True)

# last_seen must still be refreshed once it goes stale, or nothing would
# ever age out of the file.
state = load_state()
state["comoli"][TEE]["last_seen"] = old
config.STATE_FILE.write_text(json.dumps(state), encoding="utf-8")
run_comoli()
check("a stale last_seen is refreshed when the product is seen again",
      load_state()["comoli"][TEE]["last_seen"] != old, True)

# A product that leaves the site and returns is genuinely new again.
state = load_state()
state["comoli"][JACKET]["last_seen"] = old
config.STATE_FILE.write_text(json.dumps(state), encoding="utf-8")
runner.fetch = lambda session, url: (LISTING if url.endswith("mailorder")
                                     else real_pages.get(url))
run_comoli()          # jacket seen again, so it is NOT pruned
check("a product seen again keeps its history", JACKET in load_state()["comoli"], True)

# ── Run 17: a slow site stops at its budget instead of being killed ─────
class FakeClock:
    """Stand-in for the time module inside runner."""
    def __init__(self):
        self.now = 0.0
    def monotonic(self):
        self.now += 100.0        # every product costs 100 seconds
        return self.now


config.STATE_FILE = tmp / "slow.json"
runner.fetch = lambda session, url: MANY.get(url)
real_time, runner.time = runner.time, FakeClock()
rc = run_comoli()
runner.time = real_time
check("a site that overruns its budget reports it", rc, 1)
check("overrun raised the time alarm",
      any("ran out of time" in title for title, _, _ in sent), True)
check("overrun still kept what it managed to read",
      len(load_state()["comoli"]) > 0, True)

# ── Multi-site: a second brand, failure isolation, crash isolation ──────
OTHER = {
    "https://example-brand.jp/shop": '<a href="/p/coat">WOOL COAT</a>',
    "https://example-brand.jp/p/coat": "<p>M</p><p>L</p>",
}
COAT = "https://example-brand.jp/p/coat"


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
        page = fetch(session, COAT)
        if page is None:
            yield Stock("WOOL COAT", COAT, None)
            return
        # Deliberately lower-case: the engine normalises, so an adapter that
        # spells sizes differently from its targets must still match.
        yield Stock("WOOL COAT", COAT,
                    [t.lower() for t in ("M", "L") if f"<p>{t}</p>" in page])


ADAPTERS["example"] = ExampleBrand()
config.STATE_FILE = tmp / "multi.json"

ALL = dict(real_pages)
ALL["https://www.comoli.jp/mailorder"] = LISTING
ALL[JACKET] = "<p>4<span>/</span></p>"
ALL.update(OTHER)
runner.fetch = lambda session, url: ALL.get(url)

sent.clear()
rc = runner.run()
check("multi-site run is healthy", rc, 0)
check("multi-site alerted for both brands", len(sent), 2)
check("both brands present in titles",
      sorted(t.split(":")[0] for t, _, _ in sent), ["COMOLI", "EXAMPLE"])
check("state holds both sites", sorted(load_state()), ["comoli", "example"])
check("engine normalised an adapter's lower-case sizes",
      sizes_for(COAT, "example"), ["M", "L"])

# One brand down must not stop the other, and must not lose its state.
OTHER[COAT] = "<p>M</p>"           # L sells out, M steady
ALL.update(OTHER)
runner.fetch = lambda session, url: None if "comoli.jp" in url else ALL.get(url)
sent.clear()
rc = runner.run()
check("a dead site makes the run unhealthy", rc, 1)
check("the dead site alarmed once", sum("unreachable" in t for t, _, _ in sent), 1)
state = load_state()
check("healthy site still updated its state", state["example"][COAT]["sizes"], ["M"])
check("dead site kept its previous state", state["comoli"][JACKET]["sizes"], ["4"])


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
runner.fetch = lambda session, url: ALL.get(url)
sent.clear()
rc = runner.run()
check("a crashing adapter makes the run unhealthy", rc, 1)
check("the crash was reported", sum("crashed" in t for t, _, _ in sent), 1)
check("other sites still ran despite the crash",
      "example" in load_state() and "comoli" in load_state(), True)

# ── Graphpaper: the Shopify JSON path, end to end ──────────────────────
del ADAPTERS["comoli"]
del ADAPTERS["example"]
del ADAPTERS["boom"]
gp = Graphpaper()
gp.request_delay = 0
ADAPTERS["graphpaper"] = gp
config.STATE_FILE = tmp / "gp.json"

GP_FEED = {"products": [
    {"handle": "jacket-a", "title": "WOOL RIPSTOP SHIRT JACKET",
     "options": [{"name": "Color name", "position": 1},
                 {"name": "Size", "position": 2}],
     "variants": [{"option1": "ASH", "option2": "1", "available": True},
                  {"option1": "ASH", "option2": "2", "available": False}]},
    {"handle": "coat-b", "title": "STAND COLLAR COAT",
     "options": [{"name": "Color name", "position": 1},
                 {"name": "Size", "position": 2}],
     "variants": [{"option1": "NAVY", "option2": "2_INT", "available": True}]},
]}
GP_URL = ("https://eng.graphpaper-tokyo.com/collections/mens-global"
          "/products.json?limit=250&page=1")
COAT_B = "https://eng.graphpaper-tokyo.com/products/coat-b"

runner.fetch = lambda session, url: json.dumps(GP_FEED) if url == GP_URL else None
sent.clear()
rc = runner.run()
check("graphpaper run is healthy", rc, 0)
check("graphpaper alerted once", len(sent), 1)
check("graphpaper alerted on the 2_INT coat", "STAND COLLAR COAT" in sent[0][1], True)
check("graphpaper did not alert on the sold-out size 2 jacket",
      "SHIRT JACKET" in sent[0][1], False)
check("graphpaper normalised the underscore spelling",
      sizes_for(COAT_B, "graphpaper"), ["2-INT"])

GP_FEED["products"][0]["variants"][1]["available"] = True
sent.clear()
rc = runner.run()
check("graphpaper alerts when size 2 restocks", len(sent), 1)
check("that alert is the jacket", "SHIRT JACKET" in sent[0][1], True)

sent.clear()
check("graphpaper is silent when nothing changed", (runner.run(), len(sent)), (0, 0))

# A malformed feed must alarm and leave the memory intact.
before_gp = dict(load_state()["graphpaper"])
runner.fetch = lambda session, url: "<html>not json</html>"
sent.clear()
rc = runner.run()
check("graphpaper alarms on a non-JSON feed", rc, 1)
check("graphpaper named it as broken", "broken" in sent[0][0], True)
check("broken feed did not touch saved stock", load_state()["graphpaper"], before_gp)

runner.fetch = lambda session, url: json.dumps({"items": []})
sent.clear()
check("graphpaper alarms on an unexpected JSON shape", runner.run(), 1)

runner.fetch = lambda session, url: None
sent.clear()
rc = runner.run()
check("graphpaper alarms when unreachable", rc, 1)
check("graphpaper unreachable text", "unreachable" in sent[0][0], True)

# ── Seed mode: record current stock silently, then stay quiet ───────────
ADAPTERS.clear()
ADAPTERS["comoli"] = comoli
FRESH = {
    "https://www.comoli.jp/mailorder": LISTING,
    JACKET: "<p>4<span>/</span></p><p>5</p>",
    TEE: "<p>1<span>/</span></p>",
}
runner.fetch = lambda session, url: FRESH.get(url)
config.STATE_FILE = tmp / "seeded.json"

sent.clear()
rc = runner.run(seed=True)
check("seed run is healthy", rc, 0)
check("seed run pushes nothing", len(sent), 0)
check("seed run still wrote state", sizes_for(JACKET), ["4", "5"])

sent.clear()
check("run after seeding is silent", (runner.run(), len(sent)), (0, 0))

FRESH[TEE] = "<p>1<span>/</span></p><p>4</p>"
sent.clear()
rc = runner.run()
check("restock after seeding still alerts", len(sent), 1)
check("that alert is the tee in size 4",
      "TEE" in sent[0][1] and "size 4" in sent[0][1], True)

report("All end-to-end checks passed.")
