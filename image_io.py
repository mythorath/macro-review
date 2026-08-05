"""Shared full-resolution image loading."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageOps

import config

try:
    import rawpy
except ImportError:  # pragma: no cover
    rawpy = None  # type: ignore


def load_full_image(path: Path) -> Image.Image:
    """Load a full-resolution RGB image (JPEG/TIFF via Pillow, RAW via rawpy)."""
    ext = path.suffix.lower()
    if ext in config.RAW_EXTENSIONS:
        if rawpy is None:
            raise RuntimeError("rawpy is required to decode RAW files")
        with rawpy.imread(str(path)) as raw:
            rgb = raw.postprocess(use_camera_wb=True, no_auto_bright=True, output_bps=8)
            return Image.fromarray(rgb)
    with Image.open(path) as img:
        img = ImageOps.exif_transpose(img)
        return img.convert("RGB").copy()


def box_to_pixels(box: list[float], width: int, height: int) -> tuple[int, int, int, int]:
    """Convert [x0,y0,x1,y1] fractions to inclusive-exclusive pixel coords."""
    x0, y0, x1, y1 = box
    left = max(0, min(width - 1, int(round(x0 * width))))
    top = max(0, min(height - 1, int(round(y0 * height))))
    right = max(left + 1, min(width, int(round(x1 * width))))
    bottom = max(top + 1, min(height, int(round(y1 * height))))
    return left, top, right, bottom
