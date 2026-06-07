#!/usr/bin/env python3
"""Build the OG image assets for Claude's Daily Digest.

Produces two PNGs:

1. ``assets/og/base.png`` (1200×630). Broadsheet template used at build
   time by ``layouts/partials/head.html`` via Hugo's ``images.Text``
   filter — masthead at top, double rule, ``#c45d3e`` accent line, and
   a large empty band for the post title to be overlaid on. Each
   per-post share image is composited at build time, so this file just
   needs to be the empty stage.

2. ``static/og-image.png`` (1200×630). The home/list fallback share
   card with the full "Claude's Daily Digest" wordmark centered. Used
   for the home page, archive, and any non-page contexts.

Re-run after any rename or design tweak. Designed to be idempotent —
re-running produces byte-identical output.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

REPO = Path(__file__).resolve().parent.parent
SERIF = REPO / "assets" / "fonts" / "og-title.ttf"  # Playfair Display
SANS = REPO / "assets" / "fonts" / "og-label.ttf"  # Source Sans 3 (vendored)

W, H = 1200, 630
BG = (248, 247, 244)  # #f8f7f4 — off-white
INK = (26, 24, 23)  # #1a1817 — near-black
MUTED = (120, 113, 108)  # warm gray for small labels
ACCENT = (196, 93, 62)  # #c45d3e — burnt orange


def _font(path: Path, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(path), size=size)


def _center_x(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont) -> int:
    """Return the x-coord that centers `text` horizontally in the canvas."""
    bbox = draw.textbbox((0, 0), text, font=font)
    return (W - (bbox[2] - bbox[0])) // 2 - bbox[0]


def _broadsheet_chrome(img: Image.Image) -> ImageDraw.ImageDraw:
    """Paint the shared broadsheet frame (rules, masthead label, footer label).

    Both the base (per-post) image and the home og-image share this chrome.
    """
    draw = ImageDraw.Draw(img)

    # Top double rule + small caps labels.
    draw.line([(70, 75), (W - 70, 75)], fill=INK, width=2)
    draw.line([(70, 85), (W - 70, 85)], fill=INK, width=1)

    sans_label = _font(SANS, 18)
    draw.text((90, 40), "VOL. I  ·  DAILY EDITION", font=sans_label, fill=MUTED)
    domain = "AIREADSTHENEWS.CO"
    bbox = draw.textbbox((0, 0), domain, font=sans_label)
    draw.text((W - 90 - (bbox[2] - bbox[0]), 40), domain, font=sans_label, fill=MUTED)

    # Bottom double rule + tagline.
    draw.line([(70, H - 95), (W - 70, H - 95)], fill=INK, width=1)
    draw.line([(70, H - 85), (W - 70, H - 85)], fill=INK, width=2)

    footer = "A DAILY DIGEST  ·  CLAUDE READS THE NEWS"
    bbox = draw.textbbox((0, 0), footer, font=sans_label)
    draw.text(
        ((W - (bbox[2] - bbox[0])) // 2, H - 55),
        footer,
        font=sans_label,
        fill=ACCENT,
    )

    return draw


def build_base() -> Path:
    """Per-post template. Masthead at the top with the wordmark in small
    caps; the large center band is left intentionally empty for Hugo's
    images.Text filter to draw the post title into."""
    img = Image.new("RGB", (W, H), BG)
    draw = _broadsheet_chrome(img)

    # Small caps wordmark in the masthead area (above the empty title band).
    masthead = "CLAUDE'S DAILY DIGEST"
    masthead_font = _font(SANS, 28)
    draw.text(
        (_center_x(draw, masthead, masthead_font), 130),
        masthead,
        font=masthead_font,
        fill=INK,
    )

    # Short accent line under the wordmark.
    draw.line([(W // 2 - 70, 180), (W // 2 + 70, 180)], fill=ACCENT, width=3)

    out = REPO / "assets" / "og" / "base.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, "PNG", optimize=True)
    return out


def build_home() -> Path:
    """Home/list share card. Large centered wordmark + tagline."""
    img = Image.new("RGB", (W, H), BG)
    draw = _broadsheet_chrome(img)

    # Large serif wordmark — two lines so it fits at this size without
    # crowding the rules.
    line1 = "Claude's"
    line2 = "Daily Digest"
    serif_lg = _font(SERIF, 138)

    y1 = 165
    draw.text((_center_x(draw, line1, serif_lg), y1), line1, font=serif_lg, fill=INK)
    bbox = draw.textbbox((0, 0), line1, font=serif_lg)
    y2 = y1 + (bbox[3] - bbox[1]) + 6
    draw.text((_center_x(draw, line2, serif_lg), y2), line2, font=serif_lg, fill=INK)

    # Accent rule under the wordmark.
    bbox2 = draw.textbbox((0, 0), line2, font=serif_lg)
    h2 = bbox2[3] - bbox2[1]
    rule_y = y2 + h2 + 28
    draw.line([(W // 2 - 90, rule_y), (W // 2 + 90, rule_y)], fill=ACCENT, width=3)

    # Tagline below.
    tagline = "An AI reads the news every morning and writes about what caught its attention."
    tag_font = _font(SERIF, 28)
    draw.text(
        (_center_x(draw, tagline, tag_font), rule_y + 22),
        tagline,
        font=tag_font,
        fill=INK,
    )

    out = REPO / "static" / "og-image.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, "PNG", optimize=True)
    return out


def main() -> int:
    if not SERIF.exists():
        print(f"missing serif font: {SERIF}", file=sys.stderr)
        return 1
    if not SANS.exists():
        print(f"missing sans font: {SANS}", file=sys.stderr)
        return 1

    base = build_base()
    home = build_home()
    print(f"wrote {base}")
    print(f"wrote {home}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
