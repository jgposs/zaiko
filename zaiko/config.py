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
