#!/usr/bin/env python3
"""
Daily generation script for The Watcher.
Fetches news, generates a reflection via Claude CLI, publishes to the site.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import frontmatter as fm_parser

# Add scripts dir to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from config import (
    ARTICLES_PER_CATEGORY,
    CONTENT_DIR,
    DATA_DIR,
    LOG_DIR,
    MEMORY_ENTRIES,
    MODEL,
    MODEL_DISPLAY,
    PROJECT_ROOT,
    TIMEZONE,
)
from fetch_news import Article
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
    """Call Claude via CLI to generate today's reflection."""
    today_str = datetime.now().strftime("%A, %B %-d, %Y")
    user_prompt = build_prompt(today_str, news_content, previous_entries)

    logger.info(f"Calling claude CLI ({MODEL}) with {len(user_prompt)} chars of context...")

    result = subprocess.run(
        [
            "claude", "-p",
            "--model", MODEL,
            "--output-format", "json",
            "--tools", "",
            "--system-prompt", SYSTEM_PROMPT,
        ],
        input=user_prompt,
        capture_output=True,
        text=True,
        timeout=120,
    )

    if result.returncode != 0:
        raise RuntimeError(f"claude CLI failed (exit {result.returncode}): {result.stderr}")

    response = json.loads(result.stdout)
    if response.get("is_error"):
        raise RuntimeError(f"claude CLI error: {response.get('result', 'unknown')}")

    response_text = response["result"]
    logger.info(f"Generated {len(response_text)} chars")
    return response_text


# Patterns that must never appear in an LLM-generated reflection. If any
# match the entire post is rejected — fail closed. Hugo's goldmark renderer
# with unsafe=false is the authoritative layer that keeps raw HTML from
# reaching the rendered page; this reject pass adds defense in depth and
# catches vectors (like javascript: URLs in markdown links) that goldmark
# does NOT filter.
_DANGEROUS_HTML_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("script tag", re.compile(r"<\s*/?\s*script\b", re.IGNORECASE)),
    ("iframe tag", re.compile(r"<\s*/?\s*iframe\b", re.IGNORECASE)),
    ("object tag", re.compile(r"<\s*/?\s*object\b", re.IGNORECASE)),
    ("embed tag", re.compile(r"<\s*/?\s*embed\b", re.IGNORECASE)),
    # Inline event handler attribute inside a tag: <a onclick="...">. HTML5
    # accepts `/` as an attribute-name separator in addition to whitespace,
    # so match either — `<svg/onload=alert(1)>` is a real XSS payload that a
    # `\s`-only regex would miss. DOTALL so an attacker can't split the tag
    # across a newline. `[^>]*` scopes the match to inside the tag so that
    # prose like "once = 5" doesn't false-positive.
    (
        "inline event handler",
        re.compile(r"<[^>]*[/\s]on\w+\s*=", re.IGNORECASE | re.DOTALL),
    ),
    # Dangerous URL schemes (javascript:, vbscript:, data:, file:) — rejected
    # only when they appear in contexts where a renderer would treat them as
    # live URLs. A bare regex like `javascript\s*:` was tried first and
    # rejected: it false-positives on entirely legitimate prose such as
    # "I read JavaScript: The Good Parts" or "the javascript: URI scheme
    # was deprecated", which would DoS the daily pipeline the first time
    # a news headline contains the word. These four patterns cover the
    # real attack vectors without touching prose:
    #
    # 1) Markdown inline link targets: [text](scheme:...) and the GFM
    #    angle-wrapped variant [text](<scheme:...>)
    (
        "dangerous-scheme markdown link",
        re.compile(
            r"]\s*\(\s*<?\s*(?:javascript|data|vbscript|file)\s*:",
            re.IGNORECASE,
        ),
    ),
    # 2) Markdown reference definitions: [ref]: scheme:...  at line start.
    #    MULTILINE so the ^ matches after any newline in the raw text.
    (
        "dangerous-scheme reference link",
        re.compile(
            r"^\s*\[[^\]]+\]:\s*<?\s*(?:javascript|data|vbscript|file)\s*:",
            re.IGNORECASE | re.MULTILINE,
        ),
    ),
    # 3) HTML attributes (href/src/action/formaction). Defence in depth
    #    behind the tag patterns and Hugo's unsafe=false escaping.
    (
        "dangerous-scheme HTML attribute",
        re.compile(
            r"""(?:href|src|action|formaction)\s*=\s*["']?\s*(?:javascript|data|vbscript|file)\s*:""",
            re.IGNORECASE,
        ),
    ),
    # 4) Markdown autolinks: <scheme:...>
    (
        "dangerous-scheme autolink",
        re.compile(
            r"<\s*(?:javascript|data|vbscript|file)\s*:",
            re.IGNORECASE,
        ),
    ),
)


def _reject_dangerous_html(text: str) -> None:
    """Raise ValueError if the text contains any disallowed pattern.

    Fails closed: any match aborts the pipeline rather than trying to
    sanitize-and-continue. The raw LLM response has already been written
    to data/YYYY-MM-DD-raw.md before this is called, so the rejected
    content remains available for inspection and replay.
    """
    for label, pattern in _DANGEROUS_HTML_PATTERNS:
        match = pattern.search(text)
        if match:
            raise ValueError(
                f"Reflection rejected: contains disallowed {label} "
                f"({match.group(0)!r})"
            )


def parse_reflection(raw: str) -> tuple[dict, str]:
    """Parse the frontmatter and body from Claude's response.

    The full raw text (frontmatter + body) is run through
    ``_reject_dangerous_html`` before any parsing so that injection
    attempts in title/mood_color/topics are caught alongside body-level
    attacks. Hugo's goldmark renderer (unsafe=false) then escapes any
    stray HTML at render time — we do not run a second Python-level
    sanitizer here because html5-based sanitizers mangle legitimate
    prose like "the <think> tag" or "use <Component>".
    """
    text = raw.strip()
    # Handle case where Claude wraps in ```markdown blocks
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:])
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

    # Screen the full text — frontmatter included — before splitting it up.
    _reject_dangerous_html(text)

    if not text.startswith("---"):
        today = datetime.now(ZoneInfo(TIMEZONE))
        return {
            "title": f"Entry for {today.strftime('%B %-d, %Y')}",
            "mood_score": _DEFAULT_MOOD_SCORE,
            "mood_color": _DEFAULT_MOOD_COLOR,
            "topics": list(_DEFAULT_TOPICS),
        }, text

    # Use python-frontmatter for robust parsing (handles --- in body text)
    post = fm_parser.loads(text)
    return dict(post.metadata), post.content


# Characters backslash-escaped in attacker-influenced source titles/names
# before embedding them in the sources list. This is the subset of
# CommonMark 6.1 (Backslash escapes) that can affect inline parsing:
# link/image syntax ([ ] ( ) !), emphasis runs (* _), code spans (`),
# HTML injection (< >), headings/ordered lists (# + - . at line start),
# tables (|), raw HTML entities ({ }), and the escape itself (\).
_SOURCE_MD_META_CHARS = r"\`*_{}[]()#+-.!|<>"
_SOURCE_MD_ESCAPE_RE = re.compile("([" + re.escape(_SOURCE_MD_META_CHARS) + "])")

# Sanity limit on URL length — any URL longer than this is almost certainly
# a pathological/adversarial payload, not a legitimate article link.
_MAX_SOURCE_URL_LEN = 2048

# mood_color is interpolated into inline `style="background-color: …"`
# attributes in three Hugo templates (archive, list, single). Without
# validation, a hostile value such as
#   red; } .entry-mood-strip::before { content: url(https://evil/beacon); } .x {
# would break out of the declaration and inject CSS — a fetch primitive
# under the current CSP (`style-src 'self' 'unsafe-inline'`). We validate
# *structurally* rather than by denylist: if the value is not a plain hex
# color literal it is dropped entirely.
_MOOD_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{3,8}$")
_DEFAULT_MOOD_COLOR = "#8B6914"

# mood_score is a numeric frontmatter field rendered into the mood chart
# data island as a JSON number. Range matches the persona prompt (1 heavy
# → 10 light). Out-of-range or wrong-type values are coerced to the
# midpoint default rather than raising.
_MOOD_SCORE_MIN = 1
_MOOD_SCORE_MAX = 10
_DEFAULT_MOOD_SCORE = 5

# topics is rendered as a list of badges on each post card and drives
# taxonomy. The allowed set is fixed by the persona prompt
# (scripts/persona.py:116). Unknown entries are dropped rather than
# substituted — if the whole list filters to empty, fall back to the
# canonical "wildcard" catch-all.
_ALLOWED_TOPICS = frozenset(("politics", "markets", "energy", "tech", "wildcard"))
_DEFAULT_TOPICS: tuple[str, ...] = ("wildcard",)


def _escape_md_inline(text: str) -> str:
    """Backslash-escape CommonMark inline metacharacters and collapse whitespace.

    Titles and source names come from attacker-influenced RSS feeds. Without
    escaping, a hostile title containing `](...)` could reshape the link
    target, `*` could break italic runs, and `<script>` would reach the
    renderer as raw HTML. Apostrophes, em-dashes, quotes, and ampersands
    are NOT in the metacharacter set — they pass through untouched.

    Also collapses any internal whitespace — a newline in a title would
    break the list-item boundary and leak following content into the body.
    """
    if not text:
        return ""
    escaped = _SOURCE_MD_ESCAPE_RE.sub(r"\\\1", text)
    return re.sub(r"\s+", " ", escaped).strip()


def _validate_mood_color(value: object) -> str:
    """Return value if it is a plain hex color literal, else the default.

    Accepts ``#`` followed by 3-8 hex digits (covers ``#RGB``, ``#RGBA``,
    ``#RRGGBB``, ``#RRGGBBAA`` plus the non-standard 5/7-digit lengths
    that browsers ignore). Uses ``fullmatch`` so a trailing newline or
    second color declaration cannot sneak past the anchors. Anything
    that is not a string, or contains any character outside the hex
    alphabet, is logged and replaced with ``_DEFAULT_MOOD_COLOR``.
    """
    if isinstance(value, str) and _MOOD_COLOR_RE.fullmatch(value):
        return value
    logger.warning(
        f"Invalid mood_color {value!r}, falling back to {_DEFAULT_MOOD_COLOR}"
    )
    return _DEFAULT_MOOD_COLOR


def _validate_mood_score(value: object) -> int:
    """Return value if it is an int in ``[_MOOD_SCORE_MIN, _MOOD_SCORE_MAX]``.

    Booleans are rejected even though ``isinstance(True, int)`` is True in
    Python — a ``mood_score: true`` would otherwise be coerced to 1.
    Floats, strings, and everything else fall back to the default with a
    warning.
    """
    if (
        isinstance(value, int)
        and not isinstance(value, bool)
        and _MOOD_SCORE_MIN <= value <= _MOOD_SCORE_MAX
    ):
        return value
    logger.warning(
        f"Invalid mood_score {value!r}, falling back to {_DEFAULT_MOOD_SCORE}"
    )
    return _DEFAULT_MOOD_SCORE


def _validate_topics(value: object) -> list[str]:
    """Filter ``value`` down to a list of allowed topic strings.

    Rules:
    - ``value`` must be a list. Anything else → default.
    - Each item must be a ``str`` in ``_ALLOWED_TOPICS``. Non-string items
      and out-of-set strings are dropped and logged individually.
    - Duplicates are de-duped in a single pass while preserving order so
      the badge list matches the author's original ranking.
    - If the filtered result is empty, fall back to ``_DEFAULT_TOPICS``.
    """
    if not isinstance(value, list):
        logger.warning(
            f"Invalid topics {value!r} (not a list), falling back to {list(_DEFAULT_TOPICS)}"
        )
        return list(_DEFAULT_TOPICS)

    seen: set[str] = set()
    filtered: list[str] = []
    for item in value:
        if not isinstance(item, str):
            logger.warning(f"Dropping non-string topic {item!r}")
            continue
        if item not in _ALLOWED_TOPICS:
            logger.warning(f"Dropping unknown topic {item!r}")
            continue
        if item in seen:
            continue
        seen.add(item)
        filtered.append(item)

    if not filtered:
        logger.warning(
            f"All topics dropped, falling back to {list(_DEFAULT_TOPICS)}"
        )
        return list(_DEFAULT_TOPICS)
    return filtered


def _safe_source_url(url: str) -> str | None:
    """Return the URL if it is safe to embed as a markdown link target.

    Requirements:
    - Length must be ≤ _MAX_SOURCE_URL_LEN (2048 bytes). Anything longer is
      almost certainly an adversarial payload.
    - Scheme must be http or https. Anything else (javascript:, data:,
      file:, gopher:, mailto:, etc.) is rejected — caller emits the title
      as plain text with no link.
    - Must have a hostname.
    - Must not contain `<`, `>`, or whitespace. We angle-wrap link targets
      (`[text](<url>)`) so that balanced parens in the URL do not confuse
      the parser; angle-wrap requires `<`/`>`/whitespace to be absent
      inside the target.

    Returns None on any rejection. Caller is responsible for logging.
    """
    if not url:
        return None
    if len(url) > _MAX_SOURCE_URL_LEN:
        return None
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https"):
        return None
    if not parsed.hostname:
        return None
    if any(c in url for c in "<> \t\r\n"):
        return None
    return url


def format_sources(news: dict[str, list[Article]]) -> str:
    """Format the articles read into a sources list for the post footer.

    Emits a Hugo shortcode call rather than raw HTML. This keeps the markdown
    source free of HTML so that Hugo's markup.goldmark.renderer.unsafe can
    stay disabled — the shortcode template renders the <details>/<summary>
    wrapper on the Hugo side where it is trusted.

    All attacker-controlled text (article titles, source names) is
    backslash-escaped before interpolation. URLs are scheme-validated;
    anything non-http(s) is rendered as plain text without a link.
    """
    lines = ["\n\n---\n", "{{< sources >}}"]
    for category, articles in news.items():
        for a in articles[:ARTICLES_PER_CATEGORY]:
            title = _escape_md_inline(a.title)
            source = _escape_md_inline(a.source)
            safe_url = _safe_source_url(a.url)
            if safe_url:
                lines.append(f"- [{title}](<{safe_url}>) — *{source}*")
            else:
                if a.url:
                    logger.warning(
                        f"Rejecting unsafe source URL, emitting title as plain text: {a.url!r}"
                    )
                lines.append(f"- {title} — *{source}*")
    lines.append("{{< /sources >}}")
    return "\n".join(lines)


def save_entry(frontmatter: dict, body: str, sources_md: str = "") -> Path:
    """Save the entry as a Hugo markdown file."""
    today = datetime.now(ZoneInfo(TIMEZONE))
    date_str = today.strftime("%Y-%m-%d")
    time_str = today.isoformat()

    # Build Hugo frontmatter. Every attacker-influenced field is run through
    # a structural validator at this boundary — the `.md` file on disk never
    # contains an un-validated value, so downstream template and JS contexts
    # are safe by construction.
    fm = {
        "title": frontmatter.get("title", f"Entry for {today.strftime('%B %-d, %Y')}"),
        "date": time_str,
        "model": MODEL_DISPLAY,
        "mood_color": _validate_mood_color(frontmatter.get("mood_color")),
        "mood_score": _validate_mood_score(frontmatter.get("mood_score")),
        "topics": _validate_topics(frontmatter.get("topics")),
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


GIT_PUSH_RETRIES = 2


def _run_git(*args, timeout: int = 30) -> subprocess.CompletedProcess:
    """Run a git command and return the result."""
    return subprocess.run(
        ["git", *args],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def git_commit_and_push(filepath: Path) -> bool:
    """Commit the new entry and push to GitHub.

    Pulls with rebase before pushing to handle remote changes.
    Retries push once if it fails due to remote updates during the operation.
    """
    today = datetime.now().strftime("%Y-%m-%d")

    try:
        _run_git("add", "content/posts/", "data/")
        # Check if there are staged changes before committing
        status = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            cwd=PROJECT_ROOT,
            capture_output=True,
        )
        if status.returncode != 0:
            _run_git("commit", "-m", f"entry: {today}")
        else:
            logger.info("Nothing to commit — commit already exists, proceeding to push")
    except subprocess.CalledProcessError as e:
        logger.error(f"Git commit failed: {e.stderr}")
        return False
    except Exception as e:
        logger.error(f"Git commit error: {e}")
        return False

    # Pull with rebase then push, with retry for race conditions
    for attempt in range(1, GIT_PUSH_RETRIES + 1):
        try:
            # Pull remote changes and rebase our commit on top
            try:
                result = _run_git("pull", "--rebase", timeout=30)
                if result.stdout.strip():
                    logger.info(f"Git pull: {result.stdout.strip()}")
            except subprocess.CalledProcessError as e:
                stderr = e.stderr or ""
                if "CONFLICT" in stderr or "could not apply" in stderr:
                    logger.error(
                        f"Git rebase conflict — aborting rebase and failing. "
                        f"Manual resolution needed: {stderr}"
                    )
                    # Abort the rebase to leave the repo in a clean state
                    try:
                        _run_git("rebase", "--abort")
                    except subprocess.CalledProcessError:
                        pass
                    return False
                # Other pull errors (e.g., no remote configured)
                logger.warning(f"Git pull failed (attempt {attempt}): {stderr}")
                if attempt >= GIT_PUSH_RETRIES:
                    return False
                continue

            _run_git("push", timeout=30)
            logger.info("Committed and pushed to GitHub")
            return True

        except subprocess.CalledProcessError as e:
            stderr = e.stderr or ""
            if "rejected" in stderr or "failed to push" in stderr.lower():
                logger.warning(
                    f"Push rejected (attempt {attempt}/{GIT_PUSH_RETRIES}), "
                    f"will retry with pull --rebase"
                )
                if attempt >= GIT_PUSH_RETRIES:
                    logger.error(f"Push failed after {GIT_PUSH_RETRIES} attempts: {stderr}")
                    return False
                continue
            logger.error(f"Git push failed: {stderr}")
            return False
        except Exception as e:
            logger.error(f"Git push error: {e}")
            return False

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
