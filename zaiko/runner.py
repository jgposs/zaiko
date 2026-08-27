"""The engine. Site-agnostic: everything brand-specific lives in an adapter.

For each site it asks the adapter what is in stock, and alerts on sizes that
*became* available — a new arrival, or a restock. A size that is simply still
in stock is not re-announced on every run.

The rule the whole design turns on: a run only gets to update its memory if it
went well. An unhealthy run keeps yesterday's state, so the alerts it couldn't
trust are re-derived tomorrow rather than silently recorded as already seen.
"""

from __future__ import annotations

import copy
import time
from datetime import datetime, timezone

from .config import MAX_FAILURE_RATIO, MAX_PUSHES_PER_SITE
from .http import fetch, make_session
from .notify import chunk_alerts, have_credentials, notify
from .sites import resolve
from .sites.base import SiteLooksBroken, SiteUnavailable, normalize_size
from .state import (StateUnreadable, load_state, prune,
                    refresh_last_seen, save_state)


def _sender(dry_run: bool = False, seed: bool = False):
    """Notification function for this run, returning True when delivered.

    Looks `notify` up at call time so tests can patch runner.notify.
    """
    if seed:
        def quiet(title, message, url="", url_title="", priority=0):
            print(f"[SEED - not sent] {title}")
            return True
        return quiet

    if dry_run:
        def printer(title, message, url="", url_title="", priority=0):
            print(f"\n[DRY RUN would push] {title}\n{message}\n")
            return True
        return printer

    def real(title, message, url="", url_title="", priority=0):
        return notify(title, message, url=url, url_title=url_title,
                      priority=priority)
    return real


def _deliver(send, adapter, alerts, target_label) -> bool:
    """Push this run's alerts. False if any of them failed to reach Pushover."""
    label = adapter.label
    if not alerts:
        print(f"[DONE] {label}: no new size-{target_label} alerts this run.")
        return True

    chunks = chunk_alerts(alerts)
    capped = chunks[:MAX_PUSHES_PER_SITE]
    delivered = True

    for i, (body, url) in enumerate(capped, 1):
        suffix = f" ({i}/{len(chunks)})" if len(chunks) > 1 else ""
        sent = send(
            f"{label}: {len(alerts)} item(s) in size {target_label}{suffix}",
            body,
            url=url,
            url_title=f"Open on {label}",
            priority=1,          # high priority: these sell out fast
        )
        delivered = sent and delivered

    if len(chunks) > len(capped):
        # A flood usually means something went wrong upstream, not that 300
        # coats appeared overnight. Say so once instead of pushing 40 times.
        held = len(chunks) - len(capped)
        sent = send(
            f"{label}: {held} more message(s) held back",
            f"{len(alerts)} items matched in a single run — only the first "
            f"{len(capped)} messages were sent. The full list is in the "
            "GitHub Actions log.",
        )
        delivered = sent and delivered

    return delivered


def run_site(adapter, all_state: dict, dry_run: bool = False,
             seed: bool = False) -> int:
    """Check one site. Returns 0 if healthy, 1 if something looks wrong.

    Mutates all_state in place; the caller owns saving it. On an unhealthy
    outcome this site's slice is restored to how it started, so nothing that
    went unreported gets recorded as seen.

    seed=True records what is in stock right now without pushing anything —
    use it once on a fresh state file so the first real run doesn't announce
    the entire catalogue.
    """
    send = _sender(dry_run, seed)
    label = adapter.label
    targets = adapter.normalized_targets
    target_label = "/".join(adapter.target_sizes)
    print(f"\n[INFO] {label}: checking for size {target_label}…")

    state = all_state.setdefault(adapter.key, {})
    snapshot = copy.deepcopy(state)

    def alarm(headline: str, detail: str) -> int:
        """Report a problem and abandon everything this run learned."""
        all_state[adapter.key] = snapshot
        send(f"⚠️ Zaiko: {label} {headline}",
             f"{detail} Check the GitHub Actions logs.")
        return 1

    session = make_session(adapter)
    alerts: list[tuple[str, str]] = []
    seen: set = set()
    checked = failed = 0
    now = datetime.now(timezone.utc)
    stamp = now.isoformat(timespec="seconds")
    deadline = time.monotonic() + adapter.time_budget
    out_of_time = False

    try:
        for item in adapter.collect(session, fetch):
            if time.monotonic() > deadline:
                out_of_time = True
                break

            seen.add(item.url)

            if item.sizes is None:
                # Leave this product's saved state untouched so a temporary
                # failure can't look like a restock next time.
                failed += 1
                print(f"[SKIP] {item.name} — could not read, keeping previous state")
                continue

            checked += 1
            # Adapters yield sizes as the site spells them; folding them into
            # one form is the engine's job, so a new adapter can't silently
            # miss every target by using a different case or separator.
            sizes = list(dict.fromkeys(normalize_size(s) for s in item.sizes))

            prev = state.get(item.url, {})
            prev_sizes = prev.get("sizes")               # None = never seen
            available = [s for s in targets if s in sizes]
            newly = [s for s in available if s not in (prev_sizes or [])]

            if newly:
                how = "new item" if prev_sizes is None else "back in stock"
                alerts.append((
                    f"🔔 {adapter.display_name(item.name)} — "
                    f"size {'/'.join(newly)}\n{item.url}",
                    item.url,
                ))
                print(f"[ALERT] {item.name} — size {'/'.join(newly)} ({how})")
            elif available:
                print(f"[OK] {item.name} — size {'/'.join(available)} still in stock")
            else:
                print(f"[OK] {item.name} — no size {target_label}")

            # Only stamp changed_at when stock actually moved, so the state
            # file's git history records real changes rather than heartbeats.
            changed_at = prev.get("changed_at")
            if prev_sizes != sizes:
                changed_at = stamp
            state[item.url] = {
                "name": item.name,
                "sizes": sizes,
                "changed_at": changed_at,
                "last_seen": refresh_last_seen(prev.get("last_seen"), now),
            }

    except SiteUnavailable as e:
        return alarm("unreachable", f"{label} could not be reached ({e}).")
    except SiteLooksBroken as e:
        return alarm("monitor may be broken",
                     f"{label} responded but not in the expected shape ({e}) — "
                     "the site may have changed.")

    attempted = checked + failed
    if attempted == 0:
        return alarm("returned nothing",
                     f"The {label} adapter produced no products at all.")

    # Partial degradation is the failure mode that used to pass as healthy:
    # rate limiting or a redesign that breaks most pages but not all of them.
    if failed / attempted > MAX_FAILURE_RATIO:
        return alarm("mostly unreadable",
                     f"{failed} of {attempted} {label} products could not be "
                     "read this run.")

    if not _deliver(send, adapter, alerts, target_label):
        # Alerts that didn't arrive must not be recorded as seen, or they are
        # lost for good. Keep yesterday's state and re-derive them tomorrow.
        all_state[adapter.key] = snapshot
        print(f"[ERROR] {label}: delivery failed; state left unchanged so "
              "these alerts are re-sent next run.")
        return 1

    rc = 0
    if out_of_time:
        # Alerts already found are real and were delivered, so keep them —
        # but the catalogue was only partly walked, so don't prune, and don't
        # call the run healthy.
        send(f"⚠️ Zaiko: {label} ran out of time",
             f"Stopped after {attempted} products ({adapter.time_budget:.0f}s "
             "budget). The rest keep their previous state. Check the GitHub "
             "Actions logs.")
        rc = 1
    else:
        dropped = prune(state, seen, now)
        if dropped:
            print(f"[INFO] {label}: forgot {dropped} product(s) not seen in "
                  "a long time.")

    print(f"[DONE] {label}: {checked} read, {failed} failed, "
          f"{len(alerts)} alert(s).")
    return rc


def run(site_keys: list[str] | None = None, dry_run: bool = False,
        seed: bool = False) -> int:
    """Check every requested site. Returns 0 only if all of them were healthy."""
    adapters = resolve(site_keys)
    send = _sender(dry_run, seed)

    if not (dry_run or seed) and not have_credentials():
        # Otherwise a deleted or renamed secret produces a permanently green
        # job that quietly delivers nothing.
        print("[ERROR] PUSHOVER_USER_KEY / PUSHOVER_API_TOKEN are not set — "
              "nothing could be delivered. Refusing to advance state.")
        return 1

    try:
        state = load_state()
    except StateUnreadable as e:
        # Starting fresh here would announce the whole catalogue and commit
        # that over the only copy of the real memory.
        print(f"[ERROR] state file is unreadable ({e}); refusing to overwrite it.")
        send("⚠️ Zaiko: state file unreadable",
             f"{e}\nThe run was abandoned so the file isn't overwritten. "
             "Restore it from git history, or delete it and re-seed.")
        return 1

    if seed:
        print("[SEED] Recording current stock. No notifications will be sent.")

    worst = 0
    try:
        for adapter in adapters:
            before = copy.deepcopy(state.get(adapter.key, {}))
            try:
                rc = run_site(adapter, state, dry_run=dry_run, seed=seed)
            except Exception as e:                    # one bad site must not
                rc = 1                                # take the others down
                state[adapter.key] = before
                print(f"[ERROR] {adapter.label} raised {type(e).__name__}: {e}")
                send(f"⚠️ Zaiko: {adapter.label} crashed",
                     f"{type(e).__name__}: {e}\nCheck the GitHub Actions logs.")
            worst = max(worst, rc)
    finally:
        if dry_run:
            print("\n[DRY RUN] state not written.")
        else:
            save_state(state)

    return worst
