"""Shared visual constants for the Results gallery.

Kept muted/desaturated so the "traffic light" recommendation cues read as
purposeful data, not decoration, in line with the app's charcoal/warm/teal
palette.
"""

from __future__ import annotations

REC_COLORS: dict[str, str] = {
    "portfolio": "#c9a227",  # muted gold — top tier
    "share": "#4f8f6d",  # muted green — good to go
    "maybe": "#c98a3d",  # muted amber — borderline
    "skip": "#8a8378",  # neutral slate — de-emphasized
}
DEFAULT_REC_COLOR = "#6b6459"

CROP_COLOR = "#f59e0b"  # matches the HTML report's --crop accent
CROP_MASK_ALPHA = 110


def rec_color(rec: str | None) -> str:
    return REC_COLORS.get((rec or "").strip().lower(), DEFAULT_REC_COLOR)
