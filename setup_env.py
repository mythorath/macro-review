"""Managed venv + package/Ollama install orchestration (Phase 2 setup)."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.request import urlopen

import requests

import config
from hardware import Recommendations, find_ollama_binary, probe_system, recommend
from paths import pipeline_root, requirements_path as resolve_requirements_path
from progress import get_reporter
from python_discover import PythonCandidate, probe_python, require_base_python
from settings import (
    app_data_root,
    default_data_dir_for_new_install,
    default_settings_path,
    load_settings,
    save_settings,
)

OLLAMA_SETUP_URL = "https://ollama.com/download/OllamaSetup.exe"
OLLAMA_WAIT_SEC = 600
SKIP_REQ_NAMES = {"torch", "torchvision"}


@dataclass
class SetupPlan:
    venv_dir: Path
    python_exe: Path
    base_python: str
    torch_pip_args: list[str]
    iqa_device: str
    vision_model: str
    qrealign_variant: str
    backend: str
    ollama_binary: str | None
    ollama_http_ok: bool
    need_ollama_install: bool
    need_model_pull: bool
    force_recreate_venv: bool

    def summary_lines(self) -> list[str]:
        lines = [
            f"venv: {self.venv_dir}",
            f"python: {self.python_exe}",
            f"base_python: {self.base_python}",
            f"torch: {' '.join(self.torch_pip_args)}",
            f"iqa_device: {self.iqa_device}",
            f"qrealign_variant: {self.qrealign_variant}",
            f"backend: {self.backend}",
            f"vision_model: {self.vision_model}",
            f"ollama_binary: {self.ollama_binary or '(missing)'}",
            f"ollama_http_ok: {self.ollama_http_ok}",
            f"will_install_ollama: {self.need_ollama_install}",
            f"will_pull_model: {self.need_model_pull}",
            f"force_recreate_venv: {self.force_recreate_venv}",
        ]
        return lines


def managed_venv_dir() -> Path:
    return app_data_root() / "venv"


def managed_python(venv_dir: Path | None = None) -> Path:
    root = venv_dir or managed_venv_dir()
    if sys.platform == "win32":
        return root / "Scripts" / "python.exe"
    return root / "bin" / "python"


def download_cache_dir() -> Path:
    path = app_data_root() / "cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def code_root() -> Path:
    return pipeline_root()


def requirements_path() -> Path:
    return resolve_requirements_path()


def resolve_base_python(settings_hint: str | None = None) -> PythonCandidate:
    """Prefer a persisted base_python when still valid; otherwise rediscover."""
    hint = (settings_hint or "").strip()
    if hint:
        probed = probe_python(hint)
        if probed is not None and probed.is_usable():
            return probed
    return require_base_python()


def _stream_cmd(
    args: list[str],
    *,
    stage: str,
    timeout: float | None = None,
    env: dict[str, str] | None = None,
) -> int:
    reporter = get_reporter()
    reporter.log(stage, "$ " + " ".join(args))
    try:
        proc = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
        )
    except OSError as exc:
        reporter.error(stage, str(exc))
        return 1
    assert proc.stdout is not None
    start = time.time()
    for line in proc.stdout:
        reporter.log(stage, line.rstrip())
        if timeout is not None and (time.time() - start) > timeout:
            proc.kill()
            reporter.error(stage, f"timed out after {timeout}s")
            return 124
    return int(proc.wait() or 0)


def filtered_requirements_lines(path: Path) -> list[str]:
    lines: list[str] = []
    if not path.is_file():
        return lines
    for raw in path.read_text(encoding="utf-8").splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        name = stripped.split("==")[0].split(">=")[0].split("<=")[0].split("[")[0].strip()
        if name.lower() in SKIP_REQ_NAMES:
            continue
        lines.append(stripped)
    if not any(l.lower().startswith("imagehash") for l in lines):
        lines.append("ImageHash>=4.3.0")
    return lines


def build_plan(
    *,
    skip_ollama: bool = False,
    skip_model: bool = False,
    force_recreate_venv: bool = False,
) -> tuple[SetupPlan, Recommendations]:
    profile = probe_system()
    rec = recommend(profile)
    settings = load_settings()
    base = resolve_base_python(settings.base_python)
    venv_dir = managed_venv_dir()
    py = managed_python(venv_dir)
    binary = find_ollama_binary()
    http_ok = profile.ollama.http_ok
    model = rec.vision_model
    need_install = (not skip_ollama) and (binary is None)
    need_pull = False
    if not skip_ollama and not skip_model:
        if model not in profile.ollama.models:
            need_pull = True
    return (
        SetupPlan(
            venv_dir=venv_dir,
            python_exe=py,
            base_python=str(base.executable),
            torch_pip_args=list(rec.torch_pip_args),
            iqa_device=rec.iqa_device,
            vision_model=model,
            qrealign_variant=rec.qrealign_variant,
            backend=rec.backend,
            ollama_binary=binary,
            ollama_http_ok=http_ok,
            need_ollama_install=need_install,
            need_model_pull=need_pull,
            force_recreate_venv=force_recreate_venv,
        ),
        rec,
    )


def ensure_venv(
    *,
    base_python: str | Path | None = None,
    force_recreate: bool = False,
    dry_run: bool = False,
) -> Path:
    reporter = get_reporter()
    venv_dir = managed_venv_dir()
    py = managed_python(venv_dir)
    reporter.stage_start("setup_venv", message=str(venv_dir))
    if dry_run:
        reporter.log("setup_venv", f"dry-run: would ensure venv at {venv_dir}")
        reporter.stage_done("setup_venv", ok=1, message="dry-run")
        return py

    base = resolve_base_python(str(base_python) if base_python else None)
    if force_recreate and venv_dir.exists():
        reporter.log("setup_venv", f"removing existing venv {venv_dir}")
        shutil.rmtree(venv_dir, ignore_errors=True)

    if not py.is_file():
        reporter.log(
            "setup_venv",
            f"creating venv with {base.executable} ({base.version_str})",
        )
        venv_dir.parent.mkdir(parents=True, exist_ok=True)
        # Always create via an external interpreter — never the frozen exe.
        code = _stream_cmd(
            [str(base.executable), "-m", "venv", str(venv_dir)],
            stage="setup_venv",
            timeout=300,
        )
        if code != 0:
            raise RuntimeError(f"venv create failed with exit {code}")
        if not py.is_file():
            raise RuntimeError(f"venv created but python missing: {py}")

    code = _stream_cmd(
        [str(py), "-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel"],
        stage="setup_venv",
        timeout=600,
    )
    if code != 0:
        raise RuntimeError(f"pip upgrade failed with exit {code}")
    reporter.stage_done("setup_venv", ok=1, message=f"venv ready: {py}")
    return py


def install_python_packages(
    plan: SetupPlan,
    *,
    dry_run: bool = False,
) -> None:
    reporter = get_reporter()
    py = plan.python_exe
    reporter.stage_start("setup_pip", message=str(py))
    if dry_run:
        reporter.log("setup_pip", f"dry-run: pip install {' '.join(plan.torch_pip_args)}")
        reqs = filtered_requirements_lines(requirements_path())
        reporter.log("setup_pip", f"dry-run: then install {len(reqs)} requirements")
        reporter.stage_done("setup_pip", ok=0, message="dry-run")
        return

    torch_cmd = [str(py), "-m", "pip", "install", *plan.torch_pip_args]
    code = _stream_cmd(torch_cmd, stage="setup_pip", timeout=3600)
    if code != 0:
        raise RuntimeError(f"torch install failed with exit {code}")

    req_lines = filtered_requirements_lines(requirements_path())
    if req_lines:
        tmp = download_cache_dir() / "requirements.filtered.txt"
        tmp.write_text("\n".join(req_lines) + "\n", encoding="utf-8")
        code = _stream_cmd(
            [str(py), "-m", "pip", "install", "-r", str(tmp)],
            stage="setup_pip",
            timeout=3600,
        )
        if code != 0:
            raise RuntimeError(f"requirements install failed with exit {code}")
    reporter.stage_done("setup_pip", ok=1, message="packages installed")


def _ollama_http_ok(host: str | None = None) -> bool:
    url = (host or config.OLLAMA_HOST).rstrip("/") + "/api/tags"
    try:
        resp = requests.get(url, timeout=5)
        return bool(resp.ok)
    except requests.RequestException:
        return False


def _download_file(url: str, dest: Path, *, stage: str) -> None:
    reporter = get_reporter()
    dest.parent.mkdir(parents=True, exist_ok=True)
    reporter.log(stage, f"downloading {url} -> {dest}")
    with urlopen(url, timeout=120) as resp:  # noqa: S310 - fixed ollama CDN URL
        total = resp.headers.get("Content-Length")
        total_n = int(total) if total and total.isdigit() else None
        if total_n:
            reporter.stage_start(stage, total=total_n, message="download bytes")
        done = 0
        chunk = 1024 * 256
        with dest.open("wb") as fh:
            while True:
                data = resp.read(chunk)
                if not data:
                    break
                fh.write(data)
                done += len(data)
                if total_n:
                    reporter.item(stage, current=done, total=total_n, status="ok")
    reporter.log(stage, f"downloaded {dest.stat().st_size} bytes")


def ensure_ollama(
    plan: SetupPlan,
    *,
    skip_ollama: bool = False,
    dry_run: bool = False,
) -> str | None:
    reporter = get_reporter()
    reporter.stage_start("setup_ollama")
    if skip_ollama:
        reporter.stage_done("setup_ollama", ok=0, message="skipped")
        return find_ollama_binary()

    binary = find_ollama_binary()
    if dry_run:
        if binary:
            reporter.log("setup_ollama", f"dry-run: found {binary}")
        else:
            reporter.log("setup_ollama", f"dry-run: would download {OLLAMA_SETUP_URL}")
        reporter.stage_done("setup_ollama", ok=0, message="dry-run")
        return binary

    if binary is None:
        installer = download_cache_dir() / "OllamaSetup.exe"
        _download_file(OLLAMA_SETUP_URL, installer, stage="setup_ollama")
        reporter.log(
            "setup_ollama",
            "Launching Ollama installer. Complete UAC/wizard if prompted...",
        )
        subprocess.Popen([str(installer)], shell=False)  # noqa: S603
        deadline = time.time() + OLLAMA_WAIT_SEC
        while time.time() < deadline:
            binary = find_ollama_binary()
            if binary and _ollama_http_ok():
                break
            reporter.log("setup_ollama", "waiting for Ollama binary + HTTP...")
            time.sleep(5)
        if not binary:
            raise RuntimeError(
                "Ollama installer launched but binary not found. "
                "Finish the installer, start Ollama, then re-run setup."
            )

    if not _ollama_http_ok():
        reporter.log("setup_ollama", "Ollama HTTP down; trying 'ollama serve'...")
        try:
            subprocess.Popen(  # noqa: S603
                [binary, "serve"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            reporter.warning("setup_ollama", f"could not start serve: {exc}")
        deadline = time.time() + 120
        while time.time() < deadline and not _ollama_http_ok():
            time.sleep(3)
        if not _ollama_http_ok():
            raise RuntimeError(
                f"Ollama not reachable at {config.OLLAMA_HOST}. "
                "Start the Ollama app and re-run setup."
            )

    reporter.stage_done("setup_ollama", ok=1, message=f"ollama ready: {binary}")
    return binary


def pull_vision_model(
    plan: SetupPlan,
    *,
    skip_model: bool = False,
    dry_run: bool = False,
) -> None:
    reporter = get_reporter()
    reporter.stage_start("setup_model", message=plan.vision_model)
    if skip_model:
        reporter.stage_done("setup_model", ok=0, message="skipped")
        return
    if dry_run:
        reporter.log("setup_model", f"dry-run: ollama pull {plan.vision_model}")
        reporter.stage_done("setup_model", ok=0, message="dry-run")
        return

    binary = find_ollama_binary()
    if not binary:
        raise RuntimeError("ollama binary required for model pull")

    # Refresh tags; skip pull if already present
    try:
        resp = requests.get(f"{config.OLLAMA_HOST.rstrip('/')}/api/tags", timeout=10)
        if resp.ok:
            names = [
                str(m.get("name") or m.get("model") or "")
                for m in (resp.json().get("models") or [])
            ]
            if plan.vision_model in names:
                reporter.stage_done(
                    "setup_model",
                    ok=1,
                    message=f"already present: {plan.vision_model}",
                )
                return
    except requests.RequestException:
        pass

    code = _stream_cmd(
        [binary, "pull", plan.vision_model],
        stage="setup_model",
        timeout=7200,
    )
    if code != 0:
        raise RuntimeError(f"ollama pull failed with exit {code}")
    reporter.stage_done("setup_model", ok=1, message=f"pulled {plan.vision_model}")


def apply_setup_settings(plan: SetupPlan, *, dry_run: bool = False) -> Path:
    reporter = get_reporter()
    reporter.stage_start("setup_settings")
    path = default_settings_path()
    if dry_run:
        reporter.log("setup_settings", f"dry-run: would write {path}")
        reporter.stage_done("setup_settings", ok=0, message="dry-run")
        return path

    settings = load_settings(path if path.is_file() else None)
    settings.backend = plan.backend
    settings.iqa_device = plan.iqa_device
    settings.qrealign_variant = plan.qrealign_variant
    settings.vision_model = plan.vision_model
    settings.pipeline_python = str(plan.python_exe)
    settings.base_python = str(plan.base_python)
    if not (settings.data_dir or "").strip():
        settings.data_dir = str(default_data_dir_for_new_install())
    save_settings(settings, path)
    config.reload()
    reporter.stage_done("setup_settings", ok=1, message=f"wrote {path}")
    return path


def verify_managed_doctor(plan: SetupPlan, *, dry_run: bool = False) -> dict:
    reporter = get_reporter()
    reporter.stage_start("setup_verify")
    if dry_run:
        reporter.log("setup_verify", "dry-run: would run doctor --json in managed python")
        reporter.stage_done("setup_verify", ok=0, message="dry-run")
        return {}

    main_py = code_root() / "main.py"
    if not plan.python_exe.is_file():
        raise RuntimeError(f"managed python missing: {plan.python_exe}")
    proc = subprocess.run(
        [str(plan.python_exe), str(main_py), "doctor", "--json"],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
        cwd=str(code_root()),
    )
    if proc.returncode != 0:
        reporter.log("setup_verify", proc.stdout[-2000:] if proc.stdout else "")
        reporter.log("setup_verify", proc.stderr[-2000:] if proc.stderr else "")
        raise RuntimeError(f"doctor --json failed with exit {proc.returncode}")
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"doctor JSON parse failed: {exc}\n{proc.stdout[:500]}") from exc
    ready = bool(payload.get("recommendations", {}).get("ready_for_pipeline"))
    reporter.log("setup_verify", f"ready_for_pipeline={ready}")
    if not ready:
        checks = payload.get("recommendations", {}).get("checks") or []
        for c in checks:
            if not c.get("ok") and c.get("severity") == "critical":
                reporter.warning("setup_verify", f"{c.get('id')}: {c.get('message')}")
        raise RuntimeError("managed venv doctor reports not ready_for_pipeline")
    reporter.stage_done("setup_verify", ok=1, message="managed doctor OK")
    return payload


def run_setup(
    *,
    yes: bool = False,
    dry_run: bool = False,
    skip_ollama: bool = False,
    skip_model: bool = False,
    force_recreate_venv: bool = False,
    confirm: Callable[[str], bool] | None = None,
) -> SetupPlan:
    """
    Execute full setup. Raises RuntimeError on failure.
    confirm(prompt) -> bool used when yes is False.
    """
    reporter = get_reporter()
    # Validate base Python early (frozen GUI may not be a usable interpreter).
    try:
        require_base_python()
    except RuntimeError:
        raise

    plan, _rec = build_plan(
        skip_ollama=skip_ollama,
        skip_model=skip_model,
        force_recreate_venv=force_recreate_venv,
    )
    # Adjust need flags from CLI
    if skip_ollama:
        plan.need_ollama_install = False
        plan.need_model_pull = False
    if skip_model:
        plan.need_model_pull = False

    reporter.run_start(
        [
            "setup_venv",
            "setup_pip",
            "setup_ollama",
            "setup_model",
            "setup_settings",
            "setup_verify",
        ]
    )
    for line in plan.summary_lines():
        reporter.log("setup", line)

    if not yes:
        fn = confirm or (lambda _p: False)
        if not fn("Proceed with setup? [y/N] "):
            raise RuntimeError("Setup cancelled (pass --yes to skip prompt)")

    ensure_venv(
        base_python=plan.base_python,
        force_recreate=force_recreate_venv,
        dry_run=dry_run,
    )
    # Refresh python path after create
    plan.python_exe = managed_python(plan.venv_dir)
    install_python_packages(plan, dry_run=dry_run)
    ensure_ollama(plan, skip_ollama=skip_ollama, dry_run=dry_run)
    pull_vision_model(plan, skip_model=skip_model or skip_ollama, dry_run=dry_run)
    apply_setup_settings(plan, dry_run=dry_run)
    if not dry_run:
        verify_managed_doctor(plan, dry_run=False)
    else:
        verify_managed_doctor(plan, dry_run=True)
    reporter.run_done(ok=True, message="Setup complete." if not dry_run else "Dry-run complete.")
    return plan
