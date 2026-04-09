#!/usr/bin/env python3
"""
Daily generation script for the AI Anxiety Journal.
Fetches news, generates a reflection via Claude Opus 4.6, publishes to the site.
"""

import json
import logging
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import anthropic
import frontmatter as fm_parser
from tenacity import retry, stop_after_attempt, wait_exponential

# Add scripts dir to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from config import (
    ANTHROPIC_API_KEY,
    CONTENT_DIR,
    DATA_DIR,
    MAX_TOKENS,
    MEMORY_ENTRIES,
    MODEL,
    PROJECT_ROOT,
    TEMPERATURE,
)
from fetch_news import fetch_all_news, format_news_for_prompt
from persona import SYSTEM_PROMPT, build_prompt

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(PROJECT_ROOT / "logs" / "generate.log"),
    ],
)
logger = logging.getLogger(__name__)


def ensure_dirs():
    """Create necessary directories."""
    CONTENT_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (PROJECT_ROOT / "logs").mkdir(parents=True, exist_ok=True)


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


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=4, max=60))
def generate_reflection(news_content: str, previous_entries: str) -> str:
    """Call Claude Opus 4.6 to generate today's reflection."""
    if not ANTHROPIC_API_KEY:
        raise ValueError(
            "ANTHROPIC_API_KEY not set. Export it as an environment variable."
        )

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    today = datetime.now().strftime("%A, %B %-d, %Y")
    user_prompt = build_prompt(today, news_content, previous_entries)

    logger.info(f"Calling {MODEL} with {len(user_prompt)} chars of context...")

    message = client.messages.create(
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
        today = datetime.now(ZoneInfo("America/Los_Angeles"))
        return {
            "title": f"Entry for {today.strftime('%B %-d, %Y')}",
            "mood_score": 5,
            "mood_color": "#8B6914",
            "topics": ["wildcard"],
        }, text

    # Use python-frontmatter for robust parsing (handles --- in body text)
    post = fm_parser.loads(text)
    return dict(post.metadata), post.content


def save_entry(frontmatter: dict, body: str) -> Path:
    """Save the entry as a Hugo markdown file."""
    today = datetime.now(ZoneInfo("America/Los_Angeles"))
    date_str = today.strftime("%Y-%m-%d")
    time_str = today.isoformat()

    # Build Hugo frontmatter
    fm = {
        "title": frontmatter.get("title", f"Entry for {today.strftime('%B %-d, %Y')}"),
        "date": time_str,
        "mood_color": frontmatter.get("mood_color", "#8B6914"),
        "mood_score": frontmatter.get("mood_score", 5),
        "topics": frontmatter.get("topics", ["wildcard"]),
        "draft": False,
    }

    import yaml as _yaml

    content = "---\n" + _yaml.dump(fm, default_flow_style=False) + "---\n\n" + body

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


def build_site() -> bool:
    """Run Hugo to build the site."""
    try:
        result = subprocess.run(
            ["hugo", "--minify"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode == 0:
            logger.info("Hugo build succeeded")
            return True
        else:
            logger.error(f"Hugo build failed: {result.stderr}")
            return False
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
    failure_path = PROJECT_ROOT / "logs" / "failures.log"
    timestamp = datetime.now().isoformat()
    with open(failure_path, "a") as f:
        f.write(f"{timestamp}: {error}\n")
    logger.error(f"FAILURE: {error}")


def main():
    """Main daily generation pipeline."""
    logger.info("=" * 60)
    logger.info("AI Anxiety Journal — Daily Generation")
    logger.info("=" * 60)

    ensure_dirs()

    # Check if today's entry already exists
    today = datetime.now().strftime("%Y-%m-%d")
    if (CONTENT_DIR / f"{today}.md").exists():
        logger.info(f"Entry for {today} already exists. Skipping.")
        return

    # Step 1: Fetch news
    logger.info("Step 1: Fetching news...")
    try:
        news = fetch_all_news()
        news_content = format_news_for_prompt(news)
        if not news_content.strip():
            notify_failure("No news content fetched from any source")
            return
        logger.info(f"Fetched {len(news_content)} chars of news content")
    except Exception as e:
        notify_failure(f"News fetch failed: {e}")
        return

    # Step 2: Load previous entries for continuity
    logger.info("Step 2: Loading previous entries...")
    previous_entries = get_previous_entries()

    # Step 3: Generate reflection
    logger.info("Step 3: Generating reflection...")
    try:
        raw_response = generate_reflection(news_content, previous_entries)
        save_raw_response(raw_response)
    except Exception as e:
        notify_failure(f"Claude API call failed: {e}")
        return

    # Step 4: Parse and save
    logger.info("Step 4: Parsing and saving entry...")
    try:
        frontmatter, body = parse_reflection(raw_response)
        filepath = save_entry(frontmatter, body)
    except Exception as e:
        notify_failure(f"Failed to save entry: {e}")
        return

    # Step 5: Build site
    logger.info("Step 5: Building site...")
    if not build_site():
        notify_failure("Hugo build failed")
        return

    # Step 6: Commit and push
    logger.info("Step 6: Committing and pushing...")
    if not git_commit_and_push(filepath):
        notify_failure("Git push failed")
        return

    logger.info("Daily generation complete!")


if __name__ == "__main__":
    main()
