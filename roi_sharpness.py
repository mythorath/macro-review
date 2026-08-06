"""Full-resolution ROI sharpness + composition metrics."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np
from tqdm import tqdm

import config
from db import db, fetchall, init_db
from image_io import box_to_pixels, load_full_image


def _laplacian_var(gray: np.ndarray) -> float:
    if gray.size == 0:
        return 0.0
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def _tenengrad(gray: np.ndarray) -> float:
    if gray.size == 0:
        return 0.0
    gx = cv2.Sobel(gray, cv2.CV_64F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_64F, 0, 1, ksize=3)
    return float(np.mean(gx * gx + gy * gy))


def _fft_hf_ratio(gray: np.ndarray) -> float:
    if gray.size < 16:
        return 0.0
    f = np.fft.fft2(gray.astype(np.float64))
    fshift = np.fft.fftshift(f)
    mag = np.abs(fshift)
    h, w = mag.shape
    cy, cx = h // 2, w // 2
    radius = max(1, min(h, w) // 8)
    y, x = np.ogrid[:h, :w]
    mask_low = (y - cy) ** 2 + (x - cx) ** 2 <= radius**2
    low = float(mag[mask_low].sum()) + 1e-8
    high = float(mag[~mask_low].sum())
    return high / low


def _edge_density(gray: np.ndarray) -> float:
    if gray.size == 0:
        return 0.0
    edges = cv2.Canny(gray, 50, 150)
    return float(np.count_nonzero(edges)) / float(edges.size)


def _fft_anisotropy(gray: np.ndarray) -> float:
    """High anisotropy suggests directional motion blur."""
    if gray.size < 64:
        return 0.0
    f = np.fft.fft2(gray.astype(np.float64))
    mag = np.abs(np.fft.fftshift(f))
    h, w = mag.shape
    cy, cx = h // 2, w // 2
    # Sample radial energy along horizontal vs vertical
    horiz = float(mag[cy, :].mean()) + 1e-8
    vert = float(mag[:, cx].mean()) + 1e-8
    ratio = max(horiz, vert) / min(horiz, vert)
    return float(ratio)


def _saliency_box(gray: np.ndarray) -> list[float]:
    """Spectral residual saliency → bounding box of top 15% saliency mass."""
    h, w = gray.shape
    sal = None
    try:
        saliency = cv2.saliency.StaticSaliencySpectralResidual_create()
        ok, sal = saliency.computeSaliency(gray)
        if not ok:
            sal = None
    except Exception:
        sal = None
    if sal is None:
        # Fallback: center 40% of the frame
        return [0.3, 0.3, 0.7, 0.7]
    sal = (sal * 255).astype(np.uint8)
    thresh = np.percentile(sal, 85)
    mask = sal >= thresh
    ys, xs = np.where(mask)
    if len(xs) < 10:
        return [0.3, 0.3, 0.7, 0.7]
    x0 = float(xs.min()) / w
    x1 = float(xs.max() + 1) / w
    y0 = float(ys.min()) / h
    y1 = float(ys.max() + 1) / h
    # Expand slightly
    pad = 0.02
    return [
        max(0.0, x0 - pad),
        max(0.0, y0 - pad),
        min(1.0, x1 + pad),
        min(1.0, y1 + pad),
    ]


def _region_metrics(gray: np.ndarray, box: list[float] | None) -> dict[str, float]:
    h, w = gray.shape
    if box is None:
        return {"laplacian": 0.0, "tenengrad": 0.0, "fft": 0.0, "edge": 0.0}
    left, top, right, bottom = box_to_pixels(box, w, h)
    crop = gray[top:bottom, left:right]
    return {
        "laplacian": _laplacian_var(crop),
        "tenengrad": _tenengrad(crop),
        "fft": _fft_hf_ratio(crop),
        "edge": _edge_density(crop),
    }


def _bg_mask_metrics(gray: np.ndarray, subject_box: list[float]) -> dict[str, float]:
    h, w = gray.shape
    left, top, right, bottom = box_to_pixels(subject_box, w, h)
    mask = np.ones((h, w), dtype=np.uint8)
    mask[top:bottom, left:right] = 0
    if int(mask.sum()) < 100:
        return {"laplacian": 0.0, "tenengrad": 0.0, "fft": 0.0, "edge": 0.0}
    # Dilate subject exclusion so edges of subject don't bleed into bg
    kernel = np.ones((7, 7), np.uint8)
    mask = cv2.erode(mask, kernel, iterations=1)
    bg = gray.copy()
    bg[mask == 0] = 0
    # Use only bg pixels via crop of non-zero bounding region is messy;
    # compute on masked array by zeroing subject then measuring whole frame
    # with edge density only on mask.
    lap = float(cv2.Laplacian(bg, cv2.CV_64F).var()) if mask.any() else 0.0
    # Better: sample bg pixels into a contiguous crop from top/left strips if needed
    ys, xs = np.where(mask > 0)
    if len(xs) < 50:
        return {"laplacian": 0.0, "tenengrad": 0.0, "fft": 0.0, "edge": 0.0}
    # Approximate bg sharpness from a random sample of bg patches
    patches = []
    rng = np.random.default_rng(0)
    for _ in range(8):
        idx = int(rng.integers(0, len(xs)))
        cy, cx = int(ys[idx]), int(xs[idx])
        y0, y1 = max(0, cy - 32), min(h, cy + 32)
        x0, x1 = max(0, cx - 32), min(w, cx + 32)
        patch = gray[y0:y1, x0:x1]
        if patch.size > 100:
            patches.append(patch)
    if not patches:
        return {"laplacian": 0.0, "tenengrad": 0.0, "fft": 0.0, "edge": 0.0}
    laps = [_laplacian_var(p) for p in patches]
    tens = [_tenengrad(p) for p in patches]
    ffts = [_fft_hf_ratio(p) for p in patches]
    edges = cv2.Canny(gray, 50, 150)
    edge = float(np.count_nonzero(edges[mask > 0])) / float(np.count_nonzero(mask))
    return {
        "laplacian": float(np.mean(laps)),
        "tenengrad": float(np.mean(tens)),
        "fft": float(np.mean(ffts)),
        "edge": edge,
    }


def _thirds_distance(box: list[float]) -> float:
    """Distance of subject centroid to nearest rule-of-thirds power point (0=perfect)."""
    cx = (box[0] + box[2]) / 2.0
    cy = (box[1] + box[3]) / 2.0
    points = [(1 / 3, 1 / 3), (2 / 3, 1 / 3), (1 / 3, 2 / 3), (2 / 3, 2 / 3)]
    dists = [((cx - px) ** 2 + (cy - py) ** 2) ** 0.5 for px, py in points]
    # Normalize by max possible (~0.75 diagonal of unit square to corner thirds)
    return float(min(dists) / 0.75)


def _edge_cut(box: list[float], margin: float = 0.02) -> bool:
    return box[0] <= margin or box[1] <= margin or box[2] >= 1.0 - margin or box[3] >= 1.0 - margin


def analyze_roi(path: Path, subject_box: list[float] | None, eye_box: list[float] | None) -> dict:
    img = load_full_image(path)
    arr = np.asarray(img)
    gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
    source = "vlm"
    if subject_box is None:
        subject_box = _saliency_box(gray)
        source = "saliency"

    eye_m = _region_metrics(gray, eye_box)
    sub_m = _region_metrics(gray, subject_box)
    bg_m = _bg_mask_metrics(gray, subject_box)

    eye_vs = (eye_m["laplacian"] / (sub_m["laplacian"] + 1e-8)) if eye_box else 1.0
    separation = sub_m["edge"] / (bg_m["edge"] + 1e-8)
    # eye_sharpness provisional 0-10 from log laplacian (percentile later)
    eye_sharp_raw = eye_m["laplacian"] if eye_box else sub_m["laplacian"]
    eye_sharpness = float(min(10.0, max(0.0, np.log1p(eye_sharp_raw) * 1.15)))

    anisotropy = _fft_anisotropy(gray)
    motion_flag = 1 if anisotropy >= 2.5 else 0

    size_frac = (subject_box[2] - subject_box[0]) * (subject_box[3] - subject_box[1])

    return {
        "eye_laplacian": eye_m["laplacian"],
        "eye_tenengrad": eye_m["tenengrad"],
        "eye_fft": eye_m["fft"],
        "subject_laplacian": sub_m["laplacian"],
        "subject_tenengrad": sub_m["tenengrad"],
        "subject_fft": sub_m["fft"],
        "bg_laplacian": bg_m["laplacian"],
        "bg_tenengrad": bg_m["tenengrad"],
        "bg_fft": bg_m["fft"],
        "eye_sharpness": eye_sharpness,
        "eye_vs_subject_ratio": float(eye_vs),
        "subject_bg_separation": float(min(20.0, separation)),
        "motion_blur_flag": motion_flag,
        "thirds_distance": _thirds_distance(subject_box),
        "subject_size_frac": float(size_frac),
        "edge_cut": 1 if _edge_cut(subject_box) else 0,
        "bg_clutter": float(bg_m["edge"]),
        "subject_box": json.dumps(subject_box),
        "eye_box": json.dumps(eye_box) if eye_box else None,
        "source": source,
    }


def _pending(
    force: bool,
    limit: int | None,
    path_dirs: list[Path] | None,
    *,
    recursive: bool = True,
) -> list:
    with db() as conn:
        if force:
            rows = fetchall(
                conn,
                """
                SELECT i.id, i.path, s.subject_box, s.eye_box
                FROM images i
                LEFT JOIN scores s ON s.image_id = i.id
                ORDER BY i.source_library, i.filename
                """,
            )
        else:
            rows = fetchall(
                conn,
                """
                SELECT i.id, i.path, s.subject_box, s.eye_box
                FROM images i
                LEFT JOIN scores s ON s.image_id = i.id
                LEFT JOIN roi_metrics r ON r.image_id = i.id
                WHERE r.image_id IS NULL OR r.error IS NOT NULL
                ORDER BY i.source_library, i.filename
                """,
            )
    if path_dirs:
        rows = [
            r
            for r in rows
            if config.path_under_dirs(r["path"], path_dirs, recursive=recursive)
        ]
    if limit is not None:
        rows = rows[:limit]
    return rows


def compute_roi(
    *,
    force: bool = False,
    limit: int | None = None,
    path_dirs: list[Path] | None = None,
    recursive: bool = True,
) -> int:
    """Compute ROI sharpness/composition for pending images."""
    init_db()
    rows = _pending(force, limit, path_dirs, recursive=recursive)
    if not rows:
        print("No images pending ROI analysis.")
        return 0

    now = datetime.now(timezone.utc).isoformat()
    ok = 0
    for row in tqdm(rows, desc="ROI", unit="img"):
        image_id = row["id"]
        path = Path(row["path"])
        subject_box = eye_box = None
        if row["subject_box"]:
            try:
                subject_box = json.loads(row["subject_box"])
            except json.JSONDecodeError:
                subject_box = None
        if row["eye_box"]:
            try:
                eye_box = json.loads(row["eye_box"])
            except json.JSONDecodeError:
                eye_box = None
        error = None
        metrics: dict | None = None
        try:
            if not path.exists():
                raise FileNotFoundError(f"missing: {path}")
            metrics = analyze_roi(path, subject_box, eye_box)
            ok += 1
        except Exception as exc:
            error = str(exc)
            print(f"WARNING: ROI failed for {path}: {exc}")

        with db() as conn:
            if metrics is None:
                conn.execute(
                    """
                    INSERT INTO roi_metrics (image_id, computed_at, error)
                    VALUES (?, ?, ?)
                    ON CONFLICT(image_id) DO UPDATE SET
                        computed_at=excluded.computed_at, error=excluded.error
                    """,
                    (image_id, now, error),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO roi_metrics (
                        image_id, eye_laplacian, eye_tenengrad, eye_fft,
                        subject_laplacian, subject_tenengrad, subject_fft,
                        bg_laplacian, bg_tenengrad, bg_fft,
                        eye_sharpness, eye_vs_subject_ratio, subject_bg_separation,
                        motion_blur_flag, thirds_distance, subject_size_frac,
                        edge_cut, bg_clutter, subject_box, eye_box, source,
                        computed_at, error
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    )
                    ON CONFLICT(image_id) DO UPDATE SET
                        eye_laplacian=excluded.eye_laplacian,
                        eye_tenengrad=excluded.eye_tenengrad,
                        eye_fft=excluded.eye_fft,
                        subject_laplacian=excluded.subject_laplacian,
                        subject_tenengrad=excluded.subject_tenengrad,
                        subject_fft=excluded.subject_fft,
                        bg_laplacian=excluded.bg_laplacian,
                        bg_tenengrad=excluded.bg_tenengrad,
                        bg_fft=excluded.bg_fft,
                        eye_sharpness=excluded.eye_sharpness,
                        eye_vs_subject_ratio=excluded.eye_vs_subject_ratio,
                        subject_bg_separation=excluded.subject_bg_separation,
                        motion_blur_flag=excluded.motion_blur_flag,
                        thirds_distance=excluded.thirds_distance,
                        subject_size_frac=excluded.subject_size_frac,
                        edge_cut=excluded.edge_cut,
                        bg_clutter=excluded.bg_clutter,
                        subject_box=excluded.subject_box,
                        eye_box=excluded.eye_box,
                        source=excluded.source,
                        computed_at=excluded.computed_at,
                        error=excluded.error
                    """,
                    (
                        image_id,
                        metrics["eye_laplacian"],
                        metrics["eye_tenengrad"],
                        metrics["eye_fft"],
                        metrics["subject_laplacian"],
                        metrics["subject_tenengrad"],
                        metrics["subject_fft"],
                        metrics["bg_laplacian"],
                        metrics["bg_tenengrad"],
                        metrics["bg_fft"],
                        metrics["eye_sharpness"],
                        metrics["eye_vs_subject_ratio"],
                        metrics["subject_bg_separation"],
                        metrics["motion_blur_flag"],
                        metrics["thirds_distance"],
                        metrics["subject_size_frac"],
                        metrics["edge_cut"],
                        metrics["bg_clutter"],
                        metrics["subject_box"],
                        metrics["eye_box"],
                        metrics["source"],
                        now,
                        None,
                    ),
                )

    print(f"ROI analyzed {ok}/{len(rows)} images.")
    return ok


if __name__ == "__main__":
    compute_roi(limit=5)
