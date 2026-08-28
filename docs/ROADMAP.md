# Roadmap

Ordered by intent: make it excellent for one person first, then open it up.

## Now — more brands

The adapter layer exists for this; the work is mostly reconnaissance. For each
candidate brand, check for a Shopify `products.json` or an XHR feed *before*
writing any HTML parsing (see `docs/ADDING_A_BRAND.md`).

Worth thinking through as the roster grows:

- **Per-brand target sizes** already work. Sizing varies more than expected —
  Graphpaper alone runs 1–3 for garments and 7.5–11 for shoes.
- **Runtime** is per-site budgeted, so a slow brand can't starve the others, but
  the workflow's overall timeout will eventually need raising.
- **A watchlist** — alerting on everything in your size stops scaling somewhere
  around brand five. Filtering by keyword, category, or a specific item is the
  natural next feature, and it is a change to the engine, not the adapters.

## Next — history and a dashboard

`state.json`'s git history is already a dataset: every commit is a real stock
change with a timestamp, going back to the first seed. Nothing reads it yet.

Plausible shape: a script that walks `git log -p state.json` into a table of
(product, size, appeared, disappeared), and a static page published from it.
Questions it could answer — what dropped when, how long things last in your
size, whether restocks cluster, which brands actually produce hits.

Design notes for whoever builds it:

- The history is append-only and small; regenerating from scratch each time is
  fine and avoids a second source of truth.
- `changed_at` marks real changes only, which is exactly why the `last_seen`
  churn had to be fixed before this was worth building.
- GitHub Pages from the same repo is the obvious host, and free on a public
  repo.

## Later — usable by other people

Deliberately last. The things that would need to change:

- **Configuration.** Sizes and enabled brands are currently in code. They'd move
  to a config file so a fork is edit-one-file rather than edit-two-modules.
- **Setup docs.** Pushover keys, secrets, seeding, and the first dry-run as a
  numbered path rather than institutional knowledge.
- **Notification channels.** Pushover is assumed throughout `notify.py`. A
  second channel needs a decision on what "delivered" means when one succeeds
  and another fails — probably "any channel succeeded", or a flaky secondary
  would cause endless re-alerting on the primary.
- **Politeness under forking.** The one genuine risk of popularity: fifty forks
  polling hard would be a real burden on small shops. The README asks; a
  jittered default schedule would ask more effectively.

## Known gaps

Deliberately not done, with reasons:

- **No heartbeat.** Nothing proves the monitor is alive on a quiet day beyond
  looking at the Actions tab. A weekly "still watching" push would close the
  last silence-ambiguity, at the cost of noise.
- **Size namespaces don't exist.** Sizes are compared as strings, so two scales
  within one brand could theoretically collide. Not a live problem.
- **No desktop notification.** Considered; Pushover's own desktop client is the
  zero-code option if wanted.
- **`state.json` is public**, which makes the watchlist public too. Accepted
  knowingly.
