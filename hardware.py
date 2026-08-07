"""Hardware / environment probing and install recommendations (Phase 1 doctor)."""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import requests

import config
from paths import is_frozen
from python_discover import discover_base_python

# ---------------------------------------------------------------------------
# Recommendation constants (Phase 2/3 reuse these)
# ---------------------------------------------------------------------------

TORCH_CUDA_INDEX_URL = "https://download.pytorch.org/whl/cu128"
VISION_MODEL_LOW = "qwen2.5vl:7b"
VISION_MODEL_MID = "qwen2.5vl:7b"
VISION_MODEL_HIGH = "qwen3.6:35b"
DISK_WARN_FREE_BYTES = 15 * 1024**3  # 15 GiB

Vendor = Literal["nvidia", "amd", "intel", "unknown"]
Severity = Literal["critical", "warning", "info"]


@dataclass
class PythonInfo:
    executable: str
    version: str
    version_tuple: tuple[int, int, int]
    is_64bit: bool
    in_venv: bool

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["version_tuple"] = list(self.version_tuple)
        return d


@dataclass
class GpuDevice:
    vendor: Vendor
    name: str
    vram_mb: int | None
    index: int
    source: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TorchInfo:
    installed: bool
    version: str | None = None
    cuda_available: bool = False
    cuda_version: str | None = None
    device_names: list[str] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class OllamaInfo:
    binary_path: str | None
    version: str | None
    host: str
    http_ok: bool
    models: list[str] = field(default_factory=list)
    vision_model_configured: str = ""
    vision_model_present: bool = False
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DiskInfo:
    path: str
    total_bytes: int | None
    free_bytes: int | None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PackageInfo:
    name: str
    import_name: str
    installed: bool
    version: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AccelerationInfo:
    nvidia_cuda: dict[str, Any]
    amd_rocm_windows: dict[str, Any]
    amd_directml: dict[str, Any]
    cpu: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "nvidia_cuda": self.nvidia_cuda,
            "amd_rocm_windows": self.amd_rocm_windows,
            "amd_directml": self.amd_directml,
            "cpu": self.cpu,
        }


@dataclass
class CheckItem:
    id: str
    ok: bool
    severity: Severity
    message: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Recommendations:
    backend: str
    iqa_device: str
    torch_index_url: str | None
    torch_pip_args: list[str]
    qrealign_variant: str
    qrealign_pro_optional: bool
    vision_model: str
    acceleration: AccelerationInfo
    checks: list[CheckItem]
    ready_for_pipeline: bool
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "iqa_device": self.iqa_device,
            "torch_index_url": self.torch_index_url,
            "torch_pip_args": list(self.torch_pip_args),
            "qrealign_variant": self.qrealign_variant,
            "qrealign_pro_optional": self.qrealign_pro_optional,
            "vision_model": self.vision_model,
            "acceleration": self.acceleration.to_dict(),
            "checks": [c.to_dict() for c in self.checks],
            "ready_for_pipeline": self.ready_for_pipeline,
            "notes": list(self.notes),
        }


@dataclass
class HardwareProfile:
    probed_at: str
    os_name: str
    os_release: str
    os_supported: bool
    python: PythonInfo
    gpus: list[GpuDevice]
    torch: TorchInfo
    ollama: OllamaInfo
    disk: DiskInfo
    packages: list[PackageInfo]
    settings_backend: str
    settings_vision_model: str
    settings_iqa_device: str
    openai_key_set: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "probed_at": self.probed_at,
            "os_name": self.os_name,
            "os_release": self.os_release,
            "os_supported": self.os_supported,
            "python": self.python.to_dict(),
            "gpus": [g.to_dict() for g in self.gpus],
            "torch": self.torch.to_dict(),
            "ollama": self.ollama.to_dict(),
            "disk": self.disk.to_dict(),
            "packages": [p.to_dict() for p in self.packages],
            "settings_backend": self.settings_backend,
            "settings_vision_model": self.settings_vision_model,
            "settings_iqa_device": self.settings_iqa_device,
            "openai_key_set": self.openai_key_set,
        }


def _run_cmd(args: list[str], *, timeout: float = 15.0) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return proc.returncode, proc.stdout or "", proc.stderr or ""
    except FileNotFoundError:
        return 127, "", "not found"
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"
    except OSError as exc:
        return 1, "", str(exc)


def _probe_python() -> PythonInfo:
    if is_frozen():
        candidate = discover_base_python()
        if candidate is not None:
            return PythonInfo(
                executable=str(candidate.executable),
                version=candidate.version_str,
                version_tuple=candidate.version,
                is_64bit=candidate.is_64bit,
                in_venv=False,
            )
        return PythonInfo(
            executable="(frozen — no system Python 3.11+ found)",
            version="unknown",
            version_tuple=(0, 0, 0),
            is_64bit=False,
            in_venv=False,
        )

    vi = sys.version_info
    in_venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix)
    return PythonInfo(
        executable=sys.executable,
        version=platform.python_version(),
        version_tuple=(vi.major, vi.minor, vi.micro),
        is_64bit=sys.maxsize > 2**32,
        in_venv=in_venv,
    )


def _vendor_from_name(name: str) -> Vendor:
    lower = name.lower()
    if any(tok in lower for tok in ("nvidia", "geforce", "rtx", "gtx", "quadro", "tesla")):
        return "nvidia"
    if any(tok in lower for tok in ("amd", "radeon", "instinct")):
        return "amd"
    if "intel" in lower or "uhd" in lower or "iris" in lower:
        return "intel"
    return "unknown"


def _probe_nvidia_smi() -> list[GpuDevice]:
    code, out, _err = _run_cmd(
        [
            "nvidia-smi",
            "--query-gpu=name,memory.total",
            "--format=csv,noheader,nounits",
        ],
        timeout=10.0,
    )
    if code != 0 or not out.strip():
        return []
    devices: list[GpuDevice] = []
    for idx, line in enumerate(out.strip().splitlines()):
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 2:
            continue
        name = parts[0]
        vram_mb: int | None
        try:
            vram_mb = int(float(parts[1]))
        except ValueError:
            vram_mb = None
        devices.append(
            GpuDevice(
                vendor="nvidia",
                name=name,
                vram_mb=vram_mb,
                index=idx,
                source="nvidia-smi",
            )
        )
    return devices


def _probe_cim_gpus() -> list[GpuDevice]:
    if platform.system() != "Windows":
        return []
    ps = (
        "Get-CimInstance Win32_VideoController | "
        "Select-Object Name, AdapterRAM | ConvertTo-Json -Compress"
    )
    code, out, _err = _run_cmd(
        ["powershell", "-NoProfile", "-Command", ps],
        timeout=20.0,
    )
    if code != 0 or not out.strip():
        return []
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict):
        rows = [data]
    elif isinstance(data, list):
        rows = data
    else:
        return []
    devices: list[GpuDevice] = []
    for idx, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        name = str(row.get("Name") or "").strip()
        if not name:
            continue
        vram_mb: int | None = None
        raw_ram = row.get("AdapterRAM")
        try:
            if raw_ram is not None and int(raw_ram) > 0:
                vram_mb = int(int(raw_ram) / (1024 * 1024))
        except (TypeError, ValueError):
            vram_mb = None
        devices.append(
            GpuDevice(
                vendor=_vendor_from_name(name),
                name=name,
                vram_mb=vram_mb,
                index=idx,
                source="cim",
            )
        )
    return devices


def _merge_gpus(nvidia: list[GpuDevice], cim: list[GpuDevice]) -> list[GpuDevice]:
    if nvidia:
        merged = list(nvidia)
        nvidia_names = {g.name.lower() for g in nvidia}
        for g in cim:
            if g.vendor == "nvidia":
                continue
            # Skip CIM duplicates that look like the same NVIDIA device
            if g.name.lower() in nvidia_names:
                continue
            merged.append(
                GpuDevice(
                    vendor=g.vendor,
                    name=g.name,
                    vram_mb=g.vram_mb,
                    index=len(merged),
                    source=g.source,
                )
            )
        return merged
    # Reindex CIM-only list
    return [
        GpuDevice(
            vendor=g.vendor,
            name=g.name,
            vram_mb=g.vram_mb,
            index=i,
            source=g.source,
        )
        for i, g in enumerate(cim)
    ]


def _probe_torch() -> TorchInfo:
    try:
        import torch
    except Exception as exc:
        return TorchInfo(installed=False, error=str(exc))
    names: list[str] = []
    cuda_ok = bool(torch.cuda.is_available())
    cuda_ver = None
    try:
        cuda_ver = getattr(torch.version, "cuda", None)
    except Exception:
        cuda_ver = None
    if cuda_ok:
        try:
            for i in range(torch.cuda.device_count()):
                names.append(torch.cuda.get_device_name(i))
        except Exception:
            pass
    return TorchInfo(
        installed=True,
        version=getattr(torch, "__version__", None),
        cuda_available=cuda_ok,
        cuda_version=str(cuda_ver) if cuda_ver else None,
        device_names=names,
    )


def _probe_packages() -> list[PackageInfo]:
    specs = [
        ("pyiqa", "pyiqa"),
        ("opencv-python", "cv2"),
        ("pillow", "PIL"),
        ("rawpy", "rawpy"),
        ("ImageHash", "imagehash"),
        ("numpy", "numpy"),
        ("requests", "requests"),
        ("tqdm", "tqdm"),
    ]
    out: list[PackageInfo] = []
    for name, import_name in specs:
        try:
            mod = __import__(import_name)
            ver = getattr(mod, "__version__", None)
            out.append(
                PackageInfo(
                    name=name,
                    import_name=import_name,
                    installed=True,
                    version=str(ver) if ver else None,
                )
            )
        except Exception as exc:
            out.append(
                PackageInfo(
                    name=name,
                    import_name=import_name,
                    installed=False,
                    error=str(exc),
                )
            )
    return out


def _find_ollama_binary() -> str | None:
    which = shutil.which("ollama")
    if which:
        return which
    candidates = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe",
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Ollama" / "ollama.exe",
    ]
    for path in candidates:
        if path.is_file():
            return str(path)
    return None


def find_ollama_binary() -> str | None:
    """Public wrapper for setup / other modules."""
    return _find_ollama_binary()


def _probe_ollama() -> OllamaInfo:
    host = config.OLLAMA_HOST
    vision = config.VISION_MODEL
    binary = _find_ollama_binary()
    version: str | None = None
    if binary:
        code, out, err = _run_cmd([binary, "--version"], timeout=10.0)
        text = (out or err or "").strip()
        version = text.splitlines()[0] if text else None if code == 0 else text or None

    http_ok = False
    models: list[str] = []
    error: str | None = None
    try:
        resp = requests.get(f"{host.rstrip('/')}/api/tags", timeout=5)
        if resp.ok:
            http_ok = True
            body = resp.json()
            for item in body.get("models") or []:
                name = item.get("name") or item.get("model")
                if name:
                    models.append(str(name))
        else:
            error = f"HTTP {resp.status_code}"
    except requests.RequestException as exc:
        error = str(exc)

    present = vision in models

    return OllamaInfo(
        binary_path=binary,
        version=version,
        host=host,
        http_ok=http_ok,
        models=models,
        vision_model_configured=vision,
        vision_model_present=present,
        error=error,
    )


def _probe_disk() -> DiskInfo:
    path = Path(config.DATA_DIR)
    probe = path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    if not probe.exists():
        probe = Path.cwd()
    try:
        usage = shutil.disk_usage(probe)
        return DiskInfo(
            path=str(path),
            total_bytes=int(usage.total),
            free_bytes=int(usage.free),
        )
    except OSError as exc:
        return DiskInfo(path=str(path), total_bytes=None, free_bytes=None, error=str(exc))


def _probe_directml_installed() -> bool:
    try:
        __import__("torch_directml")
        return True
    except Exception:
        return False


def probe_system() -> HardwareProfile:
    """Collect hardware / environment profile for the current interpreter."""
    os_name = platform.system()
    gpus = _merge_gpus(_probe_nvidia_smi(), _probe_cim_gpus())
    return HardwareProfile(
        probed_at=datetime.now(timezone.utc).isoformat(),
        os_name=os_name,
        os_release=platform.release(),
        os_supported=os_name == "Windows",
        python=_probe_python(),
        gpus=gpus,
        torch=_probe_torch(),
        ollama=_probe_ollama(),
        disk=_probe_disk(),
        packages=_probe_packages(),
        settings_backend=config.BACKEND,
        settings_vision_model=config.VISION_MODEL,
        settings_iqa_device=config.IQA_DEVICE,
        openai_key_set=bool(config.OPENAI_API_KEY),
    )


def primary_nvidia_vram_mb(profile: HardwareProfile) -> int | None:
    for g in profile.gpus:
        if g.vendor == "nvidia" and g.vram_mb is not None:
            return g.vram_mb
    return None


def has_nvidia(profile: HardwareProfile) -> bool:
    return any(g.vendor == "nvidia" for g in profile.gpus)


def has_amd(profile: HardwareProfile) -> bool:
    return any(g.vendor == "amd" for g in profile.gpus)


def _acceleration(profile: HardwareProfile) -> AccelerationInfo:
    directml_installed = _probe_directml_installed()
    return AccelerationInfo(
        nvidia_cuda={
            "status": "supported" if has_nvidia(profile) else "unavailable",
            "recommended_for_iqa": has_nvidia(profile),
            "message": (
                "Use CUDA PyTorch wheels for IQA on NVIDIA GPUs."
                if has_nvidia(profile)
                else "No NVIDIA GPU detected."
            ),
        },
        amd_rocm_windows={
            "status": "not_available",
            "recommended_for_iqa": False,
            "message": "PyTorch does not ship ROCm wheels for Windows.",
        },
        amd_directml={
            "status": "experimental_unsupported",
            "installed": directml_installed,
            "recommended_for_iqa": False,
            "message": (
                "torch-directml has partial op coverage; pyiqa / Q-ReAlign are not "
                "validated on DirectML. Prefer CPU torch for IQA and Ollama for VLM on AMD."
            ),
        },
        cpu={
            "status": "supported",
            "recommended_for_iqa": not has_nvidia(profile),
            "message": "CPU torch is always available as an IQA fallback (slower).",
        },
    )


def recommend(profile: HardwareProfile) -> Recommendations:
    """Map a hardware profile to concrete install / config recommendations."""
    notes: list[str] = []
    nvidia = has_nvidia(profile)
    vram = primary_nvidia_vram_mb(profile)

    if nvidia:
        iqa_device = "cuda"
        torch_index = TORCH_CUDA_INDEX_URL
        torch_pip_args = [
            "torch",
            "torchvision",
            "--index-url",
            TORCH_CUDA_INDEX_URL,
        ]
    else:
        iqa_device = "cpu"
        torch_index = None
        torch_pip_args = ["torch", "torchvision"]
        if has_amd(profile):
            notes.append(
                "AMD GPU detected: use CPU PyTorch for IQA; Ollama can still use the GPU for VLM."
            )
        else:
            notes.append("No NVIDIA GPU: IQA will run on CPU (slower).")

    qrealign = "qrealign-lite"
    qrealign_pro_optional = False
    if vram is not None and vram >= 16 * 1024:
        qrealign_pro_optional = True
        notes.append("VRAM >= 16 GB: qrealign-pro is optional (heavier); lite remains default.")

    if not nvidia or vram is None or vram < 8 * 1024:
        vision = VISION_MODEL_LOW
    elif vram < 24 * 1024:
        vision = VISION_MODEL_MID
    else:
        vision = VISION_MODEL_HIGH

    if profile.settings_backend == "openai" and profile.openai_key_set:
        backend = "openai"
        notes.append("Settings already use OpenAI backend with API key set.")
    else:
        backend = "ollama"

    accel = _acceleration(profile)
    checks: list[CheckItem] = []

    py_ok = profile.python.version_tuple >= (3, 11, 0)
    checks.append(
        CheckItem(
            id="python_version",
            ok=py_ok,
            severity="critical",
            message=(
                f"Python {profile.python.version} (>= 3.11 required)"
                if py_ok
                else f"Python {profile.python.version} is too old; need 3.11+"
            ),
        )
    )
    checks.append(
        CheckItem(
            id="python_64bit",
            ok=profile.python.is_64bit,
            severity="critical",
            message="64-bit Python" if profile.python.is_64bit else "32-bit Python is not supported",
        )
    )
    checks.append(
        CheckItem(
            id="os_supported",
            ok=profile.os_supported,
            severity="critical",
            message=(
                f"OS {profile.os_name} {profile.os_release} (Windows supported)"
                if profile.os_supported
                else f"OS {profile.os_name} is not a supported v1 target (Windows only)"
            ),
        )
    )

    if nvidia:
        gpu_msg = f"NVIDIA GPU path ({profile.gpus[0].name if profile.gpus else 'detected'})"
        gpu_ok = True
    elif has_amd(profile):
        gpu_msg = "AMD GPU: IQA via CPU torch; VLM via Ollama"
        gpu_ok = True
    elif profile.gpus:
        gpu_msg = f"Non-NVIDIA GPU(s) detected; IQA via CPU ({profile.gpus[0].name})"
        gpu_ok = True
    else:
        gpu_msg = "No discrete GPU reported; IQA via CPU"
        gpu_ok = True
    checks.append(
        CheckItem(id="gpu_path", ok=gpu_ok, severity="info", message=gpu_msg)
    )

    torch_ok = profile.torch.installed
    if torch_ok and nvidia and not profile.torch.cuda_available:
        checks.append(
            CheckItem(
                id="torch",
                ok=False,
                severity="critical",
                message=(
                    f"torch {profile.torch.version} installed but CUDA unavailable; "
                    f"reinstall from {TORCH_CUDA_INDEX_URL}"
                ),
            )
        )
        torch_device_ok = False
    elif torch_ok:
        checks.append(
            CheckItem(
                id="torch",
                ok=True,
                severity="info",
                message=(
                    f"torch {profile.torch.version}"
                    + (
                        f", CUDA {profile.torch.cuda_version}, devices={profile.torch.device_names}"
                        if profile.torch.cuda_available
                        else " (CPU)"
                    )
                ),
            )
        )
        torch_device_ok = True
    else:
        checks.append(
            CheckItem(
                id="torch",
                ok=False,
                severity="critical",
                message=f"torch not installed ({profile.torch.error or 'import failed'})",
            )
        )
        torch_device_ok = False

    pyiqa_pkg = next((p for p in profile.packages if p.name == "pyiqa"), None)
    if profile.torch.installed:
        checks.append(
            CheckItem(
                id="pyiqa",
                ok=bool(pyiqa_pkg and pyiqa_pkg.installed),
                severity="critical",
                message=(
                    f"pyiqa {pyiqa_pkg.version}"
                    if pyiqa_pkg and pyiqa_pkg.installed
                    else "pyiqa not installed"
                ),
            )
        )
    else:
        checks.append(
            CheckItem(
                id="pyiqa",
                ok=False,
                severity="warning",
                message="pyiqa check skipped until torch is installed",
            )
        )

    for req in ("opencv-python", "pillow", "numpy", "requests", "tqdm"):
        pkg = next((p for p in profile.packages if p.name == req), None)
        ok = bool(pkg and pkg.installed)
        checks.append(
            CheckItem(
                id=f"pkg_{req}",
                ok=ok,
                severity="critical" if req in ("pillow", "numpy", "opencv-python") else "warning",
                message=f"{req} OK" if ok else f"{req} missing",
            )
        )

    if backend == "ollama":
        checks.append(
            CheckItem(
                id="ollama_http",
                ok=profile.ollama.http_ok,
                severity="critical",
                message=(
                    f"Ollama reachable at {profile.ollama.host}"
                    if profile.ollama.http_ok
                    else f"Ollama not reachable at {profile.ollama.host}"
                    + (f" ({profile.ollama.error})" if profile.ollama.error else "")
                ),
            )
        )
        checks.append(
            CheckItem(
                id="ollama_binary",
                ok=bool(profile.ollama.binary_path),
                severity="warning",
                message=(
                    f"ollama binary: {profile.ollama.binary_path}"
                    if profile.ollama.binary_path
                    else "ollama binary not found on PATH"
                ),
            )
        )
        checks.append(
            CheckItem(
                id="vision_model",
                ok=profile.ollama.vision_model_present,
                severity="warning",
                message=(
                    f"Vision model present: {profile.ollama.vision_model_configured}"
                    if profile.ollama.vision_model_present
                    else (
                        f"Vision model '{profile.ollama.vision_model_configured}' not in "
                        f"ollama list; suggested pull: {vision}"
                    )
                ),
            )
        )

    free = profile.disk.free_bytes
    disk_ok = free is not None and free >= DISK_WARN_FREE_BYTES
    if free is None:
        disk_msg = f"Disk check failed for {profile.disk.path}: {profile.disk.error}"
        disk_ok = False
    else:
        free_gb = free / (1024**3)
        disk_msg = f"{free_gb:.1f} GiB free under {profile.disk.path}"
        if not disk_ok:
            disk_msg += " (< 15 GiB recommended for models/weights)"
    checks.append(
        CheckItem(
            id="disk_space",
            ok=disk_ok,
            severity="warning",
            message=disk_msg,
        )
    )

    critical_ok = all(c.ok for c in checks if c.severity == "critical")
    ready = bool(profile.os_supported and critical_ok and torch_device_ok)

    return Recommendations(
        backend=backend,
        iqa_device=iqa_device,
        torch_index_url=torch_index,
        torch_pip_args=torch_pip_args,
        qrealign_variant=qrealign,
        qrealign_pro_optional=qrealign_pro_optional,
        vision_model=vision,
        acceleration=accel,
        checks=checks,
        ready_for_pipeline=ready,
        notes=notes,
    )


def format_report(profile: HardwareProfile, rec: Recommendations) -> str:
    lines: list[str] = []
    lines.append("=== Macro Review doctor ===")
    lines.append(f"Probed at: {profile.probed_at}")
    lines.append("")
    lines.append("-- System --")
    lines.append(f"OS: {profile.os_name} {profile.os_release} (supported={profile.os_supported})")
    lines.append(
        f"Python: {profile.python.version} ({'64-bit' if profile.python.is_64bit else '32-bit'}) "
        f"venv={profile.python.in_venv}"
    )
    lines.append(f"Executable: {profile.python.executable}")
    lines.append("")
    lines.append("-- GPU --")
    if not profile.gpus:
        lines.append("(none detected)")
    for g in profile.gpus:
        vram = f"{g.vram_mb} MiB" if g.vram_mb is not None else "VRAM unknown"
        lines.append(f"[{g.index}] {g.vendor}: {g.name} ({vram}, via {g.source})")
    lines.append("")
    lines.append("-- Torch --")
    if profile.torch.installed:
        lines.append(
            f"installed={profile.torch.version} cuda_available={profile.torch.cuda_available} "
            f"cuda={profile.torch.cuda_version} devices={profile.torch.device_names}"
        )
    else:
        lines.append(f"not installed ({profile.torch.error})")
    lines.append("")
    lines.append("-- Packages --")
    for p in profile.packages:
        if p.installed:
            lines.append(f"  {p.name}: {p.version or 'ok'}")
        else:
            lines.append(f"  {p.name}: MISSING")
    lines.append("")
    lines.append("-- Ollama --")
    lines.append(f"host: {profile.ollama.host}")
    lines.append(f"binary: {profile.ollama.binary_path or '(not found)'}")
    lines.append(f"version: {profile.ollama.version or '(unknown)'}")
    lines.append(f"http_ok: {profile.ollama.http_ok}")
    lines.append(
        f"configured vision model: {profile.ollama.vision_model_configured} "
        f"(present={profile.ollama.vision_model_present})"
    )
    if profile.ollama.models:
        preview = ", ".join(profile.ollama.models[:8])
        more = f" ...(+{len(profile.ollama.models) - 8})" if len(profile.ollama.models) > 8 else ""
        lines.append(f"models: {preview}{more}")
    if profile.ollama.error and not profile.ollama.http_ok:
        lines.append(f"error: {profile.ollama.error}")
    lines.append("")
    lines.append("-- Disk --")
    if profile.disk.free_bytes is not None and profile.disk.total_bytes is not None:
        lines.append(
            f"{profile.disk.path}: {profile.disk.free_bytes / 1024**3:.1f} GiB free / "
            f"{profile.disk.total_bytes / 1024**3:.1f} GiB total"
        )
    else:
        lines.append(f"{profile.disk.path}: error={profile.disk.error}")
    lines.append("")
    lines.append("-- Acceleration --")
    for key, val in rec.acceleration.to_dict().items():
        lines.append(f"  {key}: {val.get('status')} - {val.get('message')}")
        if key == "amd_directml" and "installed" in val:
            lines.append(f"    installed={val['installed']}")
    lines.append("")
    lines.append("-- Recommendations --")
    lines.append(f"backend: {rec.backend}")
    lines.append(f"iqa_device: {rec.iqa_device}")
    lines.append(f"qrealign_variant: {rec.qrealign_variant} (pro_optional={rec.qrealign_pro_optional})")
    lines.append(f"vision_model: {rec.vision_model}")
    lines.append(f"torch_index_url: {rec.torch_index_url or '(PyPI / CPU default)'}")
    lines.append(f"torch_pip_args: {' '.join(rec.torch_pip_args)}")
    for note in rec.notes:
        lines.append(f"note: {note}")
    lines.append("")
    lines.append("-- Checklist --")
    for c in rec.checks:
        mark = "OK" if c.ok else "FAIL"
        lines.append(f"  [{mark}] ({c.severity}) {c.id}: {c.message}")
    lines.append("")
    lines.append(f"ready_for_pipeline: {rec.ready_for_pipeline}")
    return "\n".join(lines) + "\n"


def doctor_payload(profile: HardwareProfile | None = None) -> dict[str, Any]:
    profile = profile or probe_system()
    rec = recommend(profile)
    return {"profile": profile.to_dict(), "recommendations": rec.to_dict()}
