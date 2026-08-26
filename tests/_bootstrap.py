"""Make the repo root importable no matter where the tests are run from."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

failures: list[str] = []


def check(label, got, want):
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'}  {label}\n        got={got!r} want={want!r}")
    if not ok:
        failures.append(label)


def report(headline: str) -> None:
    print()
    if failures:
        print(f"{len(failures)} FAILING: {failures}")
        raise SystemExit(1)
    print(headline)
