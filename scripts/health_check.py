#!/usr/bin/env python3
"""Health check for Claude's Daily Digest.

Reports, in one pass:
- Freshest entry across every reader profile.
- Per-profile staleness so a single dead desk does not hide behind
  another profile shipping every morning.
- Failures in the last 7 days from `logs/failures.log`.
- RSS feed health summary from `data/feed-health.json`.
"""

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from config import CONTENT_DIR, FEED_HEALTH_PATH, LOG_DIR
from profiles import READER_PROFILES

FAILURES_PATH = LOG_DIR / "failures.log"

# A profile with no new entry in this many days trips a WARN; more than
# TWO days trips a FAIL. Matches the site-wide freshness thresholds.
STALE_WARN_DAYS = 1
STALE_FAIL_DAYS = 2


def _newest_entry_in(section: Path) -> tuple[str | None, int]:
    """Return (date_str, days_ago) for the newest .md in `section`.

    Returns (None, -1) if the section has no valid entries.
    """
    if not section.exists():
        return None, -1
    posts = sorted(section.glob("*.md"), reverse=True)
    if not posts:
        return None, -1
    date_str = posts[0].stem  # filename is YYYY-MM-DD.md
    try:
        post_date = datetime.strptime(date_str, "%Y-%m-%d")
        return date_str, (datetime.now() - post_date).days
    except ValueError:
        return date_str, -1


def check_last_post_by_profile() -> dict[str, tuple[str | None, int]]:
    """Freshest entry per profile.

    Returns {slug: (date_str, days_ago)}. days_ago is -1 when the section
    is missing or its most recent filename is not a parseable date.
    """
    return {
        slug: _newest_entry_in(CONTENT_DIR / slug)
        for slug in READER_PROFILES.keys()
    }


def check_last_post_overall() -> tuple[str | None, int]:
    """Freshest entry across every profile section.

    The site is "live" as long as at least one desk ships each morning,
    so the overall freshness signal is the min of per-profile freshness.
    """
    best_date: str | None = None
    best_days = -1
    for _, (date_str, days_ago) in check_last_post_by_profile().items():
        if date_str is None:
            continue
        if best_date is None or days_ago < best_days:
            best_date = date_str
            best_days = days_ago
    return best_date, best_days


def check_recent_failures(days: int = 7) -> list[str]:
    """Return failure log lines from the last N days."""
    if not FAILURES_PATH.exists():
        return []
    cutoff = datetime.now() - timedelta(days=days)
    recent = []
    for line in FAILURES_PATH.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        # Lines are formatted: 2026-04-09T12:00:00.000000: error message
        try:
            parts = line.split(": ", 1)
            if len(parts) < 2:
                continue
            timestamp_str = parts[0]
            if "T" not in timestamp_str:
                continue
            timestamp = datetime.fromisoformat(timestamp_str)
            if timestamp >= cutoff:
                recent.append(line)
        except (ValueError, IndexError):
            continue
    return recent


def check_feed_health() -> dict | None:
    """Load feed health data if available."""
    if not FEED_HEALTH_PATH.exists():
        return None
    try:
        return json.loads(FEED_HEALTH_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _describe_freshness(date_str: str | None, days_ago: int) -> tuple[str, bool]:
    """Return (message, ok_flag) describing a freshness sample."""
    if date_str is None:
        return ("no posts found", False)
    if days_ago < 0:
        return (f"last post: {date_str} (date unparseable)", False)
    if days_ago == 0:
        return (f"last post: {date_str} (today)", True)
    if days_ago == 1:
        return (f"last post: {date_str} (yesterday)", True)
    if days_ago <= STALE_FAIL_DAYS:
        return (f"last post: {date_str} ({days_ago} days ago)", False)
    return (f"last post: {date_str} ({days_ago} days ago)", False)


def main():
    """Run all health checks and print a report."""
    print("=" * 50)
    print("Claude's Daily Digest — Health Check")
    print("=" * 50)
    print()

    all_ok = True

    # 1. Overall freshness (min days_ago across all profiles).
    overall_date, overall_days = check_last_post_overall()
    msg, ok = _describe_freshness(overall_date, overall_days)
    status = "OK" if ok else ("WARN" if overall_days == STALE_WARN_DAYS + 1 else "FAIL")
    print(f"[{status:4s}] site: {msg}")
    if not ok:
        all_ok = False

    # 2. Per-profile freshness.
    per_profile = check_last_post_by_profile()
    for slug in READER_PROFILES.keys():
        date_str, days_ago = per_profile[slug]
        msg, ok = _describe_freshness(date_str, days_ago)
        if ok:
            print(f"[OK  ] {slug}: {msg}")
        else:
            status = "WARN" if 0 <= days_ago <= STALE_WARN_DAYS + 1 else "FAIL"
            print(f"[{status:4s}] {slug}: {msg}")
            all_ok = False

    # 3. Recent failures
    failures = check_recent_failures(days=7)
    if failures:
        print(f"[WARN] {len(failures)} failure(s) in last 7 days:")
        for f in failures[-5:]:
            print(f"       {f}")
        if len(failures) > 5:
            print(f"       ... and {len(failures) - 5} more")
        all_ok = False
    else:
        print("[OK  ] no failures in last 7 days")

    # 4. Feed health
    health = check_feed_health()
    if health is None:
        print("[INFO] no feed health data available (run generate first)")
    else:
        total = len(health)
        recent_failures = []
        for url, record in health.items():
            last_failure = record.get("last_failure")
            last_success = record.get("last_success")
            if last_failure and (not last_success or last_failure > last_success):
                recent_failures.append((url, record.get("last_error", "unknown")))

        if recent_failures:
            print(f"[WARN] {len(recent_failures)}/{total} feeds currently failing:")
            for url, error in recent_failures[:5]:
                print(f"       {error}: {url}")
            if len(recent_failures) > 5:
                print(f"       ... and {len(recent_failures) - 5} more")
            all_ok = False
        else:
            print(f"[OK  ] all {total} tracked feeds healthy")

    print()
    if all_ok:
        print("Status: ALL OK")
    else:
        print("Status: ISSUES DETECTED — review above")

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
