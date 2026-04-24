"""Configuration for The Watcher daily generation."""

import json
from pathlib import Path

# Paths
PROJECT_ROOT = Path(__file__).parent.parent
CONTENT_DIR = PROJECT_ROOT / "content" / "posts"
DATA_DIR = PROJECT_ROOT / "data"
LOG_DIR = PROJECT_ROOT / "logs"
FEED_HEALTH_PATH = DATA_DIR / "feed-health.json"

# Load local config (user-specific paths, gitignored)
_local_config_path = PROJECT_ROOT / "local.json"
LOCAL_CONFIG = {}
if _local_config_path.exists():
    with open(_local_config_path) as _f:
        LOCAL_CONFIG = json.load(_f)

TIMEZONE = LOCAL_CONFIG.get("timezone", "America/Los_Angeles")

# Model config — uses claude CLI (subscription), not direct API
MODEL = "sonnet"
MODEL_DISPLAY = "Claude Sonnet 4.6"

# News sources — RSS feeds that actually work and give usable content
# Mix of direct outlet RSS + Google News topic feeds (free, unlimited, no API key)
RSS_FEEDS = {
    "politics": [
        "https://news.google.com/rss/headlines/section/topic/WORLD?hl=en-US&gl=US&ceid=US:en",
        "https://feeds.reuters.com/Reuters/worldNews",
        "https://feeds.bbci.co.uk/news/world/rss.xml",
        "https://rss.nytimes.com/services/xml/rss/nyt/World.xml",
    ],
    "markets": [
        "https://news.google.com/rss/headlines/section/topic/BUSINESS?hl=en-US&gl=US&ceid=US:en",
        "https://feeds.content.dowjones.io/public/rss/mw_topstories",
        "https://www.cnbc.com/id/100003114/device/rss/rss.html",
    ],
    "energy": [
        "https://news.google.com/rss/search?q=energy+climate+when:1d&hl=en-US&gl=US&ceid=US:en",
        "https://feeds.reuters.com/reuters/businessNews",
        "https://www.utilitydive.com/feeds/news/",
    ],
    "tech": [
        "https://news.google.com/rss/headlines/section/topic/TECHNOLOGY?hl=en-US&gl=US&ceid=US:en",
        "https://feeds.arstechnica.com/arstechnica/index",
        "https://www.theverge.com/rss/index.xml",
    ],
    "wildcard": [
        "https://hnrss.org/frontpage?count=10",
        "https://news.google.com/rss/headlines/section/topic/SCIENCE?hl=en-US&gl=US&ceid=US:en",
    ],
}

# How many articles to fetch per category
ARTICLES_PER_CATEGORY = 5

# How many previous entries to include for continuity
MEMORY_ENTRIES = 5

# Topics to cover
TOPICS = ["politics", "markets", "energy", "tech", "wildcard"]
