"""Adapter registry. Add a brand here and the runner picks it up."""

from __future__ import annotations

from .base import SiteAdapter
from .comoli import Comoli
from .graphpaper import Graphpaper
from .kent import Kent
from .neighbour import Neighbour

ADAPTERS: dict[str, SiteAdapter] = {
    a.key: a for a in (Comoli(), Graphpaper(), Kent(), Neighbour())
}


def resolve(keys: list[str] | None = None) -> list[SiteAdapter]:
    """Return the adapters to run. No keys means every registered site."""
    if not keys:
        return list(ADAPTERS.values())
    unknown = [k for k in keys if k not in ADAPTERS]
    if unknown:
        known = ", ".join(sorted(ADAPTERS))
        raise SystemExit(f"Unknown site(s): {', '.join(unknown)}. Known: {known}")
    return [ADAPTERS[k] for k in keys]
