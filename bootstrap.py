"""Bootstrap CLI entry used by the frozen GUI for doctor/setup.

When packaged, MacroReview.exe is not a Python interpreter. The GUI launches
itself with `--bootstrap doctor|setup …` so setup can run in-process against
pipeline modules shipped beside the executable.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import config
from hardware import doctor_payload, format_report, probe_system, recommend
from paths import pipeline_root
from progress import make_reporter, use_reporter
from setup_env import build_plan, run_setup


def _ensure_path() -> Path:
    root = pipeline_root()
    root_s = str(root)
    if root_s not in sys.path:
        sys.path.insert(0, root_s)
    return root


def cmd_doctor(args: argparse.Namespace) -> int:
    _ensure_path()

    if getattr(args, "pipeline", False):
        pipeline = (config.PIPELINE_PYTHON or "").strip()
        if not pipeline:
            print("pipeline_python is not set. Run setup first.", file=sys.stderr)
            return 1
        exe = Path(pipeline)
        if not exe.is_file():
            print(f"pipeline_python missing: {exe}", file=sys.stderr)
            return 1
        cmd = [str(exe), str(config.CODE_ROOT / "main.py"), "doctor"]
        if args.json:
            cmd.append("--json")
        if args.strict:
            cmd.append("--strict")
        return int(subprocess.call(cmd, cwd=str(config.CODE_ROOT)))

    with use_reporter(make_reporter("human")):
        profile = probe_system()
        rec = recommend(profile)
        if args.json:
            print(json.dumps(doctor_payload(profile), indent=2))
        else:
            print(format_report(profile, rec), end="")
        if args.strict and not rec.ready_for_pipeline:
            return 1
    return 0


def cmd_setup(args: argparse.Namespace) -> int:
    _ensure_path()

    def _confirm(prompt: str) -> bool:
        if not sys.stdin.isatty():
            print("Non-interactive terminal: pass --yes to proceed.")
            return False
        try:
            answer = input(prompt)
        except EOFError:
            return False
        return answer.strip().lower() in {"y", "yes"}

    mode = "jsonl" if args.progress == "jsonl" else "human"
    with use_reporter(make_reporter(mode)):
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
            return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="MacroReview --bootstrap")
    sub = parser.add_subparsers(dest="command", required=True)

    doctor = sub.add_parser("doctor")
    doctor.add_argument("--json", action="store_true")
    doctor.add_argument("--strict", action="store_true")
    doctor.add_argument("--pipeline", action="store_true")
    doctor.set_defaults(func=cmd_doctor)

    setup = sub.add_parser("setup")
    setup.add_argument("--yes", action="store_true")
    setup.add_argument("--dry-run", action="store_true")
    setup.add_argument("--skip-ollama", action="store_true")
    setup.add_argument("--skip-model", action="store_true")
    setup.add_argument("--force-recreate-venv", action="store_true")
    setup.add_argument("--progress", choices=("human", "jsonl"), default="jsonl")
    setup.set_defaults(func=cmd_setup)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
