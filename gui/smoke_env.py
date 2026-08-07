"""Prepare isolated process state before GUI modules load in smoke mode."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

SMOKE_ROOT: Path | None = None

if "--smoke-test" in sys.argv:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    SMOKE_ROOT = Path(tempfile.mkdtemp(prefix="macroreview_smoke_"))
    os.environ["LOCALAPPDATA"] = str(SMOKE_ROOT)
    os.environ.pop("MACROREVIEW_SETTINGS", None)
