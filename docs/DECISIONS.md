# Decisions

Why things are the way they are, including the parts that were wrong first.
Newest last.

## Engine + adapters, rather than one script per brand

The monitor began as a single Comoli script. Generalising it as "core engine,
one adapter per brand" was chosen over copying the script because the valuable
part isn't the parsing — it's the careful failure handling, and that should
exist once. A brand is now one file and one registry line.

## An adapter's contract is `collect()`, not `parse_page()`

The first version of the contract assumed every site works like Comoli: a
listing, then one request per product. Graphpaper is Shopify and needs neither —
one JSON request returns the whole catalogue. Rather than bend Graphpaper into
the wrong shape, the contract became "yield what's in stock, however you like",
which is the only thing the two sites genuinely have in common.

## Three-valued stock, not two

`None` (couldn't read) is deliberately distinct from `[]` (read, nothing in
stock). This is the single most important decision in the codebase and the one
most often got wrong. See `docs/ARCHITECTURE.md`.

## Safety lives in the engine

Size normalisation was originally done per-adapter and moved to the engine after
noticing that a new adapter spelling sizes differently from its own
`target_sizes` would match nothing, silently, forever. The general rule: the
adapter is the thing most likely to be written quickly and wrongly, so it should
own as little as possible.

## Daily, not every 30 minutes

The monitor ran `*/30 * * * *` from May to August 2026, which exhausted the
private repo's Actions minutes and got the account billing-blocked on 19 August.
The block went unnoticed for six days — the monitor had simply stopped running
while appearing to be fine.

The schedule is now daily. Making the repo public removed the cost constraint,
so a higher frequency is affordable again, but the README's "note on use" asks
forks not to hammer small brands and it would be poor form to ignore that
ourselves. Daily suits how these labels drop.

## Seeding

`state.json` was never committed under the old setup, so a fresh checkout
treated the entire catalogue as new — 164 alerts across ~21 pushes on first run.
`--seed` records current stock without notifying. The cost is that anything
in stock at seed time is invisible until it sells out and returns.

## Public repo, MIT

Made public partly for free Actions minutes, partly to share the approach. The
licence is for the warranty disclaimer more than the permission grant: this is a
script that makes requests to other people's sites, and it should say plainly
that it comes with no warranty.

The README leads with a "note on use" for the same reason — the politeness
settings are load-bearing, and a fork that turns the frequency up would be a
real nuisance to a business that never asked to be monitored.

---

## Mistakes, and what they cost

Kept because the pattern in them is more useful than any individual fix.

**The monitor was dead for six days and nothing said so.** A billing block
produced runs that completed in two seconds with no runner. Silence looked
exactly like "nothing in your size". Everything in the failure-alarm design
descends from this.

**Alerts were generated, undelivered, and recorded as seen.** Three separate
paths: a refused Pushover push, an alarm firing after products were already
staged, and an exception midway through `collect()`. In each case the run
reported success and the alert was gone forever. Fixed by the rule that an
unhealthy run restores the state it started with.

**`last_seen` was stamped every run.** Added for pruning; it made `state.json`
produce a ~260-line diff daily with no stock having moved, destroying the git
history that the file is committed *for*. Now refreshed only once a week.
The lesson generalised: **before adding a field to a state entry, ask what it
does to the diff of an unchanged run.**

**A reshuffled list counted as a change.** Immediately after the `last_seen`
fix, the next run still committed a diff: the same sizes had come back in a
different order and `changed_at` was stamped for it. Sizes are now compared as
sets. Two non-changes writing themselves into the history in two days is the
argument for the standing question — **any write to a state entry must justify
itself against the diff of an unchanged run.**

**A test passed for the wrong reason — twice.** First, an assertion of the form
`url in body` passed trivially against an empty string. Second, the test for the
`last_seen` fix compared two runs that both landed in the same second, so
second-resolution timestamps matched even while being rewritten. Both were
caught only by mutation testing.

Hence the standing rule: **after fixing something, break it again and watch the
test fail.** A green suite is evidence of nothing until you've seen it go red
for the reason you expect.

**The schedule was described from the file, not the run history.** The workflow
said daily, so it was called daily — but the repo had actually been running
every 30 minutes until a week earlier. Read what a system *did*, not only what
its config says it should do.

**A deleted Shopify collection answers 200 with an empty list.** Found while
adding the Neighbour store, by asking the live site for a collection that
doesn't exist rather than assuming it would 404. Every status-code check in the
world would have called that feed healthy, and the run would have recorded
COMOLI as sold out in every size at that shop and then said nothing again. It
was already handled — `total == 0` raises `SiteLooksBroken` — but by accident
rather than intent, and untested. Now both. The general form: **ask what a
watched thing's disappearance actually looks like on the wire, and check that
the code can tell it from an ordinary quiet day.**

**One brand can need two adapters.** COMOLI is watched at its own mailorder and
at a stockist. They are separate site keys with separate state, because a
garment sold out in Japan and in stock in Vancouver is not one fact, and
merging them would silence the shop that has it.

**A null size became the string "None", and that read as success.** Found by
reviewing the Shopify adapter for null dereferences rather than by anything
going wrong. `variant.get("option2", "")` returns the default only when the key
is *absent*; Shopify sends the key with a null value, so `str()` turned it into
`"None"` — a size token that normalises fine, matches no target, and leaves the
product recorded as read-and-uninteresting. Every other bug of this shape in
this repo has been an adapter returning `[]` where it meant `None`; this one
returned a populated list, which is worse, because a non-empty answer looks
healthier than an empty one. It had never fired in the wild — 310 state entries,
zero `NONE` values — and would have stayed invisible when it did.

The generalisation, which is now the third variant of the same lesson: **the
dangerous failure is not the one that returns nothing, it is the one that
returns something plausible.** `[]` for unknown, the colour where the size
should be, and `"None"` for a null are all the same mistake — a confident
answer the adapter had no right to give.
