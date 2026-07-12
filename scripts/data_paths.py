"""Paths, cache I/O, and cleanup for per-profile data files.

Filename convention (double underscore separates semantic segments):

    data/YYYY-MM-DD__<slug>-news.json     cached article metadata
    data/YYYY-MM-DD__<slug>-raw.md        raw claude CLI response
    data/YYYY-MM-DD-state.json            per-day pipeline state (shared)

Legacy (pre-refactor) filenames used single underscores throughout
(`YYYY-MM-DD-news.json`, `YYYY-MM-DD-raw.md`). `cleanup_old_state_files`
recognizes both schemas so the 30-day cleanup window keeps working while
old files age out.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta
from pathlib import Path

from config import DATA_DIR
from fetch_news import Article

logger = logging.getLogger(__name__)


def news_cache_path(date_str: str, slug: str) -> Path:
    """Cached article metadata for one profile on one day."""
    return DATA_DIR / f"{date_str}__{slug}-news.json"


def raw_response_path(date_str: str, slug: str) -> Path:
    """Raw claude CLI response text for one profile on one day."""
    return DATA_DIR / f"{date_str}__{slug}-raw.md"


def save_news_cache(
    date_str: str, slug: str, news: dict[str, list[Article]]
) -> None:
    """Persist fetched article metadata so a resume does not re-fetch."""
    cache = {}
    for category, articles in news.items():
        cache[category] = [
            {
                "title": a.title,
                "summary": a.summary,
                "url": a.url,
                "source": a.source,
                "category": a.category,
                "full_text": a.full_text,
            }
            for a in articles
        ]
    try:
        news_cache_path(date_str, slug).write_text(
            json.dumps(cache, indent=2) + "\n"
        )
    except OSError as e:
        logger.warning(f"[{slug}] Could not write news cache: {e}")


def load_news_cache(
    date_str: str, slug: str
) -> dict[str, list[Article]] | None:
    """Load cached news articles from disk. Returns None if unavailable."""
    path = news_cache_path(date_str, slug)
    if not path.exists():
        return None
    try:
        cache = json.loads(path.read_text())
        news: dict[str, list[Article]] = {}
        for category, articles in cache.items():
            news[category] = [
                Article(
                    title=a["title"],
                    summary=a["summary"],
                    url=a["url"],
                    source=a["source"],
                    category=a["category"],
                    full_text=a.get("full_text", ""),
                )
                for a in articles
            ]
        return news
    except (json.JSONDecodeError, KeyError, OSError) as e:
        logger.warning(f"[{slug}] Could not load news cache: {e}")
        return None


# Two-schema recognizer for the daily-data filename family, used by the
# cleanup pass. Anchored on ISO date + a known suffix; the slug (if any)
# lives between the double-underscore delimiter and the suffix.
#
# Matches:
#   2026-07-12-news.json            (legacy)
#   2026-07-12-raw.md               (legacy)
#   2026-07-12-state.json           (state file, unchanged shape)
#   2026-07-12__tech-news.json      (new per-profile)
#   2026-07-12__wildcard-raw.md     (new per-profile)
_DATA_FILE_PATTERN = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})"
    r"(?:__[a-z0-9_-]+)?"
    r"-(?:state\.json|news\.json|raw\.md)$"
)


def cleanup_old_state_files(keep_days: int = 30) -> None:
    """Remove state/cache/raw files older than `keep_days`.

    Understands both the legacy `YYYY-MM-DD-<suffix>` names and the new
    `YYYY-MM-DD__<slug>-<suffix>` names. A file whose name does not match
    either schema is left alone.
    """
    if not DATA_DIR.exists():
        return
    cutoff = datetime.now() - timedelta(days=keep_days)
    for path in DATA_DIR.iterdir():
        if not path.is_file():
            continue
        match = _DATA_FILE_PATTERN.match(path.name)
        if not match:
            continue
        try:
            file_date = datetime.strptime(match.group("date"), "%Y-%m-%d")
        except ValueError:
            continue
        if file_date < cutoff:
            try:
                path.unlink()
                logger.info(f"Cleaned up old file: {path.name}")
            except OSError as e:
                logger.warning(f"Could not delete {path.name}: {e}")
