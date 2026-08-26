# Zaiko 在庫

Stock monitor for Japanese fashion brand shops. It walks each brand's online
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
- **Graphpaper is read through `products.json`**, the same public endpoint the
  store's own frontend uses — no page scraping at all.

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
    comoli.py           COMOLI — scrapes comoli.jp/mailorder
    graphpaper.py       Graphpaper — reads the Shopify JSON feed
tests/
  test_parser.py        parsing and message handling, offline
  test_e2e.py           full runs with the network stubbed out
```

## Sites

| key | brand | sizes | how it reads stock |
|---|---|---|---|
| `comoli` | COMOLI | 4, 5 | scrapes the mail order listing, then each product page |
| `graphpaper` | Graphpaper | 2, 2-INT | reads the Shopify `products.json` feed for the mens-global collection |

Sizes are matched loosely: `2_INT`, `2-int` and `2 INT` all count as `2-INT`,
so you can write target sizes the obvious way regardless of how a shop spells
them.

## Adding a brand

An adapter has exactly one required job — `collect`, which yields what is in
stock right now, product by product. How it gets there is its own business:
COMOLI walks a listing and fetches each product page, Graphpaper reads a JSON
feed in one request. The engine handles the rest — state, change detection,
alerting, and the failure alarms.

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

ADAPTERS = {a.key: a for a in (Comoli(), Graphpaper(), YourBrand())}
```

## Failure behaviour

The awkward part of a stock monitor is not finding stock — it is not lying to
you when something breaks. Zaiko treats these as distinct:

- **A page fails to load.** Its previous state is left untouched, so a network
  blip can't read as "sold out" today and "restocked!" tomorrow.
- **Pages load but no sizes parse anywhere.** The site markup probably changed.
  You get a *monitor may be broken* push rather than silence.
- **The listing page is empty or unreachable.** Same — you're told, and the run
  exits non-zero.
- **One brand is down or its adapter crashes.** The other brands still run, and
  the healthy ones still update their state.

Silence from Zaiko means "nothing new in your size", never "something quietly
went wrong."

## License

MIT — see [LICENSE](LICENSE). Provided as is, with no warranty.
