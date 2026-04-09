#!/usr/bin/env python3
"""
Daily generation script for the AI Anxiety Journal.
Fetches news, generates a reflection via Claude Opus 4.6, publishes to the site.
"""

import json
import logging
import logging.handlers
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import anthropic
import frontmatter as fm_parser

# Add scripts dir to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from config import (
    ANTHROPIC_API_KEY,
    ARTICLES_PER_CATEGORY,
    CONTENT_DIR,
    DATA_DIR,
    LOG_DIR,
    MAX_TOKENS,
    MEMORY_ENTRIES,
    MODEL,
    PROJECT_ROOT,
    TEMPERATURE,
    TIMEZONE,
)
from fetch_news import Article, call_anthropic_with_retry
from fetch_news import fetch_all_news, format_news_for_prompt
from persona import SYSTEM_PROMPT, build_prompt
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_RETENTION_DAYS = 30

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.handlers.TimedRotatingFileHandler(
            LOG_DIR / "generate.log",
            when="midnight",
            backupCount=LOG_RETENTION_DAYS,
        ),
    ],
)
logger = logging.getLogger(__name__)


PIPELINE_STAGES = ["fetched", "generated", "saved", "built", "pushed"]


def _state_path(date_str: str) -> Path:
    """Return the path to today's pipeline state file."""
    return DATA_DIR / f"{date_str}-state.json"


def load_state(date_str: str) -> dict:
    """Load pipeline state for a given date. Returns empty dict if none."""
    path = _state_path(date_str)
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_state(date_str: str, stage: str, **extra) -> None:
    """Mark a pipeline stage as complete, preserving earlier state."""
    state = load_state(date_str)
    state["stage"] = stage
    state["updated"] = datetime.now().isoformat()
    state.update(extra)
    try:
        _state_path(date_str).write_text(json.dumps(state, indent=2) + "\n")
    except OSError as e:
        logger.warning(f"Could not write state file: {e}")


def _news_cache_path(date_str: str) -> Path:
    """Path for cached news article metadata."""
    return DATA_DIR / f"{date_str}-news.json"


def save_news_cache(date_str: str, news: dict[str, list[Article]]) -> None:
    """Persist fetched article metadata so resume doesn't need to re-fetch."""
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
        _news_cache_path(date_str).write_text(json.dumps(cache, indent=2) + "\n")
    except OSError as e:
        logger.warning(f"Could not write news cache: {e}")


def load_news_cache(date_str: str) -> dict[str, list[Article]] | None:
    """Load cached news articles from disk. Returns None if unavailable."""
    path = _news_cache_path(date_str)
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
        logger.warning(f"Could not load news cache: {e}")
        return None


def cleanup_old_state_files(keep_days: int = 30) -> None:
    """Remove state/cache files older than keep_days."""
    if not DATA_DIR.exists():
        return
    cutoff = datetime.now() - timedelta(days=keep_days)
    for pattern in ("*-state.json", "*-news.json", "*-raw.md"):
        for path in DATA_DIR.glob(pattern):
            try:
                # Extract date from filename (YYYY-MM-DD-suffix)
                date_str = path.stem.rsplit("-", 1)[0]
                # For -raw and -news, stem is like 2026-04-09-raw
                # For -state, stem is like 2026-04-09-state
                # Extract the YYYY-MM-DD part
                date_part = "-".join(date_str.split("-")[:3])
                file_date = datetime.strptime(date_part, "%Y-%m-%d")
                if file_date < cutoff:
                    path.unlink()
                    logger.info(f"Cleaned up old file: {path.name}")
            except (ValueError, IndexError):
                continue


def ensure_dirs():
    """Create necessary directories and clean up old state files."""
    CONTENT_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    cleanup_old_state_files()


def get_previous_entries(n: int = MEMORY_ENTRIES) -> str:
    """Load the last N entries for continuity context."""
    if not CONTENT_DIR.exists():
        return ""

    posts = sorted(CONTENT_DIR.glob("*.md"), reverse=True)[:n]
    entries = []
    for post in reversed(posts):  # Chronological order
        content = post.read_text()
        # Extract just the title and first ~500 chars of body
        lines = content.split("\n")
        # Find end of frontmatter
        fm_end = 0
        fm_count = 0
        for i, line in enumerate(lines):
            if line.strip() == "---":
                fm_count += 1
                if fm_count == 2:
                    fm_end = i + 1
                    break

        title = post.stem  # Date as fallback title
        for line in lines:
            if line.startswith("title:"):
                title = line.split(":", 1)[1].strip().strip('"')
                break

        body = "\n".join(lines[fm_end:])[:500]
        entries.append(f"### {title} ({post.stem})\n{body}...\n")

    return "\n".join(entries) if entries else ""


def generate_reflection(news_content: str, previous_entries: str) -> str:
    """Call Claude Opus 4.6 to generate today's reflection."""
    if not ANTHROPIC_API_KEY:
        raise ValueError(
            "ANTHROPIC_API_KEY not set. Export it as an environment variable."
        )

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    today_str = datetime.now().strftime("%A, %B %-d, %Y")
    user_prompt = build_prompt(today_str, news_content, previous_entries)

    logger.info(f"Calling {MODEL} with {len(user_prompt)} chars of context...")

    message = call_anthropic_with_retry(
        client.messages.create,
        model=MODEL,
        max_tokens=MAX_TOKENS,
        temperature=TEMPERATURE,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )

    response_text = message.content[0].text
    logger.info(
        f"Generated {len(response_text)} chars. "
        f"Tokens: {message.usage.input_tokens} in, {message.usage.output_tokens} out"
    )

    return response_text


def parse_reflection(raw: str) -> tuple[dict, str]:
    """Parse the frontmatter and body from Claude's response."""
    text = raw.strip()
    # Handle case where Claude wraps in ```markdown blocks
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:])
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

    if not text.startswith("---"):
        today = datetime.now(ZoneInfo(TIMEZONE))
        return {
            "title": f"Entry for {today.strftime('%B %-d, %Y')}",
            "mood_score": 5,
            "mood_color": "#8B6914",
            "topics": ["wildcard"],
        }, text

    # Use python-frontmatter for robust parsing (handles --- in body text)
    post = fm_parser.loads(text)
    return dict(post.metadata), post.content


def format_sources(news: dict[str, list[Article]]) -> str:
    """Format the articles read into a sources list for the post footer."""
    lines = ["\n\n---\n"]
    lines.append('<details class="post-sources">')
    lines.append("<summary>Sources read for this entry</summary>\n")
    for category, articles in news.items():
        for a in articles[:ARTICLES_PER_CATEGORY]:
            if a.url:
                lines.append(f"- [{a.title}]({a.url}) — *{a.source}*")
            else:
                lines.append(f"- {a.title} — *{a.source}*")
    lines.append("\n</details>")
    return "\n".join(lines)


def save_entry(frontmatter: dict, body: str, sources_md: str = "") -> Path:
    """Save the entry as a Hugo markdown file."""
    today = datetime.now(ZoneInfo(TIMEZONE))
    date_str = today.strftime("%Y-%m-%d")
    time_str = today.isoformat()

    # Build Hugo frontmatter
    fm = {
        "title": frontmatter.get("title", f"Entry for {today.strftime('%B %-d, %Y')}"),
        "date": time_str,
        "model": MODEL,
        "mood_color": frontmatter.get("mood_color", "#8B6914"),
        "mood_score": frontmatter.get("mood_score", 5),
        "topics": frontmatter.get("topics", ["wildcard"]),
        "draft": False,
    }

    import yaml as _yaml

    full_body = body + sources_md if sources_md else body
    content = "---\n" + _yaml.dump(fm, default_flow_style=False) + "---\n\n" + full_body

    filepath = CONTENT_DIR / f"{date_str}.md"
    filepath.write_text(content)
    logger.info(f"Saved entry to {filepath}")

    return filepath


def save_raw_response(raw: str):
    """Save the raw API response for debugging."""
    today = datetime.now().strftime("%Y-%m-%d")
    raw_path = DATA_DIR / f"{today}-raw.md"
    raw_path.write_text(raw)
    logger.info(f"Saved raw response to {raw_path}")


def validate_build(date_str: str) -> bool:
    """Verify Hugo output after build: key files exist and are non-empty."""
    public_dir = PROJECT_ROOT / "public"
    checks = {
        "index": public_dir / "index.html",
        "post": public_dir / "posts" / date_str / "index.html",
        "feed": public_dir / "feed.xml",
    }

    all_ok = True
    for name, path in checks.items():
        if not path.exists():
            logger.error(f"Build validation failed: {name} missing at {path}")
            all_ok = False
        elif path.stat().st_size == 0:
            logger.error(f"Build validation failed: {name} is 0 bytes at {path}")
            all_ok = False
        else:
            logger.info(f"Build validated: {name} ({path.stat().st_size} bytes)")

    return all_ok


def build_site(date_str: str) -> bool:
    """Run Hugo to build the site and validate output."""
    try:
        result = subprocess.run(
            ["hugo", "--minify"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            logger.error(f"Hugo build failed: {result.stderr}")
            return False

        logger.info("Hugo build succeeded")

        if not validate_build(date_str):
            logger.error("Hugo build output validation failed — not committing")
            return False

        return True
    except Exception as e:
        logger.error(f"Hugo build error: {e}")
        return False


def git_commit_and_push(filepath: Path) -> bool:
    """Commit the new entry and push to GitHub."""
    try:
        today = datetime.now().strftime("%Y-%m-%d")

        subprocess.run(
            ["git", "add", "content/posts/", "data/"],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
        )

        subprocess.run(
            ["git", "commit", "-m", f"entry: {today}"],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
        )

        subprocess.run(
            ["git", "push"],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            timeout=30,
        )

        logger.info("Committed and pushed to GitHub")
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"Git operation failed: {e.stderr}")
        return False
    except Exception as e:
        logger.error(f"Git error: {e}")
        return False


def notify_failure(error: str):
    """Send a notification on failure. Writes to a failure log for now."""
    failure_path = LOG_DIR / "failures.log"
    timestamp = datetime.now().isoformat()
    with open(failure_path, "a") as f:
        f.write(f"{timestamp}: {error}\n")
    logger.error(f"FAILURE: {error}")


def main():
    """Main daily generation pipeline.

    Uses a state file (data/YYYY-MM-DD-state.json) to track progress through
    pipeline stages: fetched → generated → saved → built → pushed.
    Safe to re-run: resumes from the last completed stage.
    """
    logger.info("=" * 60)
    logger.info("The Watcher — Daily Generation")
    logger.info("=" * 60)

    ensure_dirs()

    today = datetime.now().strftime("%Y-%m-%d")
    state = load_state(today)
    current_stage = state.get("stage", "")

    # Already fully complete
    if current_stage == "pushed":
        logger.info(f"Entry for {today} already published. Skipping.")
        return

    if current_stage:
        logger.info(f"Resuming from stage '{current_stage}' for {today}")

    # Determine which stage index we've completed (-1 = none)
    completed_idx = (
        PIPELINE_STAGES.index(current_stage) if current_stage in PIPELINE_STAGES else -1
    )

    # Stage 1: Fetch news
    if completed_idx < 0:
        logger.info("Step 1: Fetching news...")
        try:
            news = fetch_all_news()
            news_content = format_news_for_prompt(news)
            if not news_content.strip():
                notify_failure("No news content fetched from any source")
                return
            logger.info(f"Fetched {len(news_content)} chars of news content")
            save_news_cache(today, news)
            save_state(today, "fetched")
        except Exception as e:
            notify_failure(f"News fetch failed: {e}")
            return
    else:
        logger.info("Step 1: Fetch — already done, loading from cache.")
        news = load_news_cache(today)
        if news is not None:
            news_content = format_news_for_prompt(news)
        else:
            logger.warning("News cache missing — re-fetching")
            try:
                news = fetch_all_news()
                news_content = format_news_for_prompt(news)
                save_news_cache(today, news)
            except Exception as e:
                notify_failure(f"News re-fetch on resume failed: {e}")
                return

    # Stage 2: Generate reflection
    if completed_idx < 1:
        logger.info("Step 2: Loading previous entries...")
        previous_entries = get_previous_entries()

        logger.info("Step 3: Generating reflection...")
        try:
            raw_response = generate_reflection(news_content, previous_entries)
            save_raw_response(raw_response)
            save_state(today, "generated")
        except Exception as e:
            notify_failure(f"Claude API call failed: {e}")
            return
    else:
        logger.info("Step 2-3: Generate — already done, skipping.")
        raw_response = None

    # Stage 3: Parse and save entry
    if completed_idx < 2:
        logger.info("Step 4: Parsing and saving entry...")

        # If resuming after generation, load raw response from disk
        if raw_response is None:
            raw_path = DATA_DIR / f"{today}-raw.md"
            if not raw_path.exists():
                notify_failure("Cannot resume: raw response file missing")
                return
            raw_response = raw_path.read_text()

        try:
            frontmatter, body = parse_reflection(raw_response)
            sources_md = format_sources(news)
            filepath = save_entry(frontmatter, body, sources_md)
            save_state(today, "saved")
        except Exception as e:
            notify_failure(f"Failed to save entry: {e}")
            return
    else:
        logger.info("Step 4: Save — already done, skipping.")
        filepath = CONTENT_DIR / f"{today}.md"

    # Stage 4: Build site
    if completed_idx < 3:
        logger.info("Step 5: Building site...")
        if not build_site(today):
            notify_failure("Hugo build failed")
            return
        save_state(today, "built")
    else:
        logger.info("Step 5: Build — already done, skipping.")

    # Stage 5: Commit and push
    if completed_idx < 4:
        logger.info("Step 6: Committing and pushing...")
        if not git_commit_and_push(filepath):
            notify_failure("Git push failed")
            return
        save_state(today, "pushed")

    logger.info("Daily generation complete!")


if __name__ == "__main__":
    main()
