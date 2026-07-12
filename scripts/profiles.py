"""Reader profiles: the six tabs that carve the daily digest into desks.

Each `ReaderProfile` is one desk. Every daily run walks the profile list,
fetches that desk's feeds, generates its own entry with its own persona
fragment, and saves to its own section. Continuity memory (recent entries,
past headlines) is scoped per profile as well, so the Tech desk does not
keep bringing up geopolitics and the International desk does not develop
opinions about GPU roadmaps.

The registry is the single source of truth: `config.toml`'s
`[[params.reader_profiles]]` block and the Hugo tab order must match this
dict's insertion order exactly. `generate.py` asserts that at startup.

To add a profile:
1. Add a new entry at the desired position in READER_PROFILES.
2. Write prompts/profiles/<slug>.md (the "YOUR BEAT" fragment).
3. Add the matching `[[params.reader_profiles]]` block to config.toml,
   in the same position, with the same slug and label.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ReaderProfile:
    """One reader profile: a desk with its own feeds, persona, and section.

    - slug: URL-safe identifier used for content path, data filenames, and
      the persona fragment filename. Lowercase, no spaces.
    - tab_label: what the site nav shows. Human-readable.
    - description: one-line pitch for what this desk covers, used both on
      the site and substituted into the persona system prompt.
    - feeds: sub-category name -> list of feed/query URLs. Sub-categories
      group related sources within a desk (e.g. tech might split "outlets"
      from "ai_search") and become the section headers in the news prompt.
    - topics: the closed set of allowed frontmatter topics for entries in
      this profile. Substituted into the persona prompt, and used by
      generate.py to validate the model's `topics:` list.
    - prompt_filename: relative to prompts/profiles/, defaults to
      `<slug>.md`. Kept configurable so a slug rename does not orphan the
      fragment.
    """
    slug: str
    tab_label: str
    description: str
    feeds: dict[str, list[str]]
    topics: tuple[str, ...]
    prompt_filename: str = ""

    def __post_init__(self):
        # Fill in the default fragment filename after the frozen-dataclass
        # constructor runs. object.__setattr__ is the frozen-dataclass
        # escape hatch that lets __post_init__ set a defaulted field.
        if not self.prompt_filename:
            object.__setattr__(self, "prompt_filename", f"{self.slug}.md")


# The Google News search-query pattern, reused across profiles. `when:1d`
# restricts to the last day so the tech AI feed does not surface last
# week's launches every morning.
_GN_SEARCH = (
    "https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
)


# The six desks, in tab order. Iteration order == site tab order because
# Python 3.7+ dicts preserve insertion order.
READER_PROFILES: dict[str, ReaderProfile] = {
    "international": ReaderProfile(
        slug="international",
        tab_label="International",
        description="The world beyond the United States",
        feeds={
            "world": [
                "https://feeds.bbci.co.uk/news/world/rss.xml",
                "https://www.theguardian.com/world/rss",
                "https://www.aljazeera.com/xml/rss/all.xml",
                "https://news.google.com/rss/headlines/section/topic/WORLD?hl=en-US&gl=US&ceid=US:en",
            ],
            "geopolitics": [
                _GN_SEARCH.format(query="diplomacy+OR+geopolitics+when:1d"),
            ],
        },
        topics=("diplomacy", "conflict", "economy", "society", "climate"),
    ),
    "usa": ReaderProfile(
        slug="usa",
        tab_label="USA",
        description="American politics and public life",
        feeds={
            "national": [
                "https://rss.nytimes.com/services/xml/rss/nyt/US.xml",
                "https://feeds.npr.org/1001/rss.xml",
                "https://news.google.com/rss/headlines/section/topic/NATION?hl=en-US&gl=US&ceid=US:en",
            ],
            "politics": [
                _GN_SEARCH.format(query="US+politics+congress+when:1d"),
            ],
        },
        topics=("politics", "policy", "courts", "economy", "society"),
    ),
    "tech": ReaderProfile(
        slug="tech",
        tab_label="Tech",
        description="AI and the digital world",
        feeds={
            "outlets": [
                "https://news.ycombinator.com/rss",
                "https://feeds.arstechnica.com/arstechnica/index",
                "https://www.theverge.com/rss/index.xml",
            ],
            "ai": [
                _GN_SEARCH.format(query="artificial+intelligence+when:1d"),
            ],
        },
        topics=("ai", "software", "hardware", "policy", "culture"),
    ),
    "energy": ReaderProfile(
        slug="energy",
        tab_label="Energy",
        description="Climate, power, and the grid",
        feeds={
            "utilities": [
                "https://www.utilitydive.com/feeds/news/",
            ],
            "climate_and_grid": [
                _GN_SEARCH.format(query="energy+climate+when:1d"),
                _GN_SEARCH.format(query="renewables+grid+when:1d"),
            ],
        },
        topics=("renewables", "climate", "policy", "grid", "markets"),
    ),
    "markets": ReaderProfile(
        slug="markets",
        tab_label="Markets",
        description="Money, macro, and business",
        feeds={
            "business": [
                "https://news.google.com/rss/headlines/section/topic/BUSINESS?hl=en-US&gl=US&ceid=US:en",
                "https://www.cnbc.com/id/100003114/device/rss/rss.html",
            ],
            "macro": [
                _GN_SEARCH.format(query="stocks+markets+macro+when:1d"),
            ],
        },
        topics=("stocks", "macro", "business", "commodities", "policy"),
    ),
    "wildcard": ReaderProfile(
        slug="wildcard",
        tab_label="Wildcard",
        description="Whatever caught Claude's eye",
        feeds={
            "wildcard": [
                "https://news.ycombinator.com/rss",
                "https://news.google.com/rss/headlines/section/topic/SCIENCE?hl=en-US&gl=US&ceid=US:en",
            ],
        },
        topics=("science", "culture", "oddities", "internet", "history"),
    ),
}


def all_profile_slugs() -> list[str]:
    """Return every profile slug in the canonical tab order."""
    return list(READER_PROFILES.keys())


def get_profile(slug: str) -> ReaderProfile:
    """Look up a profile by slug. Fails loud on an unknown slug."""
    try:
        return READER_PROFILES[slug]
    except KeyError as e:
        known = ", ".join(READER_PROFILES.keys())
        raise KeyError(
            f"Unknown reader-profile slug {slug!r}. Known slugs: {known}"
        ) from e
