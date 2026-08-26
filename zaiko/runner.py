"""The engine. Site-agnostic: everything brand-specific lives in an adapter.

For each site it asks the adapter what is in stock, and alerts on sizes that
*became* available — a new arrival, or a restock. A size that is simply still
in stock is not re-announced on every run.
"""

from __future__ import annotations

from datetime import datetime, timezone

from .http import fetch, make_session
from .notify import chunk_lines, notify
from .sites import resolve
from .sites.base import SiteLooksBroken, SiteUnavailable
from .state import load_state, save_state


def _sender(dry_run: bool = False, seed: bool = False):
    """Notification function for this run. Looks `notify` up at call time so
    tests can patch runner.notify."""
    if seed:
        def quiet(title, message, url="", url_title="", priority=0):
            print(f"[SEED - not sent] {title}")
        return quiet

    if dry_run:
        def printer(title, message, url="", url_title="", priority=0):
            print(f"\n[DRY RUN would push] {title}\n{message}\n")
        return printer

    return lambda *a, **kw: notify(*a, **kw)


def run_site(adapter, all_state: dict, dry_run: bool = False,
             seed: bool = False) -> int:
    """Check one site. Returns 0 if healthy, 1 if something looks wrong.

    Mutates all_state in place; the caller owns saving it.
    seed=True records what is in stock right now without pushing anything —
    use it once on a fresh state file so the first real run doesn't announce
    the entire catalogue.
    """
    send = _sender(dry_run, seed)
    label = adapter.label
    targets = adapter.normalized_targets
    target_label = "/".join(adapter.target_sizes)
    print(f"\n[INFO] {label}: checking for size {target_label}…")

    session = make_session(adapter)
    state = all_state.setdefault(adapter.key, {})
    alerts: list[dict] = []
    checked = failed = sizes_seen = 0

    try:
        for item in adapter.collect(session, fetch):
            if item.sizes is None:
                # Leave this product's saved state untouched so a temporary
                # failure can't look like a restock next time.
                failed += 1
                print(f"[SKIP] {item.name} — could not load, keeping previous state")
                continue

            checked += 1
            sizes = item.sizes
            sizes_seen += len(sizes)

            prev = state.get(item.url, {})
            prev_sizes = prev.get("sizes")               # None = never seen
            available = [s for s in targets if s in sizes]
            newly = [s for s in available if s not in (prev_sizes or [])]

            if newly:
                how = "new item" if prev_sizes is None else "back in stock"
                alerts.append({
                    "name": adapter.display_name(item.name),
                    "url": item.url,
                    "sizes": newly,
                })
                print(f"[ALERT] {item.name} — size {'/'.join(newly)} ({how})")
            elif available:
                print(f"[OK] {item.name} — size {'/'.join(available)} still in stock")
            else:
                print(f"[OK] {item.name} — no size {target_label}")

            # Only stamp a time when stock actually moved, so the state file
            # (and its git history) records real changes rather than heartbeats.
            changed_at = prev.get("changed_at")
            if prev_sizes != sizes:
                changed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            state[item.url] = {
                "name": item.name, "sizes": sizes, "changed_at": changed_at,
            }

    except SiteUnavailable as e:
        send(
            f"⚠️ Zaiko: {label} unreachable",
            f"{label} could not be reached ({e}). Check the GitHub Actions logs.",
        )
        return 1
    except SiteLooksBroken as e:
        send(
            f"⚠️ Zaiko: {label} monitor may be broken",
            f"{label} responded but not in the expected shape ({e}) — the site "
            "may have changed. Check the GitHub Actions logs.",
        )
        return 1

    # Structure sanity check, scoped to THIS run — if products read fine but no
    # sizes turned up anywhere, the markup or feed probably changed under us.
    if checked and sizes_seen == 0:
        send(
            f"⚠️ Zaiko: {label} monitor may be broken",
            f"Read {checked} {label} products but found no sizes on any of "
            "them — the site may have changed. Check the GitHub Actions logs.",
        )
        return 1

    if failed and checked == 0:
        send(
            f"⚠️ Zaiko: {label} unreadable",
            f"All {failed} {label} products failed to load. Check the logs.",
        )
        return 1

    if alerts:
        lines = [
            f"🔔 {a['name']} — size {'/'.join(a['sizes'])}\n{a['url']}"
            for a in alerts
        ]
        chunks = chunk_lines(lines)
        for i, body in enumerate(chunks, 1):
            suffix = f" ({i}/{len(chunks)})" if len(chunks) > 1 else ""
            send(
                f"{label}: {len(alerts)} item(s) in size {target_label}{suffix}",
                body,
                url=alerts[0]["url"],
                url_title=f"Open on {label}",
                priority=1,          # high priority: these sell out fast
            )
    else:
        print(f"[DONE] {label}: no new size-{target_label} alerts this run.")

    print(f"[DONE] {label}: {checked} read, {failed} failed, "
          f"{len(alerts)} alert(s).")
    return 0


def run(site_keys: list[str] | None = None, dry_run: bool = False,
        seed: bool = False) -> int:
    """Check every requested site. Returns 0 only if all of them were healthy."""
    adapters = resolve(site_keys)
    state = load_state()
    worst = 0

    if seed:
        print("[SEED] Recording current stock. No notifications will be sent.")

    try:
        for adapter in adapters:
            try:
                rc = run_site(adapter, state, dry_run=dry_run, seed=seed)
            except Exception as e:                    # one bad site must not
                rc = 1                                # take the others down
                print(f"[ERROR] {adapter.label} raised {type(e).__name__}: {e}")
                _sender(dry_run, seed)(
                    f"⚠️ Zaiko: {adapter.label} crashed",
                    f"{type(e).__name__}: {e}\nCheck the GitHub Actions logs.",
                )
            worst = max(worst, rc)
    finally:
        if dry_run:
            print("\n[DRY RUN] state not written.")
        else:
            save_state(state)

    return worst
