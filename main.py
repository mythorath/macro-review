"""CLI entry point for the macro photo review pipeline."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import config
from analyze import analyze_images
from crop_export import export_crops
from db import init_db
from dedupe import compute_dedupe
from hardware import doctor_payload, format_report, probe_system, recommend
from heuristics import compute_heuristics
from indexer import index_images
from iqa import compute_iqa
from previews import build_previews
from progress import get_reporter, make_reporter, use_reporter
from rank import compute_rankings
from report import build_report
from roi_sharpness import compute_roi
from settings import (
    LibraryEntry,
    default_settings_path,
    init_settings,
    load_settings,
    save_settings,
)
from setup_env import build_plan, run_setup



RUN_STAGES = [
    "index",
    "preview",
    "heuristics",
    "iqa",
    "analyze",
    "roi",
    "dedupe",
    "rank",
    "report",
    "crop-export",
]


def _dirs_from_args(args: argparse.Namespace) -> list[Path] | None:
    raw = getattr(args, "dir", None) or None
    if not raw:
        return None
    return [Path(d).expanduser() for d in raw]


def _sources_from_args(args: argparse.Namespace) -> list[tuple[str, Path]] | None:
    dirs = _dirs_from_args(args)
    if not dirs:
        return None
    return config.resolve_source_dirs(dirs)


def _recursive_from_args(args: argparse.Namespace) -> bool:
    """--dir uses settings.recursive_default unless --recursive is passed."""
    if _dirs_from_args(args):
        if bool(getattr(args, "recursive", False)):
            return True
        return bool(config.RECURSIVE_DEFAULT)
    return True


def _progress_mode(args: argparse.Namespace) -> str:
    explicit = getattr(args, "progress", None)
    if explicit:
        return str(explicit).strip().lower()
    env = os.environ.get("MACROREVIEW_PROGRESS", "").strip().lower()
    if env in ("human", "jsonl"):
        return env
    return "human"


def cmd_index(args: argparse.Namespace) -> None:
    index_images(
        source_dirs=_sources_from_args(args),
        recursive=_recursive_from_args(args),
    )


def cmd_preview(args: argparse.Namespace) -> None:
    build_previews(
        force=args.force,
        limit=args.limit,
        path_dirs=_dirs_from_args(args),
        recursive=_recursive_from_args(args),
    )


def cmd_heuristics(args: argparse.Namespace) -> None:
    compute_heuristics(
        force=args.force,
        limit=args.limit,
        path_dirs=_dirs_from_args(args),
        recursive=_recursive_from_args(args),
    )


def cmd_iqa(args: argparse.Namespace) -> None:
    compute_iqa(
        force=args.force,
        limit=args.limit,
        path_dirs=_dirs_from_args(args),
        recursive=_recursive_from_args(args),
    )


def cmd_analyze(args: argparse.Namespace) -> None:
    analyze_images(
        limit=args.limit,
        backend_name=args.backend,
        force=args.force,
        path_dirs=_dirs_from_args(args),
        recursive=_recursive_from_args(args),
    )


def cmd_roi(args: argparse.Namespace) -> None:
    compute_roi(
        force=args.force,
        limit=args.limit,
        path_dirs=_dirs_from_args(args),
        recursive=_recursive_from_args(args),
    )


def cmd_dedupe(args: argparse.Namespace) -> None:
    compute_dedupe(
        force=args.force,
        limit=args.limit,
        path_dirs=_dirs_from_args(args),
        recursive=_recursive_from_args(args),
    )


def cmd_rank(args: argparse.Namespace) -> None:
    compute_rankings(
        force=args.force,
        limit=args.limit,
        path_dirs=_dirs_from_args(args),
        recursive=_recursive_from_args(args),
    )


def cmd_report(args: argparse.Namespace) -> None:
    build_report(
        path_dirs=_dirs_from_args(args),
        recursive=_recursive_from_args(args),
    )


def cmd_crop_export(args: argparse.Namespace) -> None:
    export_crops(
        score_threshold=args.threshold,
        limit=args.limit,
        path_dirs=_dirs_from_args(args),
        recursive=_recursive_from_args(args),
    )


def cmd_run(args: argparse.Namespace) -> None:
    """Full pipeline: index → preview → heuristics → iqa → analyze → roi → dedupe → rank → report → crop."""
    reporter = get_reporter()
    init_db()
    config.ensure_dirs()
    path_dirs = _dirs_from_args(args)
    sources = _sources_from_args(args)
    recursive = _recursive_from_args(args)
    stage_force = args.force or (args.limit is not None)
    reporter.run_start(RUN_STAGES)
    if path_dirs:
        mode = "recursive" if recursive else "direct files only"
        reporter.log("run", f"Scoped to ({mode}):")
        for d in path_dirs:
            reporter.log("run", f"  {d}")
    index_images(source_dirs=sources, recursive=recursive)
    build_previews(
        force=args.force,
        limit=args.limit,
        path_dirs=path_dirs,
        recursive=recursive,
    )
    compute_heuristics(
        force=stage_force,
        limit=args.limit,
        path_dirs=path_dirs,
        recursive=recursive,
    )
    compute_iqa(
        force=stage_force,
        limit=args.limit,
        path_dirs=path_dirs,
        recursive=recursive,
    )
    analyze_images(
        limit=args.limit,
        backend_name=args.backend,
        force=stage_force,
        path_dirs=path_dirs,
        recursive=recursive,
    )
    compute_roi(
        force=stage_force,
        limit=args.limit,
        path_dirs=path_dirs,
        recursive=recursive,
    )
    compute_dedupe(
        force=True,
        limit=args.limit,
        path_dirs=path_dirs,
        recursive=recursive,
    )
    compute_rankings(force=True, path_dirs=path_dirs, recursive=recursive)
    build_report(path_dirs=path_dirs, recursive=recursive)
    export_crops(
        score_threshold=args.threshold,
        limit=args.crop_limit,
        path_dirs=path_dirs,
        recursive=recursive,
    )
    reporter.run_done(ok=True, message=f"Done. Open gallery: {config.REPORT_PATH}")


def cmd_settings_show(_args: argparse.Namespace) -> None:
    path = default_settings_path()
    exists = path.is_file()
    settings = config.active_settings()
    print(f"settings_file: {path}")
    print(f"settings_exists: {exists}")
    print(f"code_root: {config.CODE_ROOT}")
    print(f"data_dir: {config.DATA_DIR}")
    print(f"db_path: {config.DB_PATH}")
    print(f"preview_dir: {config.PREVIEW_DIR}")
    print(f"report_path: {config.REPORT_PATH}")
    print(f"crop_dir: {config.CROP_DIR}")
    print(f"log_dir: {config.LOG_DIR}")
    print(f"backend: {config.BACKEND}")
    print(f"ollama_host: {config.OLLAMA_HOST}")
    print(f"vision_model: {config.VISION_MODEL}")
    print(f"iqa_device: {config.IQA_DEVICE}")
    print(f"qrealign_variant: {config.QREALIGN_VARIANT}")
    print(f"recursive_default: {config.RECURSIVE_DEFAULT}")
    print(f"pipeline_python: {config.PIPELINE_PYTHON or '(current interpreter)'}")
    print("libraries:")
    if not settings.libraries:
        print("  (none)")
    for lib in settings.libraries:
        print(f"  - {lib.name}: {lib.path}")


def cmd_settings_init(args: argparse.Namespace) -> None:
    try:
        path, _created = init_settings(force=args.force)
    except FileExistsError as exc:
        print(str(exc))
        print("Pass --force to overwrite.")
        raise SystemExit(1) from exc
    config.reload()
    print(f"Wrote settings: {path}")


def cmd_settings_set_data_dir(args: argparse.Namespace) -> None:
    path = default_settings_path()
    settings = load_settings(path if path.is_file() else None)
    settings.data_dir = str(Path(args.path).expanduser())
    save_settings(settings, path)
    config.reload()
    print(f"data_dir set to {config.DATA_DIR}")
    print(f"settings: {path}")


def cmd_settings_add_library(args: argparse.Namespace) -> None:
    path = default_settings_path()
    settings = load_settings(path if path.is_file() else None)
    root = Path(args.path).expanduser()
    try:
        root = root.resolve()
    except OSError:
        root = root.absolute()
    name = args.name or root.name or "library"
    # Replace existing same path / name
    settings.libraries = [
        lib
        for lib in settings.libraries
        if lib.name != name and Path(lib.path) != root
    ]
    settings.libraries.append(LibraryEntry(name=name, path=str(root)))
    save_settings(settings, path)
    config.reload()
    print(f"Added library {name}: {root}")
    print(f"settings: {path}")


def cmd_doctor(args: argparse.Namespace) -> None:
    if getattr(args, "pipeline", False):
        pipeline = (config.PIPELINE_PYTHON or "").strip()
        if not pipeline:
            print("pipeline_python is not set. Run: python main.py setup --yes")
            raise SystemExit(1)
        exe = Path(pipeline)
        if not exe.is_file():
            print(f"pipeline_python missing: {exe}")
            raise SystemExit(1)
        # Avoid recursive --pipeline when re-entering via managed python.
        cmd = [str(exe), str(config.CODE_ROOT / "main.py"), "doctor"]
        if args.json:
            cmd.append("--json")
        if args.strict:
            cmd.append("--strict")
        raise SystemExit(subprocess.call(cmd, cwd=str(config.CODE_ROOT)))

    profile = probe_system()
    rec = recommend(profile)
    if args.json:
        print(json.dumps(doctor_payload(profile), indent=2))
    else:
        print(format_report(profile, rec), end="")
    if args.strict and not rec.ready_for_pipeline:
        raise SystemExit(1)


def cmd_setup(args: argparse.Namespace) -> None:
    def _confirm(prompt: str) -> bool:
        if not sys.stdin.isatty():
            print("Non-interactive terminal: pass --yes to proceed.")
            return False
        try:
            answer = input(prompt)
        except EOFError:
            return False
        return answer.strip().lower() in {"y", "yes"}

    if args.dry_run:
        plan, _rec = build_plan(
            skip_ollama=args.skip_ollama,
            skip_model=args.skip_model,
            force_recreate_venv=args.force_recreate_venv,
        )
        print("Setup plan (dry-run):")
        for line in plan.summary_lines():
            print(f"  {line}")

    try:
        run_setup(
            yes=args.yes or args.dry_run,
            dry_run=args.dry_run,
            skip_ollama=args.skip_ollama,
            skip_model=args.skip_model,
            force_recreate_venv=args.force_recreate_venv,
            confirm=_confirm,
        )
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


def _add_dir_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--dir",
        action="append",
        metavar="PATH",
        help="Limit to this folder (repeatable). Direct files only unless --recursive.",
    )
    parser.add_argument(
        "--recursive",
        action="store_true",
        help="With --dir, include images in subfolders (default: settings.recursive_default).",
    )


def _add_progress_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--progress",
        choices=("human", "jsonl"),
        default=None,
        help="Progress output mode (default: human, or MACROREVIEW_PROGRESS env).",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Macro photo review pipeline (IQA ensemble + ROI + Ollama VLM).",
    )
    _add_progress_arg(parser)
    sub = parser.add_subparsers(dest="command", required=True)

    p_index = sub.add_parser("index", help="Scan source folders into SQLite")
    _add_dir_arg(p_index)
    _add_progress_arg(p_index)
    p_index.set_defaults(func=cmd_index)

    p_preview = sub.add_parser("preview", help="Generate cached preview JPEGs")
    _add_dir_arg(p_preview)
    _add_progress_arg(p_preview)
    p_preview.add_argument("--force", action="store_true")
    p_preview.add_argument("--limit", type=int, default=None)
    p_preview.set_defaults(func=cmd_preview)

    p_heur = sub.add_parser("heuristics", help="Local sharpness/exposure metrics")
    _add_dir_arg(p_heur)
    _add_progress_arg(p_heur)
    p_heur.add_argument("--force", action="store_true")
    p_heur.add_argument("--limit", type=int, default=None)
    p_heur.set_defaults(func=cmd_heuristics)

    p_iqa = sub.add_parser("iqa", help="IQA ensemble (TOPIQ, CLIP-IQA+, LAION, Q-ReAlign, …)")
    _add_dir_arg(p_iqa)
    _add_progress_arg(p_iqa)
    p_iqa.add_argument("--force", action="store_true")
    p_iqa.add_argument("--limit", type=int, default=None)
    p_iqa.set_defaults(func=cmd_iqa)

    p_analyze = sub.add_parser("analyze", help="Score images with vision model")
    _add_dir_arg(p_analyze)
    _add_progress_arg(p_analyze)
    p_analyze.add_argument("--limit", type=int, default=None)
    p_analyze.add_argument("--backend", choices=("ollama", "openai"), default=None)
    p_analyze.add_argument("--force", action="store_true")
    p_analyze.set_defaults(func=cmd_analyze)

    p_roi = sub.add_parser("roi", help="Full-res eye/subject ROI sharpness + composition")
    _add_dir_arg(p_roi)
    _add_progress_arg(p_roi)
    p_roi.add_argument("--force", action="store_true")
    p_roi.add_argument("--limit", type=int, default=None)
    p_roi.set_defaults(func=cmd_roi)

    p_dedupe = sub.add_parser("dedupe", help="Burst/near-duplicate grouping via pHash")
    _add_dir_arg(p_dedupe)
    _add_progress_arg(p_dedupe)
    p_dedupe.add_argument("--force", action="store_true")
    p_dedupe.add_argument("--limit", type=int, default=None)
    p_dedupe.set_defaults(func=cmd_dedupe)

    p_rank = sub.add_parser("rank", help="Percentile-calibrated share_score v2")
    _add_dir_arg(p_rank)
    _add_progress_arg(p_rank)
    p_rank.add_argument("--force", action="store_true")
    p_rank.add_argument("--limit", type=int, default=None)
    p_rank.set_defaults(func=cmd_rank)

    p_report = sub.add_parser("report", help="Build HTML gallery + CSV")
    _add_dir_arg(p_report)
    _add_progress_arg(p_report)
    p_report.set_defaults(func=cmd_report)

    p_crop = sub.add_parser("crop-export", help="Export full-res crops")
    _add_dir_arg(p_crop)
    _add_progress_arg(p_crop)
    p_crop.add_argument("--threshold", type=float, default=None)
    p_crop.add_argument("--limit", type=int, default=None)
    p_crop.set_defaults(func=cmd_crop_export)

    p_run = sub.add_parser("run", help="Run full pipeline (resumable)")
    _add_dir_arg(p_run)
    _add_progress_arg(p_run)
    p_run.add_argument("--limit", type=int, default=None)
    p_run.add_argument("--backend", choices=("ollama", "openai"), default=None)
    p_run.add_argument("--force", action="store_true")
    p_run.add_argument("--threshold", type=float, default=None)
    p_run.add_argument("--crop-limit", type=int, default=None)
    p_run.set_defaults(func=cmd_run)

    p_settings = sub.add_parser("settings", help="Show or write user settings")
    settings_sub = p_settings.add_subparsers(dest="settings_command", required=True)

    p_show = settings_sub.add_parser("show", help="Print resolved settings and paths")
    p_show.set_defaults(func=cmd_settings_show)

    p_init = settings_sub.add_parser("init", help="Write default settings.json if missing")
    p_init.add_argument("--force", action="store_true", help="Overwrite existing settings")
    p_init.set_defaults(func=cmd_settings_init)

    p_data = settings_sub.add_parser("set-data-dir", help="Set workspace data_dir")
    p_data.add_argument("path", help="Directory for cache, report, crops, logs")
    p_data.set_defaults(func=cmd_settings_set_data_dir)

    p_add = settings_sub.add_parser("add-library", help="Add a photo library folder")
    p_add.add_argument("path", help="Library folder path")
    p_add.add_argument("--name", default=None, help="Library label (default: folder name)")
    p_add.set_defaults(func=cmd_settings_add_library)

    p_doctor = sub.add_parser(
        "doctor",
        help="Probe GPU/Python/Ollama and print setup recommendations",
    )
    _add_progress_arg(p_doctor)
    p_doctor.add_argument(
        "--json",
        action="store_true",
        help="Emit one JSON object {profile, recommendations} on stdout",
    )
    p_doctor.add_argument(
        "--strict",
        action="store_true",
        help="Exit 1 if ready_for_pipeline is false",
    )
    p_doctor.add_argument(
        "--pipeline",
        action="store_true",
        help="Run doctor using settings.pipeline_python (managed venv)",
    )
    p_doctor.set_defaults(func=cmd_doctor)

    p_setup = sub.add_parser(
        "setup",
        help="Create managed venv, install deps, ensure Ollama/model",
    )
    _add_progress_arg(p_setup)
    p_setup.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Do not prompt for confirmation",
    )
    p_setup.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned actions without changing the machine",
    )
    p_setup.add_argument(
        "--skip-ollama",
        action="store_true",
        help="Skip Ollama install / HTTP ensure",
    )
    p_setup.add_argument(
        "--skip-model",
        action="store_true",
        help="Skip ollama pull of recommended vision model",
    )
    p_setup.add_argument(
        "--force-recreate-venv",
        action="store_true",
        help="Delete and recreate the managed venv",
    )
    p_setup.set_defaults(func=cmd_setup)

    return parser


def main(argv: list[str] | None = None) -> int:
    config.reload()
    parser = build_parser()
    args = parser.parse_args(argv)
    command = getattr(args, "command", None)
    if command in ("doctor", "setup"):
        try:
            config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
    else:
        config.ensure_dirs()
    mode = _progress_mode(args)
    with use_reporter(make_reporter(mode)):
        args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
