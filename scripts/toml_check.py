"""Startup validation: config.toml's reader_profiles block matches the registry.

The pipeline (Python) and the site (Hugo) each carry their own copy of
the profile list: the registry in scripts/profiles.py and the
`[[params.reader_profiles]]` array in config.toml. Drift between the two
would silently produce entries the site can't render or nav tabs that
never get an entry. This module refuses to start the run when the two
copies disagree.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from config import PROJECT_ROOT
from profiles import READER_PROFILES


class ProfileConfigMismatch(RuntimeError):
    """Raised when config.toml disagrees with the READER_PROFILES registry."""


def _load_config_toml() -> dict:
    """Parse config.toml at the repo root. Raises on missing file."""
    path = PROJECT_ROOT / "config.toml"
    if not path.exists():
        raise FileNotFoundError(f"config.toml missing at {path}")
    with open(path, "rb") as f:
        return tomllib.load(f)


def _expected_toml_block() -> str:
    """Return the exact TOML snippet config.toml must contain."""
    lines = []
    for profile in READER_PROFILES.values():
        lines.append("[[params.reader_profiles]]")
        lines.append(f'  slug = "{profile.slug}"')
        lines.append(f'  label = "{profile.tab_label}"')
        lines.append(f'  description = "{profile.description}"')
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def validate_config_matches_registry() -> None:
    """Fail loud if config.toml's reader_profiles differ from the registry.

    Checks presence of the block and exact (slug, label, description)
    triples in exact order. Any mismatch raises ProfileConfigMismatch
    with the exact TOML block that would fix it.
    """
    config = _load_config_toml()
    params = config.get("params") or {}
    block = params.get("reader_profiles")

    if not block:
        raise ProfileConfigMismatch(
            "config.toml is missing [[params.reader_profiles]]. Add:\n\n"
            + _expected_toml_block()
        )

    got = [
        (
            (entry.get("slug") or "").strip(),
            (entry.get("label") or "").strip(),
            (entry.get("description") or "").strip(),
        )
        for entry in block
    ]
    want = [
        (p.slug, p.tab_label, p.description) for p in READER_PROFILES.values()
    ]

    if got != want:
        raise ProfileConfigMismatch(
            "config.toml [[params.reader_profiles]] does not match "
            "READER_PROFILES.\n"
            f"  got:  {got}\n"
            f"  want: {want}\n\n"
            "Replace the [[params.reader_profiles]] blocks in config.toml with:\n\n"
            + _expected_toml_block()
        )
