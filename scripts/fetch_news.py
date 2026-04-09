"""Fetch and deduplicate news from RSS feeds."""

import hashlib
import logging
import re
from dataclasses import dataclass, field

import feedparser
import trafilatura

from config import ARTICLES_PER_CATEGORY, RSS_FEEDS

logger = logging.getLogger(__name__)


@dataclass
class Article:
    title: str
    summary: str
    url: str
    source: str
    category: str
    full_text: str = ""
    fingerprint: str = field(default="", init=False)

    def __post_init__(self):
        # Simple fingerprint for dedup: normalized title hash
        normalized = re.sub(r"[^a-z0-9 ]", "", self.title.lower()).strip()
        self.fingerprint = hashlib.md5(normalized.encode()).hexdigest()[:12]


def fetch_rss_feed(url: str, category: str) -> list[Article]:
    """Fetch articles from a single RSS feed."""
    try:
        feed = feedparser.parse(url)
        articles = []
        for entry in feed.entries[:ARTICLES_PER_CATEGORY]:
            summary = ""
            if hasattr(entry, "summary"):
                # Strip HTML tags from summary
                summary = re.sub(r"<[^>]+>", "", entry.get("summary", ""))
            articles.append(
                Article(
                    title=entry.get("title", "Untitled"),
                    summary=summary[:500],
                    url=entry.get("link", ""),
                    source=feed.feed.get("title", url),
                    category=category,
                )
            )
        return articles
    except Exception as e:
        logger.warning(f"Failed to fetch {url}: {e}")
        return []


def extract_full_text(article: Article) -> Article:
    """Extract full article text using trafilatura."""
    if not article.url:
        return article
    try:
        downloaded = trafilatura.fetch_url(article.url)
        if downloaded:
            text = trafilatura.extract(downloaded)
            if text:
                # Limit to ~1000 chars to control token usage
                article.full_text = text[:1000]
    except Exception as e:
        logger.warning(f"Failed to extract text from {article.url}: {e}")
    return article


def deduplicate(articles: list[Article]) -> list[Article]:
    """Remove duplicate articles based on title similarity."""
    seen_fingerprints = set()
    unique = []
    for article in articles:
        if article.fingerprint not in seen_fingerprints:
            seen_fingerprints.add(article.fingerprint)
            unique.append(article)
    return unique


def fetch_all_news() -> dict[str, list[Article]]:
    """Fetch news from all configured RSS feeds, deduplicated."""
    all_articles = []

    for category, urls in RSS_FEEDS.items():
        for url in urls:
            articles = fetch_rss_feed(url, category)
            all_articles.extend(articles)
            logger.info(f"Fetched {len(articles)} articles from {url}")

    # Deduplicate across all sources
    unique = deduplicate(all_articles)
    logger.info(
        f"Total: {len(all_articles)} articles, {len(unique)} after dedup"
    )

    # Extract full text for the top articles per category
    by_category: dict[str, list[Article]] = {}
    for article in unique:
        by_category.setdefault(article.category, []).append(article)

    # Extract full text for top 3 per category (limit API/scraping load)
    for category, articles in by_category.items():
        for i, article in enumerate(articles[:3]):
            by_category[category][i] = extract_full_text(article)

    return by_category


def format_news_for_prompt(news: dict[str, list[Article]]) -> str:
    """Format all news into a structured string for the Claude prompt."""
    sections = []
    for category, articles in news.items():
        lines = [f"\n## {category.upper()}"]
        for a in articles[:ARTICLES_PER_CATEGORY]:
            lines.append(f"\n### {a.title}")
            lines.append(f"Source: {a.source}")
            if a.full_text:
                lines.append(f"\n{a.full_text}")
            elif a.summary:
                lines.append(f"\n{a.summary}")
        sections.append("\n".join(lines))
    return "\n\n---\n".join(sections)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    news = fetch_all_news()
    print(format_news_for_prompt(news))
