"""No-reference IQA ensemble via pyiqa (TOPIQ, CLIP-IQA+, LAION-Aes, MANIQA, NIMA, Q-ReAlign)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import config
from db import db, fetchall, init_db
from progress import get_reporter, track


def _normalize(raw: float, mode: str) -> float:
    if mode == "nima":
        return float(max(0.0, min(10.0, raw)))
    # unit10 / unit01: assume roughly [0,1] → [0,10]; also clamp if already high
    if raw > 1.5:
        return float(max(0.0, min(10.0, raw)))
    return float(max(0.0, min(10.0, raw * 10.0)))


def _release_cuda() -> None:
    try:
        import torch
        import gc

        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


class MetricRunner:
    """Load one pyiqa metric, score images, then unload to free VRAM."""

    def __init__(self, storage_key: str, pyiqa_name: str, task: str | None, mode: str) -> None:
        self.storage_key = storage_key
        self.pyiqa_name = pyiqa_name
        self.task = task
        self.mode = mode
        self.device = config.IQA_DEVICE
        self._metric = None

    def load(self) -> None:
        import torch
        import pyiqa

        try:
            # Q-Align's underlying checkpoint has untied lm_head/embed_tokens
            # weights; transformers otherwise logs a harmless WARNING about it
            # on every load.
            from transformers.utils import logging as hf_logging

            hf_logging.set_verbosity_error()
        except Exception:
            pass

        if self.device == "cuda" and not torch.cuda.is_available():
            get_reporter().warning("iqa", "CUDA unavailable; falling back to CPU for IQA")
            self.device = "cpu"
        self._metric = pyiqa.create_metric(self.pyiqa_name, device=self.device)

    def unload(self) -> None:
        self._metric = None
        _release_cuda()

    def score_one(self, preview_path: Path) -> tuple[float, float]:
        assert self._metric is not None
        path = str(preview_path)
        kwargs: dict[str, Any] = {}
        if self.task:
            kwargs["task_"] = self.task
        try:
            out = self._metric(path, **kwargs) if kwargs else self._metric(path)
        except TypeError:
            # Older pyiqa variants may not accept task_
            out = self._metric(path)
        raw = float(out.item() if hasattr(out, "item") else out)
        return raw, _normalize(raw, self.mode)


def _images_needing_metric(
    metric_key: str,
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
                SELECT i.id, i.path, p.preview_path
                FROM images i
                JOIN previews p ON p.image_id = i.id
                WHERE p.error IS NULL
                ORDER BY i.source_library, i.filename
                """,
            )
        else:
            rows = fetchall(
                conn,
                """
                SELECT i.id, i.path, p.preview_path
                FROM images i
                JOIN previews p ON p.image_id = i.id
                LEFT JOIN iqa_metrics m
                    ON m.image_id = i.id AND m.metric = ?
                WHERE p.error IS NULL
                  AND (m.image_id IS NULL OR m.error IS NOT NULL)
                ORDER BY i.source_library, i.filename
                """,
                (metric_key,),
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


def _upsert_metric(
    image_id: str,
    metric: str,
    raw: float | None,
    score: float | None,
    error: str | None,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with db() as conn:
        conn.execute(
            """
            INSERT INTO iqa_metrics (image_id, metric, raw, score, computed_at, error)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(image_id, metric) DO UPDATE SET
                raw=excluded.raw,
                score=excluded.score,
                computed_at=excluded.computed_at,
                error=excluded.error
            """,
            (image_id, metric, raw, score, now, error),
        )


def _migrate_legacy_iqa() -> int:
    """Copy existing iqa_scores rows into iqa_metrics once."""
    init_db()
    copied = 0
    with db() as conn:
        rows = fetchall(
            conn,
            """
            SELECT image_id, maniqa_raw, maniqa_score, nima_raw, nima_aesthetic, computed_at
            FROM iqa_scores
            WHERE error IS NULL
            """,
        )
        for row in rows:
            for metric, raw, score in (
                ("maniqa", row["maniqa_raw"], row["maniqa_score"]),
                ("nima", row["nima_raw"], row["nima_aesthetic"]),
            ):
                if score is None:
                    continue
                existing = conn.execute(
                    "SELECT 1 FROM iqa_metrics WHERE image_id=? AND metric=?",
                    (row["image_id"], metric),
                ).fetchone()
                if existing:
                    continue
                conn.execute(
                    """
                    INSERT INTO iqa_metrics (image_id, metric, raw, score, computed_at, error)
                    VALUES (?, ?, ?, ?, ?, NULL)
                    """,
                    (row["image_id"], metric, raw, score, row["computed_at"]),
                )
                copied += 1
    if copied:
        get_reporter().warning("iqa", f"Migrated {copied} legacy IQA metric rows.")
    return copied


def compute_iqa(
    *,
    force: bool = False,
    limit: int | None = None,
    path_dirs: list[Path] | None = None,
    metrics: list[str] | None = None,
    recursive: bool = True,
) -> int:
    """
    Score previews with the IQA ensemble. Each metric is resumable independently.
    Returns total successful (image, metric) writes.
    """
    init_db()
    config.ensure_dirs()
    reporter = get_reporter()
    _migrate_legacy_iqa()

    wanted = metrics or [m[0] for m in config.IQA_METRICS]
    registry = {m[0]: m for m in config.IQA_METRICS}
    # Fast metrics first, then heavy qrealign
    order = [k for k in wanted if k in config.IQA_FAST_KEYS] + [
        k for k in wanted if k not in config.IQA_FAST_KEYS
    ]

    total = 0
    reporter.stage_start("iqa", message=f"{len(order)} metrics")
    for key in order:
        if key not in registry:
            reporter.warning("iqa", f"unknown IQA metric {key!r}, skipping")
            continue
        storage_key, pyiqa_name, task, mode = registry[key]
        rows = _images_needing_metric(
            storage_key, force, limit, path_dirs, recursive=recursive
        )
        if not rows:
            reporter.stage_done(
                "iqa",
                ok=0,
                metric=storage_key,
                message=f"IQA {storage_key}: nothing pending",
            )
            continue

        reporter.stage_start(
            "iqa",
            total=len(rows),
            metric=storage_key,
            message=f"IQA {storage_key} ({pyiqa_name}): {len(rows)} images",
        )
        runner = MetricRunner(storage_key, pyiqa_name, task, mode)
        try:
            runner.load()
        except Exception as exc:
            reporter.warning("iqa", f"failed to load {pyiqa_name}: {exc}", metric=storage_key)
            for row in rows:
                _upsert_metric(row["id"], storage_key, None, None, str(exc))
            reporter.stage_done(
                "iqa",
                ok=0,
                failed=len(rows),
                metric=storage_key,
                message=f"IQA {storage_key}: load failed",
            )
            continue

        ok = 0
        failed = 0
        for row in track(
            rows,
            stage="iqa",
            total=len(rows),
            desc=f"IQA:{storage_key}",
            metric=storage_key,
        ):
            try:
                raw, score = runner.score_one(Path(row["preview_path"]))
                _upsert_metric(row["id"], storage_key, raw, score, None)
                ok += 1
                total += 1
            except Exception as exc:
                failed += 1
                _upsert_metric(row["id"], storage_key, None, None, str(exc))
                reporter.warning(
                    "iqa",
                    f"{storage_key} failed for {row['path']}: {exc}",
                    metric=storage_key,
                )
        runner.unload()
        reporter.stage_done(
            "iqa",
            ok=ok,
            failed=failed,
            metric=storage_key,
            message=f"IQA {storage_key}: {ok}/{len(rows)} ok",
        )

    reporter.stage_done(
        "iqa",
        ok=total,
        message=f"IQA ensemble wrote {total} metric values.",
    )
    return total


if __name__ == "__main__":
    compute_iqa(limit=5)
