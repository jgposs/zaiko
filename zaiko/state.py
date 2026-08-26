"""Persistent memory of what was in stock last run.

Shape:
    {"<site key>": {"<product url>": {"name", "sizes", "changed_at", "last_seen"}}}

Committed back to the repo by the workflow, so its git history doubles as a
record of what dropped when.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone

from . import config


class StateUnreadable(Exception):
    """The state file exists but could not be parsed.

    Treated as fatal rather than as 'start fresh': starting fresh would mean
    announcing the entire catalogue as new, and committing that over the only
    copy of the real memory.
    """


def load_state() -> dict:
    path = config.STATE_FILE
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise StateUnreadable(str(e))
    if not isinstance(data, dict):
        raise StateUnreadable("top level is not an object")
    return data


def save_state(state: dict) -> None:
    """Write atomically. A half-written state file is worse than a stale one:
    the next run would read it as corrupt and lose the whole memory.

    sort_keys keeps the committed diff stable, so `git log state.json` reads
    as a clean history of what actually changed and when.
    """
    path = config.STATE_FILE
    body = json.dumps(state, indent=2, ensure_ascii=False, sort_keys=True) + "\n"

    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".state-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(body)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, str(path))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def prune(site_state: dict, seen_urls: set, now: datetime) -> int:
    """Drop products not seen for STATE_TTL_DAYS. Returns how many went.

    Without this the file grows forever, and — worse — a product that leaves
    the site and returns a season later is compared against its old entry and
    never announced. Only ever call this after a run you trust.
    """
    cutoff = now - timedelta(days=config.STATE_TTL_DAYS)
    dropped = []

    for url, entry in site_state.items():
        if url in seen_urls:
            continue
        stamp = (entry or {}).get("last_seen")
        if not stamp:
            # Pre-dates last_seen tracking; adopt now so it ages from here.
            entry["last_seen"] = now.isoformat(timespec="seconds")
            continue
        try:
            last_seen = datetime.fromisoformat(stamp)
        except ValueError:
            entry["last_seen"] = now.isoformat(timespec="seconds")
            continue
        if last_seen < cutoff:
            dropped.append(url)

    for url in dropped:
        del site_state[url]
    return len(dropped)
