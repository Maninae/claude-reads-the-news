"""Claude's Daily Digest — persona loader and prompt construction.

The persona is split across three files, assembled at runtime:
- prompts/persona_base.md: the shared voice, style, and output-format
  contract. Same text for every profile. Two placeholders:
    {{PROFILE_BRIEF}}  — a paragraph naming the desk and its remit
    {{TOPICS_LIST}}    — the closed set of allowed frontmatter topics
- prompts/profiles/<slug>.md: the desk-specific "YOUR BEAT" fragment
  (what this desk covers, what it hands off to others).
- ReaderProfile in scripts/profiles.py: the machine-readable manifest
  the pipeline reads for feeds, topics, and section routing.

`load_system_prompt(profile)` composes the first two and substitutes the
placeholders. It fails loud if a file or a placeholder is missing —
silently shipping a prompt with `{{PROFILE_BRIEF}}` still in it would be
a much worse outcome than a hard crash at the start of the run.
"""

from pathlib import Path

from config import MODEL_DISPLAY
from profiles import ReaderProfile

PROMPTS_DIR = Path(__file__).parent.parent / "prompts"
PERSONA_BASE_PATH = PROMPTS_DIR / "persona_base.md"
PROFILES_PROMPTS_DIR = PROMPTS_DIR / "profiles"

# The two placeholders the base template exposes. Kept as constants so
# any change to the tokens has one source of truth.
_PLACEHOLDER_BRIEF = "{{PROFILE_BRIEF}}"
_PLACEHOLDER_TOPICS = "{{TOPICS_LIST}}"


def load_system_prompt(profile: ReaderProfile) -> str:
    """Assemble the full system prompt for one reader profile.

    Reads persona_base.md, appends the profile's fragment (as the
    PROFILE_BRIEF), and substitutes the topics list into TOPICS_LIST.

    Raises FileNotFoundError if either markdown file is missing, and
    ValueError if the base template is missing a required placeholder.
    Failing loud here beats shipping a broken prompt to the model.
    """
    if not PERSONA_BASE_PATH.exists():
        raise FileNotFoundError(
            f"persona base template missing: {PERSONA_BASE_PATH}"
        )

    fragment_path = PROFILES_PROMPTS_DIR / profile.prompt_filename
    if not fragment_path.exists():
        raise FileNotFoundError(
            f"profile fragment missing for {profile.slug!r}: {fragment_path}"
        )

    base_text = PERSONA_BASE_PATH.read_text()
    fragment_text = fragment_path.read_text().strip()

    if _PLACEHOLDER_BRIEF not in base_text:
        raise ValueError(
            f"persona base template missing placeholder {_PLACEHOLDER_BRIEF}"
        )
    if _PLACEHOLDER_TOPICS not in base_text:
        raise ValueError(
            f"persona base template missing placeholder {_PLACEHOLDER_TOPICS}"
        )

    topics_list = ", ".join(profile.topics)
    return base_text.replace(_PLACEHOLDER_BRIEF, fragment_text).replace(
        _PLACEHOLDER_TOPICS, topics_list
    )


def build_prompt(
    date: str,
    news_content: str,
    previous_entries: str = "",
    news_history: str = "",
) -> str:
    """Build the full user prompt for Claude.

    Ordered for continuity: the past month of headlines first, then the
    recent entries, then today's news, then the writing instruction.
    """
    parts = [f"Today's date: {date}"]
    parts.append(f"Model: {MODEL_DISPLAY}\n")

    if news_history:
        parts.append(
            "## THE PAST MONTH OF NEWS (headlines you've read over the last "
            "30 days — read these first to remember where the threads stand: "
            "what developed, what fizzled, what nobody followed up on):\n"
        )
        parts.append(news_history)
        parts.append("\n---\n")

    if previous_entries:
        parts.append(
            "## YOUR RECENT ENTRIES (for continuity — reference if relevant, "
            "track developing stories, notice your own patterns):\n"
        )
        parts.append(previous_entries)
        parts.append("\n---\n")

    parts.append("## TODAY'S NEWS:\n")
    parts.append(news_content)

    parts.append(
        "\n\n---\n\nWrite today's entry. Remember: react, don't summarize. "
        "This is your journal, not a newspaper."
    )

    return "\n".join(parts)
