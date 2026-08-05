"""SQLite persistence for the photo review pipeline."""

from __future__ import annotations

import hashlib
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator, Iterable

import config

# Extra columns added to scores after the initial schema.
_SCORES_EXTRA_COLUMNS: dict[str, str] = {
    "eye_focus_score": "REAL",
    "dof_quality": "REAL",
    "background_score": "REAL",
    "lighting_score": "REAL",
    "distractions": "TEXT",
    "pose_score": "REAL",
    "share_recommendation": "TEXT",
    "prompt_version": "TEXT",
    "subject_box": "TEXT",
    "eye_box": "TEXT",
}

_RANKINGS_EXTRA_COLUMNS: dict[str, str] = {
    "tech_score_c": "REAL",
    "aes_score_c": "REAL",
    "vlm_score_c": "REAL",
    "comp_score_c": "REAL",
}


def path_id(path: str | Path) -> str:
    """Stable id from absolute path (case-normalized for Windows)."""
    normalized = str(Path(path)).replace("/", "\\").lower()
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()


def connect() -> sqlite3.Connection:
    config.ensure_dirs()
    conn = sqlite3.connect(str(config.DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def db() -> Generator[sqlite3.Connection, None, None]:
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


SCHEMA = """
CREATE TABLE IF NOT EXISTS images (
    id TEXT PRIMARY KEY,
    path TEXT NOT NULL UNIQUE,
    source_library TEXT NOT NULL,
    filename TEXT NOT NULL,
    stem TEXT NOT NULL,
    extension TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    mtime REAL NOT NULL,
    indexed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS previews (
    image_id TEXT PRIMARY KEY REFERENCES images(id) ON DELETE CASCADE,
    preview_path TEXT NOT NULL,
    width INTEGER,
    height INTEGER,
    generated_at TEXT NOT NULL,
    error TEXT
);

CREATE TABLE IF NOT EXISTS heuristics (
    image_id TEXT PRIMARY KEY REFERENCES images(id) ON DELETE CASCADE,
    sharpness REAL,
    overexpose_pct REAL,
    underexpose_pct REAL,
    tech_score REAL,
    computed_at TEXT NOT NULL,
    error TEXT
);

CREATE TABLE IF NOT EXISTS scores (
    image_id TEXT PRIMARY KEY REFERENCES images(id) ON DELETE CASCADE,
    overall_score REAL,
    sharpness_score REAL,
    composition_score REAL,
    subject TEXT,
    crop_worthy INTEGER,
    crop_box TEXT,
    crop_reason TEXT,
    comment TEXT,
    model TEXT,
    backend TEXT,
    raw_response TEXT,
    scored_at TEXT NOT NULL,
    error TEXT,
    eye_focus_score REAL,
    dof_quality REAL,
    background_score REAL,
    lighting_score REAL,
    distractions TEXT,
    pose_score REAL,
    share_recommendation TEXT,
    prompt_version TEXT,
    subject_box TEXT,
    eye_box TEXT
);

CREATE TABLE IF NOT EXISTS iqa_scores (
    image_id TEXT PRIMARY KEY REFERENCES images(id) ON DELETE CASCADE,
    maniqa_raw REAL,
    maniqa_score REAL,
    nima_raw REAL,
    nima_aesthetic REAL,
    computed_at TEXT NOT NULL,
    error TEXT
);

CREATE TABLE IF NOT EXISTS iqa_metrics (
    image_id TEXT NOT NULL REFERENCES images(id) ON DELETE CASCADE,
    metric TEXT NOT NULL,
    raw REAL,
    score REAL,
    computed_at TEXT NOT NULL,
    error TEXT,
    PRIMARY KEY (image_id, metric)
);

CREATE TABLE IF NOT EXISTS roi_metrics (
    image_id TEXT PRIMARY KEY REFERENCES images(id) ON DELETE CASCADE,
    eye_laplacian REAL,
    eye_tenengrad REAL,
    eye_fft REAL,
    subject_laplacian REAL,
    subject_tenengrad REAL,
    subject_fft REAL,
    bg_laplacian REAL,
    bg_tenengrad REAL,
    bg_fft REAL,
    eye_sharpness REAL,
    eye_vs_subject_ratio REAL,
    subject_bg_separation REAL,
    motion_blur_flag INTEGER,
    thirds_distance REAL,
    subject_size_frac REAL,
    edge_cut INTEGER,
    bg_clutter REAL,
    subject_box TEXT,
    eye_box TEXT,
    source TEXT,
    computed_at TEXT NOT NULL,
    error TEXT
);

CREATE TABLE IF NOT EXISTS dupe_groups (
    image_id TEXT PRIMARY KEY REFERENCES images(id) ON DELETE CASCADE,
    group_id TEXT NOT NULL,
    phash TEXT,
    capture_ts REAL,
    is_best INTEGER DEFAULT 0,
    grouped_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rankings (
    image_id TEXT PRIMARY KEY REFERENCES images(id) ON DELETE CASCADE,
    share_score REAL,
    rank_version TEXT,
    components_json TEXT,
    ranked_at TEXT NOT NULL,
    tech_score_c REAL,
    aes_score_c REAL,
    vlm_score_c REAL,
    comp_score_c REAL
);

CREATE TABLE IF NOT EXISTS crop_exports (
    image_id TEXT PRIMARY KEY REFERENCES images(id) ON DELETE CASCADE,
    export_path TEXT NOT NULL,
    exported_at TEXT NOT NULL,
    error TEXT
);

CREATE INDEX IF NOT EXISTS idx_images_source ON images(source_library);
CREATE INDEX IF NOT EXISTS idx_images_stem ON images(stem);
CREATE INDEX IF NOT EXISTS idx_scores_overall ON scores(overall_score);
CREATE INDEX IF NOT EXISTS idx_rankings_share ON rankings(share_score);
CREATE INDEX IF NOT EXISTS idx_iqa_metrics_metric ON iqa_metrics(metric);
CREATE INDEX IF NOT EXISTS idx_dupe_groups_gid ON dupe_groups(group_id);
"""


def _migrate_table_columns(
    conn: sqlite3.Connection,
    table: str,
    columns: dict[str, str],
) -> None:
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    for name, col_type in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {col_type}")


def init_db() -> None:
    with db() as conn:
        conn.executescript(SCHEMA)
        _migrate_table_columns(conn, "scores", _SCORES_EXTRA_COLUMNS)
        _migrate_table_columns(conn, "rankings", _RANKINGS_EXTRA_COLUMNS)


def upsert_image(conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO images (id, path, source_library, filename, stem, extension,
                            size_bytes, mtime, indexed_at)
        VALUES (:id, :path, :source_library, :filename, :stem, :extension,
                :size_bytes, :mtime, :indexed_at)
        ON CONFLICT(id) DO UPDATE SET
            path=excluded.path,
            source_library=excluded.source_library,
            filename=excluded.filename,
            stem=excluded.stem,
            extension=excluded.extension,
            size_bytes=excluded.size_bytes,
            mtime=excluded.mtime,
            indexed_at=excluded.indexed_at
        """,
        row,
    )


def fetchall(conn: sqlite3.Connection, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
    return list(conn.execute(sql, tuple(params)))


def fetchone(conn: sqlite3.Connection, sql: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
    return conn.execute(sql, tuple(params)).fetchone()
