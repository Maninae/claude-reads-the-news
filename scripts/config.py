"""Configuration for Claude's Daily Digest generation.

Cross-profile knobs live here. Per-profile feed lists, topics, and
persona fragments live in scripts/profiles.py + prompts/profiles/
(one module + one markdown file per desk).
"""

import json
from pathlib import Path

# Paths
PROJECT_ROOT = Path(__file__).parent.parent
CONTENT_DIR = PROJECT_ROOT / "content"
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
# [1m] = 1M-token context window, needed to fit the 30-day news history
MODEL = "sonnet[1m]"
MODEL_DISPLAY = "Claude Sonnet 4.6"

# How many articles to fetch per feed sub-category. Applied per profile,
# so a 5-cap desk with two sub-categories still surfaces up to 10 rows.
ARTICLES_PER_CATEGORY = 5

# How many previous entries to include for continuity (per profile).
MEMORY_ENTRIES = 5

# How many past days of news headlines to include for continuity.
# Must be <= the state-file retention window (cleanup_old_state_files
# deletes news caches older than 30 days).
NEWS_MEMORY_DAYS = 30
