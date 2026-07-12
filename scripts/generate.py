#!/usr/bin/env python3
"""Daily generation pipeline for Claude's Daily Digest.

Runs once per day under launchd. For each reader profile, in the
canonical tab order, fetches its feeds, generates an entry with its own
persona and continuity memory, and saves it to `content/<slug>/`. Then
runs Hugo once and commits every profile that made it that day.

State is tracked per profile in `data/YYYY-MM-DD-state.json`:

    {"profiles": {"tech": "saved", ...}, "built": false, "pushed": false, ...}

Re-running resumes any profile from its last completed stage. A profile
that fails at any stage is skipped and the run keeps going; the day
ships with whoever succeeded. The run exits non-zero only if zero
profiles reached `saved`.

CLI:

    --profiles slug1,slug2   restrict to a subset of profiles (default: all)
    --until STAGE            stop after STAGE
                             (fetched | generated | saved | built | pushed)
"""

from __future__ import annotations

import argparse
import json
import logging
import logging.handlers
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import frontmatter as fm_parser

# Add scripts dir to path so flat imports work whether we run this as
# `python3 scripts/generate.py` or via run.sh.
sys.path.insert(0, str(Path(__file__).parent))

from config import (
    ARTICLES_PER_CATEGORY,
    CONTENT_DIR,
    DATA_DIR,
    LOG_DIR,
    MEMORY_ENTRIES,
    MODEL,
    NEWS_MEMORY_DAYS,
    MODEL_DISPLAY,
    PROJECT_ROOT,
    TIMEZONE,
)
from data_paths import (
    cleanup_old_state_files,
    load_news_cache,
    news_cache_path,
    raw_response_path,
    save_news_cache,
)
from fetch_news import (
    AllFeedsFailedError,
    Article,
    fetch_all_news,
    format_news_for_prompt,
)
from persona import build_prompt, load_system_prompt
from pipeline_state import (
    LEGACY_COMPLETE,
    PROFILE_STAGES,
    SITE_STAGES,
    is_legacy_complete,
    load_state,
    mark_profile_stage,
    profile_reached,
    save_state,
)
from profiles import READER_PROFILES, ReaderProfile, all_profile_slugs, get_profile
from toml_check import ProfileConfigMismatch, validate_config_matches_registry

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


ALL_STAGES: tuple[str, ...] = PROFILE_STAGES + SITE_STAGES


def ensure_dirs():
    """Create necessary directories and clean up old state files."""
    CONTENT_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    cleanup_old_state_files()


def profile_content_dir(slug: str) -> Path:
    """Return `content/<slug>/`, creating it if needed."""
    path = CONTENT_DIR / slug
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_previous_entries(slug: str, n: int = MEMORY_ENTRIES) -> str:
    """Load the last N entries from a profile's section for continuity."""
    section = CONTENT_DIR / slug
    if not section.exists():
        return ""

    posts = sorted(section.glob("*.md"), reverse=True)[:n]
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


def get_previous_news(slug: str, today: str, days: int = NEWS_MEMORY_DAYS) -> str:
    """Load the past N days of cached headlines for one profile.

    Days without a cache file for this profile are skipped silently, so
    the first few days on a new profile still work: history is simply
    empty until the pipeline has run for enough mornings to fill it.
    """
    today_date = datetime.strptime(today, "%Y-%m-%d")
    sections = []
    for offset in range(days, 0, -1):
        date_str = (today_date - timedelta(days=offset)).strftime("%Y-%m-%d")
        news = load_news_cache(date_str, slug)
        if not news:
            continue
        lines = [f"### {date_str}"]
        for category, articles in news.items():
            for a in articles:
                summary = " ".join((a.summary or "").split())[:200]
                line = f"- [{category}] {a.title} ({a.source})"
                if summary:
                    line += f": {summary}"
                lines.append(line)
        sections.append("\n".join(lines))
    return "\n\n".join(sections)


CLAUDE_CLI_TIMEOUT = 600
CLAUDE_CLI_ATTEMPTS = 2


def generate_reflection(
    profile: ReaderProfile,
    news_content: str,
    previous_entries: str,
    news_history: str = "",
) -> str:
    """Call Claude via CLI to generate today's reflection for one profile."""
    today_str = datetime.now().strftime("%A, %B %-d, %Y")
    system_prompt = load_system_prompt(profile)
    user_prompt = build_prompt(today_str, news_content, previous_entries, news_history)

    logger.info(
        f"[{profile.slug}] Calling claude CLI ({MODEL}) with "
        f"{len(user_prompt)} chars of context..."
    )

    result = None
    for attempt in range(1, CLAUDE_CLI_ATTEMPTS + 1):
        try:
            result = subprocess.run(
                [
                    "claude", "-p",
                    "--model", MODEL,
                    "--output-format", "json",
                    "--tools", "",
                    "--system-prompt", system_prompt,
                ],
                input=user_prompt,
                capture_output=True,
                text=True,
                timeout=CLAUDE_CLI_TIMEOUT,
            )
            break
        except subprocess.TimeoutExpired:
            logger.warning(
                f"[{profile.slug}] claude CLI timed out after "
                f"{CLAUDE_CLI_TIMEOUT}s (attempt {attempt}/{CLAUDE_CLI_ATTEMPTS})"
            )
            if attempt >= CLAUDE_CLI_ATTEMPTS:
                raise

    if result.returncode != 0:
        raise RuntimeError(
            f"claude CLI failed (exit {result.returncode}): {result.stderr}"
        )

    response = json.loads(result.stdout)
    if response.get("is_error"):
        raise RuntimeError(
            f"claude CLI error: {response.get('result', 'unknown')}"
        )

    response_text = response["result"]
    logger.info(f"[{profile.slug}] Generated {len(response_text)} chars")
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
    # real attack vectors without touching prose.
    (
        "dangerous-scheme markdown link",
        re.compile(
            r"]\s*\(\s*<?\s*(?:javascript|data|vbscript|file)\s*:",
            re.IGNORECASE,
        ),
    ),
    (
        "dangerous-scheme reference link",
        re.compile(
            r"^\s*\[[^\]]+\]:\s*<?\s*(?:javascript|data|vbscript|file)\s*:",
            re.IGNORECASE | re.MULTILINE,
        ),
    ),
    (
        "dangerous-scheme HTML attribute",
        re.compile(
            r"""(?:href|src|action|formaction)\s*=\s*["']?\s*(?:javascript|data|vbscript|file)\s*:""",
            re.IGNORECASE,
        ),
    ),
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

    Fails closed: any match aborts the day's run for that profile rather
    than trying to sanitize-and-continue. The raw LLM response has already
    been written to data/YYYY-MM-DD__<slug>-raw.md before this is called,
    so the rejected content remains available for inspection and replay.
    """
    for label, pattern in _DANGEROUS_HTML_PATTERNS:
        match = pattern.search(text)
        if match:
            raise ValueError(
                f"Reflection rejected: contains disallowed {label} "
                f"({match.group(0)!r})"
            )


def parse_reflection(
    raw: str, profile: ReaderProfile
) -> tuple[dict, str]:
    """Parse the frontmatter and body from Claude's response.

    The full raw text (frontmatter + body) is run through
    `_reject_dangerous_html` before any parsing so that injection
    attempts in title/mood_color/topics are caught alongside body-level
    attacks. Hugo's goldmark renderer (unsafe=false) then escapes any
    stray HTML at render time.
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
            "topics": [profile.topics[0]] if profile.topics else [],
        }, text

    # Use python-frontmatter for robust parsing (handles --- in body text)
    post = fm_parser.loads(text)
    return dict(post.metadata), post.content


# Characters backslash-escaped in attacker-influenced source titles/names
# before embedding them in the sources list. This is the subset of
# CommonMark 6.1 (Backslash escapes) that can affect inline parsing.
_SOURCE_MD_META_CHARS = r"\`*_{}[]()#+-.!|<>"
_SOURCE_MD_ESCAPE_RE = re.compile("([" + re.escape(_SOURCE_MD_META_CHARS) + "])")

# Sanity limit on URL length — any URL longer than this is almost certainly
# a pathological/adversarial payload, not a legitimate article link.
_MAX_SOURCE_URL_LEN = 2048

# mood_color is interpolated into inline `style="background-color: …"`
# attributes in Hugo templates. We validate structurally: if the value is
# not a plain hex color literal it is dropped entirely.
_MOOD_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{3,8}$")
_DEFAULT_MOOD_COLOR = "#8B6914"

# mood_score is a numeric frontmatter field rendered into the mood chart
# data island as a JSON number. Range matches the persona prompt (1 heavy
# → 10 light).
_MOOD_SCORE_MIN = 1
_MOOD_SCORE_MAX = 10
_DEFAULT_MOOD_SCORE = 5

# description is rendered into <meta name="description">, og:description, and
# twitter:description by layouts/partials/head.html, plus the JSON-LD
# BlogPosting block. Hugo auto-escapes meta `content` attributes and
# `jsonify` safely encodes JSON values, so HTML-escaping here would
# double-escape; we only coerce, collapse whitespace, and bound the length.
_MAX_DESCRIPTION_LEN = 160


def _escape_md_inline(text: str) -> str:
    """Backslash-escape CommonMark inline metacharacters and collapse whitespace.

    Titles and source names come from attacker-influenced RSS feeds. Without
    escaping, a hostile title containing `](...)` could reshape the link
    target, `*` could break italic runs, and `<script>` would reach the
    renderer as raw HTML.
    """
    if not text:
        return ""
    escaped = _SOURCE_MD_ESCAPE_RE.sub(r"\\\1", text)
    return re.sub(r"\s+", " ", escaped).strip()


def _validate_mood_color(value: object) -> str:
    """Return value if it is a plain hex color literal, else the default."""
    if isinstance(value, str) and _MOOD_COLOR_RE.fullmatch(value):
        return value
    logger.warning(
        f"Invalid mood_color {value!r}, falling back to {_DEFAULT_MOOD_COLOR}"
    )
    return _DEFAULT_MOOD_COLOR


def _validate_mood_score(value: object) -> int:
    """Return value if it is an int in [_MOOD_SCORE_MIN, _MOOD_SCORE_MAX].

    Booleans are rejected even though `isinstance(True, int)` is True.
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


def _validate_topics(value: object, profile: ReaderProfile) -> list[str]:
    """Filter `value` down to a list of allowed topics for this profile.

    The allowed set is `profile.topics`, in the profile registry. Unknown
    items are dropped; if the whole list filters to empty, fall back to
    the profile's first canonical topic.
    """
    allowed = frozenset(profile.topics)
    default: list[str] = [profile.topics[0]] if profile.topics else []

    if not isinstance(value, list):
        logger.warning(
            f"[{profile.slug}] Invalid topics {value!r} (not a list), "
            f"falling back to {default}"
        )
        return list(default)

    seen: set[str] = set()
    filtered: list[str] = []
    for item in value:
        if not isinstance(item, str):
            logger.warning(f"[{profile.slug}] Dropping non-string topic {item!r}")
            continue
        if item not in allowed:
            logger.warning(
                f"[{profile.slug}] Dropping unknown topic {item!r} "
                f"(allowed: {sorted(allowed)})"
            )
            continue
        if item in seen:
            continue
        seen.add(item)
        filtered.append(item)

    if not filtered:
        logger.warning(
            f"[{profile.slug}] All topics dropped, falling back to {default}"
        )
        return list(default)
    return filtered


def _first_sentence(text: str, window: int = 200) -> str:
    """Return the first sentence of `text` (looking only at the first `window` chars)."""
    if not text:
        return ""
    head = re.sub(r"\s+", " ", text.strip())[:window]
    end = head.find(". ")
    if end > 0:
        return head[: end + 1]
    return head


def _validate_description(value: object, body: str = "") -> str:
    """Return a safe meta-description string, falling back to the body if empty."""
    candidate = value if isinstance(value, str) else ""
    candidate = re.sub(r"\s+", " ", candidate).strip()
    if not candidate:
        candidate = _first_sentence(body)
    return candidate[:_MAX_DESCRIPTION_LEN]


def _safe_source_url(url: str) -> str | None:
    """Return the URL if it is safe to embed as a markdown link target."""
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

    Emits a Hugo shortcode call rather than raw HTML. All
    attacker-controlled text is backslash-escaped before interpolation;
    URLs are scheme-validated.
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


def save_entry(
    profile: ReaderProfile,
    frontmatter: dict,
    body: str,
    sources_md: str = "",
) -> Path:
    """Save the entry as a Hugo markdown file under content/<slug>/."""
    today = datetime.now(ZoneInfo(TIMEZONE))
    date_str = today.strftime("%Y-%m-%d")
    time_str = today.isoformat()

    # Build Hugo frontmatter. Every attacker-influenced field is run through
    # a structural validator at this boundary — the `.md` file on disk never
    # contains an un-validated value.
    fm = {
        "title": frontmatter.get(
            "title", f"Entry for {today.strftime('%B %-d, %Y')}"
        ),
        "date": time_str,
        "model": MODEL_DISPLAY,
        "profile": profile.slug,
        "description": _validate_description(frontmatter.get("description"), body),
        "mood_color": _validate_mood_color(frontmatter.get("mood_color")),
        "mood_score": _validate_mood_score(frontmatter.get("mood_score")),
        "topics": _validate_topics(frontmatter.get("topics"), profile),
        "draft": False,
    }

    import yaml as _yaml

    full_body = body + sources_md if sources_md else body
    content = "---\n" + _yaml.dump(fm, default_flow_style=False) + "---\n\n" + full_body

    section = profile_content_dir(profile.slug)
    filepath = section / f"{date_str}.md"
    filepath.write_text(content)
    logger.info(f"[{profile.slug}] Saved entry to {filepath}")

    return filepath


def save_raw_response(slug: str, raw: str) -> None:
    """Save the raw claude CLI response for debugging."""
    today = datetime.now().strftime("%Y-%m-%d")
    raw_path = raw_response_path(today, slug)
    raw_path.write_text(raw)
    logger.info(f"[{slug}] Saved raw response to {raw_path}")


def validate_build(date_str: str, shipped_slugs: list[str]) -> bool:
    """Verify Hugo output: site index + feed + per-profile entry pages."""
    public_dir = PROJECT_ROOT / "public"
    checks: dict[str, Path] = {
        "index": public_dir / "index.html",
        "feed": public_dir / "feed.xml",
    }
    for slug in shipped_slugs:
        checks[f"{slug} entry"] = public_dir / slug / date_str / "index.html"

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


def build_site(date_str: str, shipped_slugs: list[str]) -> bool:
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

        if not validate_build(date_str, shipped_slugs):
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


def git_commit_and_push(shipped_slugs: list[str]) -> bool:
    """Stage `content/`, commit, and push all profiles that shipped today."""
    today = datetime.now().strftime("%Y-%m-%d")
    slug_tag = ", ".join(shipped_slugs) if shipped_slugs else "no profiles"
    commit_msg = f"entry: {today} ({slug_tag})"

    try:
        _run_git("add", "content/")
        # Check if there are staged changes before committing
        status = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            cwd=PROJECT_ROOT,
            capture_output=True,
        )
        if status.returncode != 0:
            _run_git("commit", "-m", commit_msg)
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
            try:
                result = _run_git(
                    "pull", "--rebase", "--autostash", "origin", "main", timeout=30
                )
                if result.stdout.strip():
                    logger.info(f"Git pull: {result.stdout.strip()}")
            except subprocess.CalledProcessError as e:
                stderr = e.stderr or ""
                if "CONFLICT" in stderr or "could not apply" in stderr:
                    logger.error(
                        f"Git rebase conflict — aborting rebase and failing. "
                        f"Manual resolution needed: {stderr}"
                    )
                    try:
                        _run_git("rebase", "--abort")
                    except subprocess.CalledProcessError:
                        pass
                    return False
                logger.warning(f"Git pull failed (attempt {attempt}): {stderr}")
                if attempt >= GIT_PUSH_RETRIES:
                    return False
                continue

            _run_git("push", "origin", "main", timeout=30)
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
                    logger.error(
                        f"Push failed after {GIT_PUSH_RETRIES} attempts: {stderr}"
                    )
                    return False
                continue
            logger.error(f"Git push failed: {stderr}")
            return False
        except Exception as e:
            logger.error(f"Git push error: {e}")
            return False

    return False


def notify_failure(error: str) -> None:
    """Append a failure line to logs/failures.log for the health check."""
    failure_path = LOG_DIR / "failures.log"
    timestamp = datetime.now().isoformat()
    with open(failure_path, "a") as f:
        f.write(f"{timestamp}: {error}\n")
    logger.error(f"FAILURE: {error}")


# --- Per-profile stage runners ------------------------------------------

def _stage_index(stage: str) -> int:
    """Return the position of `stage` in ALL_STAGES. Raises on unknown."""
    if stage not in ALL_STAGES:
        raise ValueError(
            f"Unknown pipeline stage {stage!r}. Known: {list(ALL_STAGES)}"
        )
    return ALL_STAGES.index(stage)


def run_profile_pipeline(
    profile: ReaderProfile,
    today: str,
    state: dict,
    stop_at_stage: str,
) -> bool:
    """Advance one profile through fetched -> generated -> saved.

    Returns True if the profile reached the `saved` stage (or stopped
    earlier because `stop_at_stage` said so and every requested stage
    completed). Any failure at any stage is logged and returns False;
    the caller continues with the remaining profiles.
    """
    slug = profile.slug
    stop_idx = _stage_index(stop_at_stage)

    news: dict[str, list[Article]] | None = None
    news_content = ""

    # Stage: fetched
    if not profile_reached(state, slug, "fetched"):
        logger.info(f"[{slug}] Step: fetching news...")
        try:
            news = fetch_all_news(profile.feeds, profile_label=slug)
            news_content = format_news_for_prompt(news)
            if not news_content.strip():
                raise RuntimeError("no news content after screening")
            logger.info(
                f"[{slug}] Fetched {len(news_content)} chars of news content"
            )
            save_news_cache(today, slug, news)
            mark_profile_stage(state, slug, "fetched")
            save_state(today, state)
        except (AllFeedsFailedError, Exception) as e:
            notify_failure(f"[{slug}] News fetch failed: {e}")
            return False
    else:
        logger.info(f"[{slug}] Fetch already done, loading from cache.")

    if stop_idx <= _stage_index("fetched"):
        return True

    # Ensure `news` is loaded before generation
    if news is None:
        news = load_news_cache(today, slug)
        if news is None:
            logger.warning(
                f"[{slug}] News cache missing on resume — re-fetching"
            )
            try:
                news = fetch_all_news(profile.feeds, profile_label=slug)
                save_news_cache(today, slug, news)
            except Exception as e:
                notify_failure(f"[{slug}] News re-fetch on resume failed: {e}")
                return False
        news_content = format_news_for_prompt(news)

    # Stage: generated
    raw_response: str | None = None
    if not profile_reached(state, slug, "generated"):
        logger.info(f"[{slug}] Step: generating reflection...")
        previous_entries = get_previous_entries(slug)
        news_history = get_previous_news(slug, today)
        logger.info(
            f"[{slug}] Loaded {len(news_history)} chars of news history"
        )
        try:
            raw_response = generate_reflection(
                profile, news_content, previous_entries, news_history
            )
            save_raw_response(slug, raw_response)
            mark_profile_stage(state, slug, "generated")
            save_state(today, state)
        except Exception as e:
            notify_failure(f"[{slug}] Claude CLI call failed: {e}")
            return False
    else:
        logger.info(f"[{slug}] Generate already done, skipping.")

    if stop_idx <= _stage_index("generated"):
        return True

    # Stage: saved
    if not profile_reached(state, slug, "saved"):
        logger.info(f"[{slug}] Step: parsing and saving entry...")
        if raw_response is None:
            raw_path = raw_response_path(today, slug)
            if not raw_path.exists():
                notify_failure(
                    f"[{slug}] Cannot resume: raw response file missing at {raw_path}"
                )
                return False
            raw_response = raw_path.read_text()

        try:
            frontmatter, body = parse_reflection(raw_response, profile)
            sources_md = format_sources(news)
            save_entry(profile, frontmatter, body, sources_md)
            mark_profile_stage(state, slug, "saved")
            save_state(today, state)
        except Exception as e:
            notify_failure(f"[{slug}] Failed to save entry: {e}")
            return False
    else:
        logger.info(f"[{slug}] Save already done, skipping.")

    return True


def _parse_args() -> argparse.Namespace:
    """Parse the CLI. Default (no args) is a full run over every profile."""
    parser = argparse.ArgumentParser(
        description="Run the Claude's Daily Digest pipeline."
    )
    parser.add_argument(
        "--profiles",
        default="",
        help=(
            "comma-separated subset of profile slugs to run "
            f"(default: all — {', '.join(all_profile_slugs())})"
        ),
    )
    parser.add_argument(
        "--until",
        default="pushed",
        choices=list(ALL_STAGES),
        help="stop after this stage (default: pushed, i.e. full run)",
    )
    return parser.parse_args()


def _select_profiles(subset: str) -> list[ReaderProfile]:
    """Resolve --profiles to a list, keeping registry order."""
    if not subset.strip():
        return list(READER_PROFILES.values())
    requested = [s.strip() for s in subset.split(",") if s.strip()]
    unknown = [s for s in requested if s not in READER_PROFILES]
    if unknown:
        raise SystemExit(
            f"Unknown profile(s) in --profiles: {unknown}. "
            f"Known: {list(READER_PROFILES.keys())}"
        )
    return [READER_PROFILES[s] for s in requested]


def main() -> int:
    """Main daily generation pipeline."""
    logger.info("=" * 60)
    logger.info("Claude's Daily Digest — Daily Generation")
    logger.info("=" * 60)

    args = _parse_args()

    # Startup validation: the Hugo config must know about the same profiles
    # as the Python registry, in the same order, or nav tabs and entry
    # sections will diverge silently.
    try:
        validate_config_matches_registry()
    except (ProfileConfigMismatch, FileNotFoundError) as e:
        notify_failure(f"Startup validation failed: {e}")
        return 2

    ensure_dirs()

    today = datetime.now().strftime("%Y-%m-%d")
    state = load_state(today)
    if is_legacy_complete(state):
        logger.info(
            f"State for {today} is in the legacy single-stage shape — "
            f"treating the day as already complete. Skipping."
        )
        return 0

    profiles = _select_profiles(args.profiles)
    stop_at_stage = args.until
    stop_idx = _stage_index(stop_at_stage)

    logger.info(
        f"Running {len(profiles)} profile(s): "
        f"{[p.slug for p in profiles]} through stage '{stop_at_stage}'"
    )

    # --- Per-profile stages -------------------------------------------------
    reached_target: list[str] = []  # profiles that reached stop_at_stage
    saved_slugs: list[str] = []     # profiles that reached the `saved` stage
    for profile in profiles:
        ok = run_profile_pipeline(profile, today, state, stop_at_stage)
        if ok:
            reached_target.append(profile.slug)
        if profile_reached(state, profile.slug, "saved"):
            saved_slugs.append(profile.slug)

    # If the caller stopped before "saved", the run's success signal is
    # whether ANY profile reached the requested stage. If they asked for
    # "saved" or beyond, we insist that at least one profile shipped;
    # otherwise the day is a failure.
    if stop_idx < _stage_index("saved"):
        if not reached_target:
            notify_failure(
                f"No profiles reached '{stop_at_stage}' — the day did not ship"
            )
            return 1
        logger.info(
            f"Stopping at --until {stop_at_stage}. "
            f"Profiles that reached this stage: {reached_target}"
        )
        return 0

    if not saved_slugs:
        notify_failure("No profiles reached 'saved' — the day did not ship")
        return 1

    # If the caller stopped at "saved" exactly, we are done before build/push.
    if stop_idx < _stage_index("built"):
        logger.info(
            f"Stopping at --until {stop_at_stage}. "
            f"Profiles saved today: {saved_slugs}"
        )
        return 0

    # --- Shared stages: build + push ---------------------------------------
    # Build over EVERY profile that reached "saved" today, not just the
    # subset the current run touched, so a resumed session finishes the
    # site with every desk that has landed so far.
    all_shipped_today = [
        slug for slug in READER_PROFILES.keys()
        if profile_reached(state, slug, "saved")
    ]

    if not state.get("built"):
        logger.info(f"Step: building site over {all_shipped_today}...")
        if not build_site(today, all_shipped_today):
            notify_failure("Hugo build failed")
            return 1
        state["built"] = True
        save_state(today, state)
    else:
        logger.info("Build already done, skipping.")

    if stop_idx < _stage_index("pushed"):
        logger.info(
            f"Stopping at --until {stop_at_stage}. Site built, not pushed."
        )
        return 0

    if not state.get("pushed"):
        logger.info(f"Step: committing and pushing {all_shipped_today}...")
        if not git_commit_and_push(all_shipped_today):
            notify_failure("Git push failed")
            return 1
        state["pushed"] = True
        save_state(today, state)
    else:
        logger.info(f"Entry for {today} already pushed. Nothing to do.")

    logger.info("Daily generation complete!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
