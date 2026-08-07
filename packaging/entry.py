"""PyInstaller entry point for MacroReview.exe."""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure repo/pipeline root is importable when frozen datas land in _MEIPASS.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from gui.app import run


if __name__ == "__main__":
    raise SystemExit(run())
