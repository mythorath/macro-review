"""QApplication bootstrap for Macro Review GUI."""

from __future__ import annotations

import sys

from gui import smoke_env

from bootstrap import main as bootstrap_main
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from gui.main_window import MainWindow
from paths import gui_resource, is_frozen, pipeline_root
from version_info import load_build_info


def _ensure_repo_on_path() -> None:
    root = str(pipeline_root())
    if root not in sys.path:
        sys.path.insert(0, root)


def _load_stylesheet() -> str:
    path = gui_resource("style.qss")
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _print_version() -> int:
    info = load_build_info()
    print(f"Macro Review {info.display}")
    print(f"version={info.version}")
    print(f"channel={info.channel}")
    print(f"commit={info.commit}")
    print(f"built_at={info.built_at}")
    print(f"repo={info.repo}")
    print(f"frozen={is_frozen()}")
    print(f"pipeline_root={pipeline_root()}")
    return 0


def _smoke_test() -> int:
    """Headless construction check for CI / packaged builds."""
    _ensure_repo_on_path()
    if smoke_env.SMOKE_ROOT is None:
        print("SMOKE FAIL: isolated environment was not prepared")
        return 1
    info = load_build_info()
    pipeline = pipeline_root()
    required = [
        pipeline / "main.py",
        pipeline / "requirements.txt",
        gui_resource("style.qss"),
    ]
    missing = [str(p) for p in required if not p.is_file()]
    if missing:
        print("SMOKE FAIL: missing resources:")
        for item in missing:
            print(f"  {item}")
        return 1

    app = QApplication(sys.argv)
    app.setApplicationName("Macro Review")
    app.setOrganizationName("MacroReview")
    app.setApplicationVersion(info.version)
    style = _load_stylesheet()
    if style:
        app.setStyleSheet(style)
    window = MainWindow(skip_startup_doctor=True)
    window.hide()
    app.processEvents()
    print(f"SMOKE OK version={info.version} pipeline={pipeline}")
    return 0


def run(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)

    if "--bootstrap" in args:
        idx = args.index("--bootstrap")
        bootstrap_args = args[idx + 1 :]
        return bootstrap_main(bootstrap_args)

    if "--version" in args or "-V" in args:
        return _print_version()

    if "--smoke-test" in args:
        return _smoke_test()

    _ensure_repo_on_path()
    info = load_build_info()
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    app.setApplicationName("Macro Review")
    app.setOrganizationName("MacroReview")
    app.setApplicationVersion(info.version)
    style = _load_stylesheet()
    if style:
        app.setStyleSheet(style)
    window = MainWindow()
    window.showMaximized()
    return app.exec()
