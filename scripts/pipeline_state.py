"""Resumable per-profile pipeline state.

Every run writes one state file, `data/YYYY-MM-DD-state.json`, whose
shape encodes progress per profile plus the two site-wide stages:

    {
      "profiles": {"international": "generated", "tech": "saved", ...},
      "built":   false,
      "pushed":  false,
      "updated": "2026-07-12T07:00:00"
    }

Per-profile stages advance through: fetched -> generated -> saved.
Once every profile that will succeed has reached `saved`, the run runs
the shared `built` and `pushed` stages once. A rerun skips any profile
already at or past the requested stage.

The old single-key `{"stage": ...}` shape used before the split ships is
treated as a completed legacy day: `load_state` returns a sentinel so
`main` exits early rather than crashing on the missing "profiles" key.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from config import DATA_DIR

logger = logging.getLogger(__name__)


# The per-profile stages, in order. Iteration and index math elsewhere
# rely on this order — do not reorder without checking generate.py.
PROFILE_STAGES: tuple[str, ...] = ("fetched", "generated", "saved")

# The shared, site-wide stages, in order.
SITE_STAGES: tuple[str, ...] = ("built", "pushed")


# Sentinel state returned when a state file predates the multi-profile
# refactor (only has the legacy "stage" key). Callers treat it as "day
# already fully complete, exit".
LEGACY_COMPLETE = {"__legacy_complete__": True}


def state_path(date_str: str) -> Path:
    """Return the path to the given day's pipeline state file."""
    return DATA_DIR / f"{date_str}-state.json"


def load_state(date_str: str) -> dict:
    """Load pipeline state for a given date.

    Returns:
    - An empty dict if no state file exists yet (fresh day).
    - LEGACY_COMPLETE if the file uses the pre-refactor {"stage": ...}
      shape — the caller treats that as a completed legacy day and exits.
    - The parsed dict for a well-formed new-shape file, with defaults
      filled in for any missing top-level keys.
    """
    path = state_path(date_str)
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"Could not read state file {path.name}: {e}")
        return {}

    # Legacy shape: {"stage": "pushed", "updated": "..."} with no "profiles".
    if "profiles" not in raw and "stage" in raw:
        logger.info(
            f"State file {path.name} uses the legacy single-stage shape; "
            f"treating {date_str} as a completed legacy day."
        )
        return dict(LEGACY_COMPLETE)

    return {
        "profiles": raw.get("profiles", {}),
        "built": bool(raw.get("built", False)),
        "pushed": bool(raw.get("pushed", False)),
        "updated": raw.get("updated"),
    }


def save_state(date_str: str, state: dict) -> None:
    """Write the state dict back to disk, timestamping the update."""
    state["updated"] = datetime.now().isoformat()
    try:
        state_path(date_str).write_text(json.dumps(state, indent=2) + "\n")
    except OSError as e:
        logger.warning(f"Could not write state file: {e}")


def mark_profile_stage(state: dict, slug: str, stage: str) -> None:
    """Record that `slug` has completed `stage`, in-place on state.

    Only advances forward: if `slug` is already past `stage`, this is a
    no-op so a resumed run cannot regress a profile.
    """
    if stage not in PROFILE_STAGES:
        raise ValueError(
            f"Unknown profile stage {stage!r}. "
            f"Known: {list(PROFILE_STAGES)}"
        )
    profiles = state.setdefault("profiles", {})
    current = profiles.get(slug)
    if current and PROFILE_STAGES.index(current) >= PROFILE_STAGES.index(stage):
        return
    profiles[slug] = stage


def profile_reached(state: dict, slug: str, stage: str) -> bool:
    """True if `slug` has completed `stage` (or a later one) in `state`."""
    if stage not in PROFILE_STAGES:
        raise ValueError(f"Unknown profile stage {stage!r}")
    current = state.get("profiles", {}).get(slug)
    if not current or current not in PROFILE_STAGES:
        return False
    return PROFILE_STAGES.index(current) >= PROFILE_STAGES.index(stage)


def is_legacy_complete(state: dict) -> bool:
    """True if `load_state` returned the LEGACY_COMPLETE sentinel."""
    return bool(state.get("__legacy_complete__"))
