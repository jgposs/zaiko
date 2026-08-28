#!/usr/bin/env python3
"""Zaiko (在庫) — stock monitor for online fashion shops.

    python run.py                      # every registered site
    python run.py --site comoli        # just one
    python run.py --dry-run            # hit the network, push nothing,
                                       # leave state.json untouched
    python run.py --seed               # record current stock silently, so the
                                       # first real run isn't a flood
    python run.py --list               # show registered sites
"""

from __future__ import annotations

import argparse
import sys

from zaiko.runner import run
from zaiko.sites import ADAPTERS


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--site", action="append", dest="sites", metavar="KEY",
                   help="site key to check; repeatable. Default: all.")
    p.add_argument("--dry-run", action="store_true",
                   help="print alerts instead of pushing, and don't save state.")
    p.add_argument("--seed", action="store_true",
                   help="record current stock without sending anything. Run "
                        "once on a fresh state file.")
    p.add_argument("--list", action="store_true",
                   help="list registered sites and exit.")
    args = p.parse_args()

    if args.list:
        for key, a in sorted(ADAPTERS.items()):
            print(f"{key:18} {a.label:20} sizes {'/'.join(a.target_sizes):8} {a.listing_url}")
        return 0

    return run(site_keys=args.sites, dry_run=args.dry_run, seed=args.seed)


if __name__ == "__main__":
    sys.exit(main())
