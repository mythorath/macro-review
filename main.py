"""CLI entry point for the macro photo review pipeline."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import config
from analyze import analyze_images
from crop_export import export_crops
from db import init_db
from dedupe import compute_dedupe
from heuristics import compute_heuristics
from indexer import index_images
from iqa import compute_iqa
from previews import build_previews
from rank import compute_rankings
from report import build_report
from roi_sharpness import compute_roi


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


def cmd_index(args: argparse.Namespace) -> None:
    index_images(source_dirs=_sources_from_args(args))


def cmd_preview(args: argparse.Namespace) -> None:
    build_previews(force=args.force, limit=args.limit, path_dirs=_dirs_from_args(args))


def cmd_heuristics(args: argparse.Namespace) -> None:
    compute_heuristics(force=args.force, limit=args.limit, path_dirs=_dirs_from_args(args))


def cmd_iqa(args: argparse.Namespace) -> None:
    compute_iqa(force=args.force, limit=args.limit, path_dirs=_dirs_from_args(args))


def cmd_analyze(args: argparse.Namespace) -> None:
    analyze_images(
        limit=args.limit,
        backend_name=args.backend,
        force=args.force,
        path_dirs=_dirs_from_args(args),
    )


def cmd_roi(args: argparse.Namespace) -> None:
    compute_roi(force=args.force, limit=args.limit, path_dirs=_dirs_from_args(args))


def cmd_dedupe(args: argparse.Namespace) -> None:
    compute_dedupe(force=args.force, limit=args.limit, path_dirs=_dirs_from_args(args))


def cmd_rank(args: argparse.Namespace) -> None:
    compute_rankings(force=args.force, limit=args.limit, path_dirs=_dirs_from_args(args))


def cmd_report(args: argparse.Namespace) -> None:
    build_report(path_dirs=_dirs_from_args(args))


def cmd_crop_export(args: argparse.Namespace) -> None:
    export_crops(
        score_threshold=args.threshold,
        limit=args.limit,
        path_dirs=_dirs_from_args(args),
    )


def cmd_run(args: argparse.Namespace) -> None:
    """Full pipeline: index → preview → heuristics → iqa → analyze → roi → dedupe → rank → report → crop."""
    init_db()
    config.ensure_dirs()
    path_dirs = _dirs_from_args(args)
    sources = _sources_from_args(args)
    stage_force = args.force or (args.limit is not None)
    if path_dirs:
        print("Scoped to:")
        for d in path_dirs:
            print(f"  {d}")
    print("== index ==")
    index_images(source_dirs=sources)
    print("== preview ==")
    build_previews(force=args.force, limit=args.limit, path_dirs=path_dirs)
    print("== heuristics ==")
    compute_heuristics(force=stage_force, limit=args.limit, path_dirs=path_dirs)
    print("== iqa ==")
    compute_iqa(force=stage_force, limit=args.limit, path_dirs=path_dirs)
    print("== analyze ==")
    analyze_images(
        limit=args.limit,
        backend_name=args.backend,
        force=stage_force,
        path_dirs=path_dirs,
    )
    print("== roi ==")
    compute_roi(force=stage_force, limit=args.limit, path_dirs=path_dirs)
    print("== dedupe ==")
    compute_dedupe(force=True, limit=args.limit, path_dirs=path_dirs)
    print("== rank ==")
    compute_rankings(force=True, path_dirs=path_dirs)
    print("== report ==")
    build_report(path_dirs=path_dirs)
    print("== crop-export ==")
    export_crops(
        score_threshold=args.threshold,
        limit=args.crop_limit,
        path_dirs=path_dirs,
    )
    print("Done.")
    print(f"Open gallery: {config.REPORT_PATH}")


def _add_dir_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--dir",
        action="append",
        metavar="PATH",
        help="Limit to this folder (repeatable).",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Macro photo review pipeline (IQA ensemble + ROI + Ollama VLM).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_index = sub.add_parser("index", help="Scan source folders into SQLite")
    _add_dir_arg(p_index)
    p_index.set_defaults(func=cmd_index)

    p_preview = sub.add_parser("preview", help="Generate cached preview JPEGs")
    _add_dir_arg(p_preview)
    p_preview.add_argument("--force", action="store_true")
    p_preview.add_argument("--limit", type=int, default=None)
    p_preview.set_defaults(func=cmd_preview)

    p_heur = sub.add_parser("heuristics", help="Local sharpness/exposure metrics")
    _add_dir_arg(p_heur)
    p_heur.add_argument("--force", action="store_true")
    p_heur.add_argument("--limit", type=int, default=None)
    p_heur.set_defaults(func=cmd_heuristics)

    p_iqa = sub.add_parser("iqa", help="IQA ensemble (TOPIQ, CLIP-IQA+, LAION, Q-ReAlign, …)")
    _add_dir_arg(p_iqa)
    p_iqa.add_argument("--force", action="store_true")
    p_iqa.add_argument("--limit", type=int, default=None)
    p_iqa.set_defaults(func=cmd_iqa)

    p_analyze = sub.add_parser("analyze", help="Score images with vision model")
    _add_dir_arg(p_analyze)
    p_analyze.add_argument("--limit", type=int, default=None)
    p_analyze.add_argument("--backend", choices=("ollama", "openai"), default=None)
    p_analyze.add_argument("--force", action="store_true")
    p_analyze.set_defaults(func=cmd_analyze)

    p_roi = sub.add_parser("roi", help="Full-res eye/subject ROI sharpness + composition")
    _add_dir_arg(p_roi)
    p_roi.add_argument("--force", action="store_true")
    p_roi.add_argument("--limit", type=int, default=None)
    p_roi.set_defaults(func=cmd_roi)

    p_dedupe = sub.add_parser("dedupe", help="Burst/near-duplicate grouping via pHash")
    _add_dir_arg(p_dedupe)
    p_dedupe.add_argument("--force", action="store_true")
    p_dedupe.add_argument("--limit", type=int, default=None)
    p_dedupe.set_defaults(func=cmd_dedupe)

    p_rank = sub.add_parser("rank", help="Percentile-calibrated share_score v2")
    _add_dir_arg(p_rank)
    p_rank.add_argument("--force", action="store_true")
    p_rank.add_argument("--limit", type=int, default=None)
    p_rank.set_defaults(func=cmd_rank)

    p_report = sub.add_parser("report", help="Build HTML gallery + CSV")
    _add_dir_arg(p_report)
    p_report.set_defaults(func=cmd_report)

    p_crop = sub.add_parser("crop-export", help="Export full-res crops")
    _add_dir_arg(p_crop)
    p_crop.add_argument("--threshold", type=float, default=None)
    p_crop.add_argument("--limit", type=int, default=None)
    p_crop.set_defaults(func=cmd_crop_export)

    p_run = sub.add_parser("run", help="Run full pipeline (resumable)")
    _add_dir_arg(p_run)
    p_run.add_argument("--limit", type=int, default=None)
    p_run.add_argument("--backend", choices=("ollama", "openai"), default=None)
    p_run.add_argument("--force", action="store_true")
    p_run.add_argument("--threshold", type=float, default=None)
    p_run.add_argument("--crop-limit", type=int, default=None)
    p_run.set_defaults(func=cmd_run)

    return parser


def main(argv: list[str] | None = None) -> int:
    config.ensure_dirs()
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
