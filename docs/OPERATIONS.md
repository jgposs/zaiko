# Operations

## Everyday commands

```bash
cd ~/zaiko
python3 run.py                    # check every site (needs credentials)
python3 run.py --site comoli      # one brand
python3 run.py --dry-run          # hit the network, push nothing, don't save state
python3 run.py --seed             # record current stock silently
python3 run.py --list             # registered sites and their target sizes

python3 tests/test_parser.py      # offline parsing tests
python3 tests/test_e2e.py         # full runs, network stubbed
```

Stock Python 3.9 on macOS is enough; `python3 -m pip install --user -r
requirements.txt` once.

## How it runs on its own

`.github/workflows/zaiko.yml` — daily at `5 15 * * *` (00:05 JST; Japan has no
DST). Also dispatchable from the Actions tab with two inputs: `site` (blank =
all) and `mode` (`normal`, `seed`, `dry-run`).

**Scheduled runs are best-effort.** The first real one fired 80 minutes late.
GitHub also drops scheduled events entirely under load, without retrying. Late
is normal; a missing run for a whole day is worth a manual dispatch rather than
a debugging session.

After each run the workflow commits `state.json` if — and only if — it changed.
A day where nothing dropped should produce **no commit at all**.

`.github/workflows/tests.yml` runs both suites on Python 3.9 and 3.12 on push.

## Secrets

`PUSHOVER_USER_KEY` and `PUSHOVER_API_TOKEN` in repo secrets. They survive
visibility changes, and are not exposed by the repo being public.

A missing secret now fails the run loudly rather than delivering nothing
quietly. A *wrong* secret surfaces as a delivery failure, which also fails the
run and leaves the state untouched so the alert is re-sent — it will never look
like silence.

## Reading an alarm

Every alarm titled `⚠️ Zaiko: <brand> …` means the run was abandoned and the
state left as it was. Nothing is lost; the alerts are re-derived next run.

| alarm | what happened | what to do |
|---|---|---|
| `unreachable` | listing/feed failed every retry | usually transient; check the site by hand |
| `monitor may be broken` | responded in an unrecognised shape | the site changed — fix the adapter |
| `mostly unreadable` | more than half the products failed | rate limiting or a partial redesign |
| `ran out of time` | site exceeded its time budget | alerts still sent; nothing pruned |
| `crashed` | the adapter raised | a bug; other brands still ran |
| `state file unreadable` | `state.json` won't parse | see recovery below |

## Recovery

**Corrupt `state.json`.** The run refuses to overwrite it. Restore from history:

```bash
git log --oneline -- state.json
git checkout <good-sha> -- state.json
git commit -m "restore state" && git push
```

Or delete it and re-seed — you lose the drop history, and the next run announces
nothing because seeding is silent.

**Alert flood.** Capped at 5 pushes per site per run, with a "held back" message
and the full list in the Actions log. A flood means the state was lost or a
parser changed, not that a hundred coats appeared. Check `state.json` before
assuming the shop restocked.

**Actions blocked on billing.** Public repos get unlimited GitHub-hosted
minutes, so this shouldn't recur. If it does, the tell is a run that completes
in ~2 seconds with zero steps and no runner assigned — that is a billing block
at the account level, not a code failure. This once went unnoticed for six days.

**Pushing after a bot commit.** The workflow commits `state.json`, so your local
clone goes behind. `git pull --rebase && git push`. Pull *before* staging, or
the pull refuses on a dirty index.

## Health check

Nothing alerts when everything is fine, which is the point and also the risk.
Once in a while:

- Is there a recent successful `Zaiko Stock Monitor` run in the Actions tab?
- Does `python3 run.py --dry-run` still report plausible product counts?
  (27 for Comoli, 232 for Graphpaper, as of August 2026.)
- Does `git log state.json` show changes on days something actually dropped?

A run that succeeds while reading zero products is the failure mode with no
natural alarm, which is why the counts are worth knowing by eye.
