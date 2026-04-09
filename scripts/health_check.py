#!/usr/bin/env python3
"""Health check script for the AI Anxiety Journal.

Reports:
- When the last successful post was published
- Any failures in the last 7 days
- RSS feed health summary (from feed-health.json)
"""

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from config import CONTENT_DIR, FEED_HEALTH_PATH, LOG_DIR

FAILURES_PATH = LOG_DIR / "failures.log"


def check_last_post() -> tuple[str | None, int]:
    """Find the most recent post and how many days ago it was.

    Returns (date_string, days_ago). Returns (None, -1) if no posts found.
    """
    if not CONTENT_DIR.exists():
        return None, -1

    posts = sorted(CONTENT_DIR.glob("*.md"), reverse=True)
    if not posts:
        return None, -1

    date_str = posts[0].stem  # filename is YYYY-MM-DD.md
    try:
        post_date = datetime.strptime(date_str, "%Y-%m-%d")
        days_ago = (datetime.now() - post_date).days
        return date_str, days_ago
    except ValueError:
        return date_str, -1


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


def main():
    """Run all health checks and print a report."""
    print("=" * 50)
    print("AI Anxiety Journal — Health Check")
    print("=" * 50)
    print()

    all_ok = True

    # 1. Last post
    last_date, days_ago = check_last_post()
    if last_date is None:
        print("[WARN] No posts found")
        all_ok = False
    elif days_ago == 0:
        print(f"[OK]   Last post: {last_date} (today)")
    elif days_ago == 1:
        print(f"[OK]   Last post: {last_date} (yesterday)")
    elif days_ago <= 2:
        print(f"[WARN] Last post: {last_date} ({days_ago} days ago)")
        all_ok = False
    else:
        print(f"[FAIL] Last post: {last_date} ({days_ago} days ago)")
        all_ok = False

    # 2. Recent failures
    failures = check_recent_failures(days=7)
    if failures:
        print(f"[WARN] {len(failures)} failure(s) in last 7 days:")
        for f in failures[-5:]:  # Show last 5
            print(f"       {f}")
        if len(failures) > 5:
            print(f"       ... and {len(failures) - 5} more")
        all_ok = False
    else:
        print("[OK]   No failures in last 7 days")

    # 3. Feed health
    health = check_feed_health()
    if health is None:
        print("[INFO] No feed health data available (run generate first)")
    else:
        total = len(health)
        recent_failures = []
        for url, record in health.items():
            last_failure = record.get("last_failure")
            last_success = record.get("last_success")
            # Flag feeds whose last check was a failure
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
            print(f"[OK]   All {total} tracked feeds healthy")

    print()
    if all_ok:
        print("Status: ALL OK")
    else:
        print("Status: ISSUES DETECTED — review above")

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
