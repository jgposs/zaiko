"""Persistent memory of what was in stock last run.

Shape:
    {"<site key>": {"<product url>": {"name", "sizes", "changed_at"}}}

Committed back to the repo by the workflow, so its git history doubles as a
record of what dropped when.
"""

import json

from . import config


def load_state() -> dict:
    path = config.STATE_FILE
    if path.exists():
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError as e:
            print(f"[WARN] State file unreadable ({e}); starting fresh.")
            return {}
        if isinstance(data, dict):
            return data
        print("[WARN] State file has an unexpected shape; starting fresh.")
    return {}


def save_state(state: dict) -> None:
    # sort_keys keeps the committed diff stable, so `git log state.json`
    # reads as a clean history of what actually changed and when.
    config.STATE_FILE.write_text(
        json.dumps(state, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    )
