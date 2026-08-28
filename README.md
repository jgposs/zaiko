# Zaiko 在庫

Stock monitor for online fashion shops. It walks each brand's online
store once a day and sends a Pushover push the moment something appears in a
size you actually wear — a new drop, or a restock of something that had sold
out.

It only tells you what *changed*. An item that is still sitting in your size
from yesterday stays quiet.

## A note on use

This is a personal project — one person checking a couple of shops for their
own size, once a day. It's public because that's the easiest way to run it and
because someone else may find the approach useful, not because it's a product.

The politeness settings are deliberate, and they matter more than they look:

- **Once a day.** These are small independent labels, not marketplaces. A daily
  check is plenty for the way they drop stock, and it costs them nothing.
- **Delays between requests**, and a normal browser user agent, so a run looks
  like one person browsing rather than a machine.
- **The Shopify shops are read through `products.json`**, the same public
  endpoint their own frontends use — no page scraping at all.

If you fork this: please keep the schedule and the delays where they are, and
use your own Pushover credentials. The whole point is that a stock alert
shouldn't cost the shop anything. Turning the frequency up doesn't get you the
item much sooner, but a hundred forks all polling hard would be a genuine
nuisance to a business that never asked to be monitored.

Nothing here bypasses a paywall, a login, a queue, or a bot protection, and it
shouldn't be extended to. It reads pages that are already public, at a pace a
person could manage by hand.

## Running it

Needs Python 3.9 or newer — which means stock macOS works with nothing
installed.

```bash
pip3 install -r requirements.txt

python3 run.py                 # check every registered site
python3 run.py --site comoli   # just one
python3 run.py --dry-run       # hit the network, push nothing, don't save state
python3 run.py --seed          # record current stock silently (see below)
python3 run.py --list          # show what's registered
```

Pushover credentials come from the environment. Without them the script still
runs and prints what it *would* have sent:

```bash
export PUSHOVER_USER_KEY=...
export PUSHOVER_API_TOKEN=...
```

In GitHub Actions they live in repo secrets of the same names.

## Seeding

`state.json` is the monitor's memory. On a fresh checkout it's empty, which
means *everything* currently in stock in your size counts as new — the first
run would push the whole catalogue at you.

Run it once in seed mode to record what's in stock right now without sending
anything:

```bash
python3 run.py --seed
```

From then on you only hear about changes. The workflow exposes the same thing:
run it by hand from the Actions tab with **mode = seed**.

## How it runs on its own

`.github/workflows/zaiko.yml` fires daily at 00:05 JST (`cron: "5 15 * * *"` —
Japan has no DST, so this holds all year). It can also be run by hand from the
Actions tab, where you can limit it to a single site and pick a mode
(`normal`, `seed`, or `dry-run`).

After each run the workflow commits `state.json` back to the repo. That file is
the monitor's memory of what was in stock last time, so its git history doubles
as a record of what dropped when.

`.github/workflows/tests.yml` runs the offline test suite on every push.

## Layout

```
run.py                  CLI entry point
zaiko/
  config.py             timeouts, user agent, state file location
  http.py               fetching with retries
  notify.py             Pushover delivery and message chunking
  state.py              load/save the stock memory
  runner.py             the engine — shared by every site
  sites/
    base.py             the SiteAdapter contract
    shopify.py          shared reader for Shopify collection feeds
    comoli.py           COMOLI — scrapes comoli.jp/mailorder
    graphpaper.py       Graphpaper — Shopify feed
    neighbour.py        COMOLI at Neighbour — Shopify feed
tests/
  test_parser.py        parsing and message handling, offline
  test_e2e.py           full runs with the network stubbed out
```

## Sites

| key | brand | sizes | how it reads stock |
|---|---|---|---|
| `comoli` | COMOLI | 4, 5 | scrapes the mail order listing, then each product page |
| `graphpaper` | Graphpaper | 2, 2-INT | reads the Shopify `products.json` feed for the mens-global collection |
| `neighbour-comoli` | COMOLI, at the Neighbour store | 4, 5 | reads the Shopify `products.json` feed for the comoli-mens collection |

A brand can appear twice. COMOLI's own mailorder and a stockist that carries
it are separate sites with separate state: the same garment in two shops is
two independent answers to "is it in stock in a 4?", and the push title says
which shop so the link goes where you expect.

Sizes are matched loosely: `2_INT`, `2-int` and `2 INT` all count as `2-INT`,
so you can write target sizes the obvious way regardless of how a shop spells
them.

## Adding a brand

An adapter has exactly one required job — `collect`, which yields what is in
stock right now, product by product. How it gets there is its own business:
COMOLI walks a listing and fetches each product page; the Shopify shops read
a JSON feed in one request, and share `ShopifyCollectionAdapter` to do it.
The engine handles the rest — state, change detection, alerting, and the
failure alarms.

```python
# zaiko/sites/yourbrand.py
from .base import SiteAdapter, SiteLooksBroken, SiteUnavailable, Stock

class YourBrand(SiteAdapter):
    key = "yourbrand"                          # state file is keyed by this
    label = "YOUR BRAND"                       # shown in push titles
    base_url = "https://yourbrand.jp"
    listing_url = "https://yourbrand.jp/shop"
    target_sizes = ("M", "L")                  # sizes are per brand

    def collect(self, session, fetch):
        html = fetch(session, self.listing_url)
        if html is None:
            raise SiteUnavailable("listing failed to load")
        products = ...                          # parse however the site needs
        if not products:
            raise SiteLooksBroken("no products found")

        for name, url in products:
            page = fetch(session, url)
            if page is None:
                yield Stock(name, url, None)    # None = couldn't read it
                continue
            yield Stock(name, url, sizes_in_stock(page))
```

`Stock(..., sizes=None)` means "couldn't read this one" and is treated very
differently from `sizes=[]`, which means "read fine, nothing in stock". Getting
that distinction right is what stops a network blip from becoming a fake
restock alert tomorrow.

Then register it in `zaiko/sites/__init__.py`:

```python
from .yourbrand import YourBrand

ADAPTERS = {a.key: a for a in (Comoli(), Graphpaper(), Neighbour(), YourBrand())}
```

## Failure behaviour

The awkward part of a stock monitor is not finding stock — it is not lying to
you when something breaks. Silence has to mean "nothing new in your size", and
never "something went quietly wrong."

The rule everything else follows from: **a run only updates its memory if it
went well.** An unhealthy run keeps yesterday's state, so whatever it couldn't
be sure about is worked out again tomorrow rather than being recorded as
already seen.

What that looks like in practice:

- **A page fails to load,** or loads with no size markup at all — a soft 404, an
  interstitial, a redesign. That product keeps its previous state. "Couldn't
  read it" is never recorded as "sold out", so there's no fake restock the day
  it recovers.
- **Most products fail in one run.** Rate limiting and half-broken redesigns
  look like this, and reporting on the handful that still parsed would be
  worse than useless. You get an alarm instead.
- **The listing or feed is unreachable, empty, or the wrong shape.** Alarm, and
  the run exits non-zero.
- **A push doesn't reach Pushover.** The state is left untouched so the alert
  is re-sent next run, rather than being marked seen and lost forever.
- **The credentials are missing.** The run stops rather than "succeeding"
  while delivering nothing.
- **The state file is unreadable.** The run refuses to overwrite it, because
  starting fresh would announce the whole catalogue and destroy the only copy
  of the real memory. Restore it from git history, or delete it and re-seed.
- **A site takes too long.** Each site gets its own time budget, so a slow shop
  can't eat the workflow's timeout and starve the other brands behind it.
- **One brand is down, or its adapter crashes.** The others still run and still
  save their state.
- **A flood.** No more than five pushes per site per run; the rest are collapsed
  into one "held back" message with the detail in the Actions log. Hundreds of
  alerts at once means something upstream broke, not that hundreds of coats
  appeared overnight.

Products that disappear from a site are forgotten after 60 days, so the state
file doesn't grow forever and a genuinely new listing of the same URL later on
is announced rather than compared against year-old data.

## Documentation

- [Architecture](docs/ARCHITECTURE.md) — how it works, and the invariant it all serves
- [Adding a brand](docs/ADDING_A_BRAND.md) — the adapter recipe, and the traps
- [Operations](docs/OPERATIONS.md) — running it, reading its alarms, recovery
- [Decisions](docs/DECISIONS.md) — why it is this way, including what went wrong
- [Roadmap](docs/ROADMAP.md) — where it is going

## License

MIT — see [LICENSE](LICENSE). Provided as is, with no warranty.
