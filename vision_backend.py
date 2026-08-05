"""Vision LLM backends for aesthetic / crop scoring."""

from __future__ import annotations

import base64
import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

import config

PROMPT_VERSION = config.PROMPT_VERSION

SYSTEM_PROMPT = """You are an expert macro photography critic specializing in insects, birds, and close-up nature photography.
Be honest and critical — most photos are average and not worth sharing.

For insect/macro shots, prioritize:
- Critical focus on the eye(s) or primary facial structure
- Whether shallow DOF helps or hurts (important structures lost to blur?)
- Motion blur, stacking artifacts, diffraction softness
- Flash quality (harsh vs controlled) and clipped highlights on shiny shells/eyes/wings
- Background smoothness and subject separation
- Subject placement, viewing angle, gaze/body orientation, negative space
- Whether appendages are cut off awkwardly or the pose feels dynamic

For birds/other subjects, adapt the same principles (eye sharp, clean background, compelling pose).

Always provide subject_box tightly around the main subject.
If an eye (or primary focus target like a compound eye) is visible, provide eye_box tightly around it; otherwise null.
If a crop would meaningfully improve the image, set crop_worthy true and provide crop_box.
All boxes are [x0, y0, x1, y1] as fractions of image width/height in [0,1], (0,0)=top-left.
Respond ONLY with valid JSON matching the schema. No markdown fences."""

USER_PROMPT = """Score this macro / nature photo for sharing potential.

Return JSON with exactly these keys:
{
  "overall_score": <number 1-10, how worth sharing>,
  "sharpness_score": <number 1-10, overall perceived sharpness>,
  "composition_score": <number 1-10>,
  "eye_focus_score": <number 1-10, how critically sharp the eye/primary focus target is; use 5 if no clear eye>,
  "dof_quality": <number 1-10, shallow DOF helping vs hurting>,
  "background_score": <number 1-10, separation and cleanliness>,
  "lighting_score": <number 1-10>,
  "pose_score": <number 1-10, pose/behavior interest>,
  "subject": <short string, e.g. "bee on flower", "lacewing on siding", "songbird">,
  "distractions": <short string listing main distractions, or empty string>,
  "share_recommendation": <one of: "skip", "maybe", "share", "portfolio">,
  "subject_box": <[x0,y0,x1,y1] fractions tightly around the main subject>,
  "eye_box": <[x0,y0,x1,y1] fractions around the eye/focus target, or null>,
  "crop_worthy": <boolean>,
  "crop_box": <[x0,y0,x1,y1] fractions or null>,
  "crop_reason": <string or null>,
  "comment": <2-3 sentence critique covering focus placement, DOF, background, and share-worthiness>
}
"""

_SHARE_RECS = {"skip", "maybe", "share", "portfolio"}


@dataclass
class VisionResult:
    overall_score: float
    sharpness_score: float
    composition_score: float
    eye_focus_score: float
    dof_quality: float
    background_score: float
    lighting_score: float
    pose_score: float
    subject: str
    distractions: str
    share_recommendation: str
    crop_worthy: bool
    crop_box: list[float] | None
    crop_reason: str | None
    comment: str
    subject_box: list[float] | None
    eye_box: list[float] | None
    raw_response: str
    model: str
    backend: str
    prompt_version: str = PROMPT_VERSION


def _clamp_score(value: Any, default: float = 5.0) -> float:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return default
    return max(1.0, min(10.0, n))


def _parse_box(value: Any, *, allow_full: bool = False) -> list[float] | None:
    if value is None:
        return None
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        box = [float(v) for v in value]
    except (TypeError, ValueError):
        return None
    if any(v < 0.0 or v > 1.0 for v in box):
        return None
    x0, y0, x1, y1 = box
    if x1 <= x0 or y1 <= y0:
        return None
    area = (x1 - x0) * (y1 - y0)
    if not allow_full and area >= 0.97:
        return None
    if area < 0.0005:
        return None
    return [x0, y0, x1, y1]


def _parse_crop_box(value: Any) -> list[float] | None:
    return _parse_box(value, allow_full=False)


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL | re.IGNORECASE)
    if fence:
        text = fence.group(1)
    else:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            text = text[start : end + 1]
    return json.loads(text)


def parse_vision_json(raw: str, *, model: str, backend: str) -> VisionResult:
    data = _extract_json(raw)
    crop_worthy = bool(data.get("crop_worthy", False))
    crop_box = _parse_crop_box(data.get("crop_box"))
    if not crop_worthy:
        crop_box = None
    if crop_worthy and crop_box is None:
        crop_worthy = False
    rec = str(data.get("share_recommendation") or "maybe").strip().lower()
    if rec not in _SHARE_RECS:
        rec = "maybe"
    subject_box = _parse_box(data.get("subject_box"), allow_full=True)
    eye_box = _parse_box(data.get("eye_box"), allow_full=True)
    return VisionResult(
        overall_score=_clamp_score(data.get("overall_score")),
        sharpness_score=_clamp_score(data.get("sharpness_score")),
        composition_score=_clamp_score(data.get("composition_score")),
        eye_focus_score=_clamp_score(data.get("eye_focus_score")),
        dof_quality=_clamp_score(data.get("dof_quality")),
        background_score=_clamp_score(data.get("background_score")),
        lighting_score=_clamp_score(data.get("lighting_score")),
        pose_score=_clamp_score(data.get("pose_score")),
        subject=str(data.get("subject") or "unknown")[:200],
        distractions=str(data.get("distractions") or "")[:400],
        share_recommendation=rec,
        crop_worthy=crop_worthy,
        crop_box=crop_box,
        crop_reason=(str(data["crop_reason"])[:400] if data.get("crop_reason") else None),
        comment=str(data.get("comment") or "")[:1000],
        subject_box=subject_box,
        eye_box=eye_box,
        raw_response=raw,
        model=model,
        backend=backend,
        prompt_version=PROMPT_VERSION,
    )


def _image_b64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("ascii")


class VisionBackend(ABC):
    @abstractmethod
    def score(self, preview_path: Path) -> VisionResult:
        raise NotImplementedError


class OllamaBackend(VisionBackend):
    def __init__(
        self,
        host: str | None = None,
        model: str | None = None,
        timeout: int | None = None,
    ) -> None:
        self.host = (host or config.OLLAMA_HOST).rstrip("/")
        self.model = model or config.VISION_MODEL
        self.timeout = timeout or config.OLLAMA_TIMEOUT_SEC

    def score(self, preview_path: Path) -> VisionResult:
        b64 = _image_b64(preview_path)
        payload = {
            "model": self.model,
            "stream": False,
            "format": "json",
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": USER_PROMPT,
                    "images": [b64],
                },
            ],
            "options": {
                "temperature": 0.2,
            },
        }
        last_error: Exception | None = None
        for _attempt in range(2):
            try:
                resp = requests.post(
                    f"{self.host}/api/chat",
                    json=payload,
                    timeout=self.timeout,
                )
                resp.raise_for_status()
                body = resp.json()
                raw = body.get("message", {}).get("content") or body.get("response") or ""
                if not raw:
                    raise RuntimeError(f"Empty Ollama response: {body}")
                return parse_vision_json(raw, model=self.model, backend="ollama")
            except (json.JSONDecodeError, KeyError, TypeError, ValueError, requests.RequestException) as exc:
                last_error = exc
                continue
        raise RuntimeError(f"Ollama scoring failed after retry: {last_error}")


class OpenAIBackend(VisionBackend):
    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        timeout: int | None = None,
    ) -> None:
        key = api_key or config.OPENAI_API_KEY
        if not key:
            raise RuntimeError("OPENAI_API_KEY is not set")
        from openai import OpenAI

        self.client = OpenAI(api_key=key, timeout=timeout or config.OPENAI_TIMEOUT_SEC)
        self.model = model or config.OPENAI_MODEL

    def score(self, preview_path: Path) -> VisionResult:
        b64 = _image_b64(preview_path)
        data_url = f"data:image/jpeg;base64,{b64}"
        last_error: Exception | None = None
        for _attempt in range(2):
            try:
                resp = self.client.chat.completions.create(
                    model=self.model,
                    temperature=0.2,
                    response_format={"type": "json_object"},
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": USER_PROMPT},
                                {"type": "image_url", "image_url": {"url": data_url}},
                            ],
                        },
                    ],
                )
                raw = resp.choices[0].message.content or ""
                if not raw:
                    raise RuntimeError("Empty OpenAI response")
                return parse_vision_json(raw, model=self.model, backend="openai")
            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                last_error = exc
                continue
        raise RuntimeError(f"OpenAI scoring failed after retry: {last_error}")


def get_backend(name: str | None = None) -> VisionBackend:
    backend = (name or config.BACKEND).lower().strip()
    if backend == "ollama":
        return OllamaBackend()
    if backend == "openai":
        return OpenAIBackend()
    raise ValueError(f"Unknown backend: {backend!r} (expected 'ollama' or 'openai')")
