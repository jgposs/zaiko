"""Shared tunables. Anything you might want to twist lives here."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# One state file for every site, keyed by site then product URL.
STATE_FILE = REPO_ROOT / "state.json"

# Be a polite client: identify as a normal browser, pause between hits.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
REQUEST_TIMEOUT = 20      # seconds
MAX_RETRIES = 3

PUSHOVER_LIMIT = 900      # chars; Pushover's hard cap is 1024
PUSHOVER_RETRIES = 3

# Never send more than this many pushes for one site in one run. Beyond it the
# rest are collapsed into a single "and N more" message. Without a cap, a
# lost state file turns into hundreds of high-priority pushes at midnight.
MAX_PUSHES_PER_SITE = 5

# Wall-clock budget per site. The workflow's own timeout kills the whole job
# and loses every site still queued behind a slow one, so stop ourselves first
# and report it.
SITE_TIME_BUDGET = 300    # seconds

# How stale a product's last_seen may get before it is rewritten. Stamping it
# every run would make state.json produce a ~260-line diff daily even when no
# stock moved, burying the real changes its git history exists to record.
LAST_SEEN_REFRESH_DAYS = 7

# Forget a product this long after it was last seen on the site. Without this
# the state file grows forever, and a delisted product that returns months
# later is compared against year-old data and never announced.
STATE_TTL_DAYS = 60

# A run where most products failed to load is not a healthy run, even if a
# few came back fine. Alarm above this share.
MAX_FAILURE_RATIO = 0.5
