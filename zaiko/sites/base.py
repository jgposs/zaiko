"""The contract every brand adapter implements.

An adapter's one required job is `collect`: hand back what is in stock right
now, product by product. How it gets there is its own business — COMOLI walks
a listing page and fetches each product, Graphpaper reads a Shopify JSON feed
in a couple of requests. The engine doesn't care.

Everything else — remembering last run, deciding what counts as news,
notifying, and the failure alarms — is shared and lives in runner.py.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterator


@dataclass(frozen=True)
class Product:
    """A product found on a listing page."""
    name: str
    url: str


@dataclass(frozen=True)
class Stock:
    """What one product looks like this run.

    sizes=None means 'could not be read' — deliberately different from an
    empty list, which means 'read fine, nothing in stock'. The engine keeps
    the previous state for None so a blip can't look like a restock later.
    """
    name: str
    url: str
    sizes: list[str] | None


class SiteUnavailable(Exception):
    """The site could not be reached at all this run."""


class SiteLooksBroken(Exception):
    """The site answered, but not in a shape we recognise — markup or API
    probably changed under us."""


def normalize_size(raw: str) -> str:
    """Fold the ways a shop might spell the same size into one token.

    '2_INT', '2-int', ' 2 INT ' all become '2-INT', so target_sizes can be
    written the obvious way and still match.
    """
    return re.sub(r"[\s_\-]+", "-", str(raw).strip().upper())


class SiteAdapter:
    # ── identity ────────────────────────────────────────────────
    key: str = ""             # short slug; the state file is keyed by this
    label: str = ""           # human name used in notification titles
    base_url: str = ""
    listing_url: str = ""

    # ── what to watch ───────────────────────────────────────────
    target_sizes: tuple[str, ...] = ()

    # ── politeness ──────────────────────────────────────────────
    request_delay: float = 0.4          # seconds between requests
    accept_language: str = "ja,en;q=0.8"

    # ── the one required method ─────────────────────────────────
    def collect(self, session, fetch) -> Iterator[Stock]:
        """Yield a Stock for every product on the site.

        `fetch(session, url) -> str | None` is passed in rather than imported
        so the engine (and tests) control how requests are made.

        Raise SiteUnavailable if the site is unreachable, or SiteLooksBroken
        if it responds with something unrecognisable. Both raise an alert
        rather than passing silently.
        """
        raise NotImplementedError

    # ── optional presentation ───────────────────────────────────
    def display_name(self, name: str) -> str:
        """Trim a raw product label down to something readable in a push."""
        return name

    # ── shared helpers ──────────────────────────────────────────
    @property
    def normalized_targets(self) -> tuple[str, ...]:
        return tuple(normalize_size(s) for s in self.target_sizes)

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.key}>"
