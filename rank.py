"""Blend IQA ensemble + VLM + ROI into percentile-calibrated share_score v2."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from tqdm import tqdm

import config
from db import db, fetchall, init_db


def _as_float(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _percentile_map(values: dict[str, float]) -> dict[str, float]:
    """Map image_id -> percentile 0–10 among provided values (higher = better)."""
    if not values:
        return {}
    ordered = sorted(values.items(), key=lambda kv: kv[1])
    n = len(ordered)
    if n == 1:
        return {ordered[0][0]: 5.0}
    out: dict[str, float] = {}
    for i, (image_id, _) in enumerate(ordered):
        out[image_id] = 10.0 * i / (n - 1)
    return out


def _weighted(components: dict[str, float | None], weights: dict[str, float]) -> float | None:
    present = {k: v for k, v in components.items() if v is not None and k in weights}
    if not present:
        return None
    wsum = sum(weights[k] for k in present)
    if wsum <= 0:
        return None
    return float(sum(present[k] * (weights[k] / wsum) for k in present))


def _invert_distance(dist: float | None) -> float | None:
    """thirds_distance 0=perfect → 10; 1=worst → 0."""
    if dist is None:
        return None
    return float(max(0.0, min(10.0, (1.0 - dist) * 10.0)))


def _invert_clutter(clutter: float | None) -> float | None:
    """bg_clutter ~0–1 edge density; lower is better."""
    if clutter is None:
        return None
    # Typical clutter 0.02–0.25
    return float(max(0.0, min(10.0, 10.0 * (1.0 - min(1.0, clutter * 4.0)))))


def _sep_score(sep: float | None) -> float | None:
    """subject_bg_separation: higher better, soft-cap around 5."""
    if sep is None:
        return None
    return float(max(0.0, min(10.0, np_log_scale(sep))))


def np_log_scale(x: float) -> float:
    import math

    return min(10.0, math.log1p(max(0.0, x)) * 3.5)


def compute_rankings(
    *,
    force: bool = False,
    limit: int | None = None,
    path_dirs: list[Path] | None = None,
) -> int:
    """Compute blend_v2 share_score + sub-composites. Returns count written."""
    init_db()
    with db() as conn:
        images = fetchall(
            conn,
            """
            SELECT i.id, i.path,
                   s.overall_score, s.eye_focus_score, s.pose_score, s.composition_score,
                   r.eye_sharpness, r.subject_bg_separation, r.thirds_distance, r.bg_clutter,
                   rk.share_score, rk.rank_version
            FROM images i
            LEFT JOIN scores s ON s.image_id = i.id AND s.error IS NULL
            LEFT JOIN roi_metrics r ON r.image_id = i.id AND r.error IS NULL
            LEFT JOIN rankings rk ON rk.image_id = i.id
            ORDER BY i.source_library, i.filename
            """,
        )
        metric_rows = fetchall(
            conn,
            """
            SELECT image_id, metric, score
            FROM iqa_metrics
            WHERE error IS NULL AND score IS NOT NULL
            """,
        )

    if path_dirs:
        images = [r for r in images if config.path_under_dirs(r["path"], path_dirs)]

    metrics_by_image: dict[str, dict[str, float]] = defaultdict(dict)
    for m in metric_rows:
        metrics_by_image[m["image_id"]][m["metric"]] = float(m["score"])

    # Collect absolute component series for percentile calibration
    series: dict[str, dict[str, float]] = defaultdict(dict)
    for row in images:
        iid = row["id"]
        for key, val in metrics_by_image.get(iid, {}).items():
            series[key][iid] = val
        for key, col in (
            ("eye_sharpness", "eye_sharpness"),
            ("overall", "overall_score"),
            ("eye_focus", "eye_focus_score"),
            ("pose", "pose_score"),
            ("vlm_composition", "composition_score"),
        ):
            v = _as_float(row[col])
            if v is not None:
                series[key][iid] = v
        sep = _sep_score(_as_float(row["subject_bg_separation"]))
        if sep is not None:
            series["bg_separation"][iid] = sep
        thirds = _invert_distance(_as_float(row["thirds_distance"]))
        if thirds is not None:
            series["thirds"][iid] = thirds
        clutter = _invert_clutter(_as_float(row["bg_clutter"]))
        if clutter is not None:
            series["clutter"][iid] = clutter

    pct: dict[str, dict[str, float]] = {k: _percentile_map(v) for k, v in series.items()}

    pending = []
    for row in images:
        if force or row["share_score"] is None or row["rank_version"] != config.RANK_VERSION:
            pending.append(row)
    if limit is not None:
        pending = pending[:limit]
    if not pending:
        print("No images pending ranking.")
        return 0

    now = datetime.now(timezone.utc).isoformat()
    written = 0
    for row in tqdm(pending, desc="Ranking", unit="img"):
        iid = row["id"]

        def p(key: str) -> float | None:
            return pct.get(key, {}).get(iid)

        tech = _weighted(
            {
                "topiq_nr": p("topiq_nr"),
                "clipiqa+": p("clipiqa+"),
                "maniqa": p("maniqa"),
                "qrealign_quality": p("qrealign_quality"),
                "eye_sharpness": p("eye_sharpness"),
            },
            config.TECH_WEIGHTS,
        )
        aes = _weighted(
            {
                "qrealign_aesthetic": p("qrealign_aesthetic"),
                "topiq_iaa": p("topiq_iaa"),
                "laion_aes": p("laion_aes"),
                "nima": p("nima"),
            },
            config.AES_WEIGHTS,
        )
        vlm = _weighted(
            {
                "overall": p("overall"),
                "eye_focus": p("eye_focus"),
                "pose": p("pose"),
            },
            config.VLM_WEIGHTS,
        )
        comp = _weighted(
            {
                "vlm_composition": p("vlm_composition"),
                "thirds": p("thirds"),
                "bg_separation": p("bg_separation"),
                "clutter": p("clutter"),
            },
            config.COMP_WEIGHTS,
        )
        share = _weighted(
            {"tech": tech, "aes": aes, "vlm": vlm, "comp": comp},
            config.SHARE_WEIGHTS_V2,
        )
        if share is None:
            continue

        components = {
            "absolute": {k: series[k].get(iid) for k in series if iid in series[k]},
            "percentile": {k: pct[k].get(iid) for k in pct if iid in pct[k]},
            "sub": {
                "tech": tech,
                "aes": aes,
                "vlm": vlm,
                "comp": comp,
            },
        }
        with db() as conn:
            conn.execute(
                """
                INSERT INTO rankings (
                    image_id, share_score, rank_version, components_json, ranked_at,
                    tech_score_c, aes_score_c, vlm_score_c, comp_score_c
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(image_id) DO UPDATE SET
                    share_score=excluded.share_score,
                    rank_version=excluded.rank_version,
                    components_json=excluded.components_json,
                    ranked_at=excluded.ranked_at,
                    tech_score_c=excluded.tech_score_c,
                    aes_score_c=excluded.aes_score_c,
                    vlm_score_c=excluded.vlm_score_c,
                    comp_score_c=excluded.comp_score_c
                """,
                (
                    iid,
                    share,
                    config.RANK_VERSION,
                    json.dumps(components),
                    now,
                    tech,
                    aes,
                    vlm,
                    comp,
                ),
            )
        written += 1

    # Refresh is_best flags after ranking
    with db() as conn:
        groups = fetchall(conn, "SELECT DISTINCT group_id FROM dupe_groups")
        for g in groups:
            members = fetchall(
                conn,
                """
                SELECT d.image_id, COALESCE(r.share_score, 0) AS score
                FROM dupe_groups d
                LEFT JOIN rankings r ON r.image_id = d.image_id
                WHERE d.group_id = ?
                ORDER BY score DESC, d.image_id
                """,
                (g["group_id"],),
            )
            if not members:
                continue
            conn.execute(
                "UPDATE dupe_groups SET is_best=0 WHERE group_id=?",
                (g["group_id"],),
            )
            conn.execute(
                "UPDATE dupe_groups SET is_best=1 WHERE image_id=?",
                (members[0]["image_id"],),
            )

    print(f"Ranked {written} images (version {config.RANK_VERSION}).")
    return written


if __name__ == "__main__":
    compute_rankings(force=True)
