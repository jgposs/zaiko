#!/usr/bin/env python3
"""End-to-end run() tests with the network stubbed out. No real requests."""

import tempfile
from pathlib import Path

import comoli_monitor as m

failures = []


def check(label, got, want):
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'}  {label}\n        got={got!r} want={want!r}")
    if not ok:
        failures.append(label)


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

sent = []
m.notify = lambda title, message, url="", priority=0: sent.append((title, message))
m.REQUEST_DELAY = 0
m.fetch = lambda session, url: PAGES.get(url)

tmp = Path(tempfile.mkdtemp())
m.STATE_FILE = tmp / "comoli_state.json"

# ── Run 1: first ever run — size 4 on the jacket should alert ───────────
sent.clear()
rc = m.run()
check("run 1 exit code", rc, 0)
check("run 1 sent exactly one notification", len(sent), 1)
check("run 1 alert names the jacket", "REVERSIBLE JACKET" in sent[0][1], True)
check("run 1 alert is for size 4", "size 4" in sent[0][1], True)
check("run 1 did not alert on the tee", "TEE" in sent[0][1], False)
check("state file written", m.STATE_FILE.exists(), True)

# ── Run 2: nothing changed — must stay silent ───────────────────────────
sent.clear()
rc = m.run()
check("run 2 exit code", rc, 0)
check("run 2 is silent (no re-announcement)", len(sent), 0)

# ── Run 3: size 5 restocks on the jacket ────────────────────────────────
PAGES["https://www.comoli.jp/mailorder/jacket"] = \
    "<p>2<span>/</span></p><p>4<span>/</span></p><p>5</p>"
sent.clear()
rc = m.run()
check("run 3 alerts on the restock", len(sent), 1)
check("run 3 alerts for size 5 only", "size 5" in sent[0][1], True)

# ── Run 4: a page fails to load — must NOT be treated as sold out ───────
real_pages = dict(PAGES)
m.fetch = lambda session, url: None if url.endswith("/jacket") else real_pages.get(url)
sent.clear()
rc = m.run()
check("run 4 stays silent when a page fails", len(sent), 0)
before = m.load_state()["https://www.comoli.jp/mailorder/jacket"]["sizes"]
check("run 4 preserved the failed product's sizes", before, ["2", "4", "5"])

# ── Run 5: page recovers unchanged — must not look like a restock ───────
m.fetch = lambda session, url: real_pages.get(url)
sent.clear()
rc = m.run()
check("run 5 no false restock after recovery", len(sent), 0)

# ── Run 6: site markup changes — the broken-monitor alarm must fire ─────
for key in list(real_pages):
    if key != "https://www.comoli.jp/mailorder":
        real_pages[key] = "<div>no sizes here any more</div>"
sent.clear()
rc = m.run()
check("run 6 exit code signals failure", rc, 1)
check("run 6 fired the broken-monitor alarm", len(sent), 1)
check("run 6 alarm text", "may be broken" in sent[0][0], True)

# ── Run 7: listing structure changes — alarm, and no silent success ─────
real_pages["https://www.comoli.jp/mailorder"] = "<div>nothing</div>"
sent.clear()
rc = m.run()
check("run 7 exit code signals failure", rc, 1)
check("run 7 alarmed on empty listing", "may be broken" in sent[0][0], True)

# ── Run 8: site unreachable entirely ────────────────────────────────────
m.fetch = lambda session, url: None
sent.clear()
rc = m.run()
check("run 8 exit code signals failure", rc, 1)
check("run 8 alarmed on unreachable site", "could not reach" in sent[0][0], True)

print()
if failures:
    print(f"{len(failures)} FAILING: {failures}")
    raise SystemExit(1)
print("All end-to-end checks passed.")
