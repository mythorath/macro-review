"""Burst / near-duplicate grouping via perceptual hash + capture time + stem pairs."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ExifTags

import config
from db import db, fetchall, init_db
from progress import get_reporter, track

try:
    import imagehash
except ImportError:  # pragma: no cover
    imagehash = None  # type: ignore


def _exif_datetime(path: Path) -> float | None:
    try:
        with Image.open(path) as img:
            exif = img.getexif()
            if not exif:
                return None
            # Prefer DateTimeOriginal (36867)
            for tag_id, value in exif.items():
                name = ExifTags.TAGS.get(tag_id, str(tag_id))
                if name in ("DateTimeOriginal", "DateTime"):
                    try:
                        dt = datetime.strptime(str(value), "%Y:%m:%d %H:%M:%S")
                        return dt.replace(tzinfo=timezone.utc).timestamp()
                    except ValueError:
                        continue
    except Exception:
        return None
    return None


def _phash_hex(preview_path: Path) -> str:
    if imagehash is None:
        raise RuntimeError("imagehash is not installed")
    with Image.open(preview_path) as img:
        return str(imagehash.phash(img.convert("RGB")))


def _hamming(a: str, b: str) -> int:
    # imagehash hex strings — convert via imagehash if available
    if imagehash is not None:
        try:
            return imagehash.hex_to_hash(a) - imagehash.hex_to_hash(b)
        except Exception:
            pass
    # Fallback: bit count on int
    try:
        return bin(int(a, 16) ^ int(b, 16)).count("1")
    except ValueError:
        return 64


class UnionFind:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def add(self, x: str) -> None:
        self.parent.setdefault(x, x)

    def find(self, x: str) -> str:
        self.add(x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def compute_dedupe(
    *,
    force: bool = False,
    limit: int | None = None,
    path_dirs: list[Path] | None = None,
    recursive: bool = True,
) -> int:
    """Group near-duplicates / bursts. Returns number of images grouped."""
    init_db()
    reporter = get_reporter()
    if imagehash is None:
        raise RuntimeError("pip install imagehash is required for dedupe")

    with db() as conn:
        rows = fetchall(
            conn,
            """
            SELECT i.id, i.path, i.stem, i.extension, i.mtime, p.preview_path,
                   d.phash, d.group_id
            FROM images i
            JOIN previews p ON p.image_id = i.id
            LEFT JOIN dupe_groups d ON d.image_id = i.id
            WHERE p.error IS NULL
            ORDER BY i.source_library, i.filename
            """,
        )
    if path_dirs:
        rows = [
            r
            for r in rows
            if config.path_under_dirs(r["path"], path_dirs, recursive=recursive)
        ]
    if limit is not None and force:
        rows = rows[:limit]

    reporter.stage_start("dedupe", total=len(rows))
    # Compute missing hashes
    records: list[dict] = []
    failed = 0
    for row in track(rows, stage="dedupe", total=len(rows), desc="pHash"):
        image_id = row["id"]
        phash = row["phash"]
        capture_ts = None
        if force or not phash:
            try:
                phash = _phash_hex(Path(row["preview_path"]))
            except Exception as exc:
                failed += 1
                reporter.warning("dedupe", f"phash failed for {row['path']}: {exc}")
                continue
            capture_ts = _exif_datetime(Path(row["path"]))
            if capture_ts is None:
                capture_ts = float(row["mtime"])
        else:
            # Reuse existing; still need capture_ts from DB or recompute lightly
            capture_ts = _exif_datetime(Path(row["path"])) or float(row["mtime"])
        records.append(
            {
                "id": image_id,
                "path": row["path"],
                "stem": row["stem"],
                "extension": row["extension"],
                "phash": phash,
                "capture_ts": capture_ts,
            }
        )

    if not records:
        reporter.stage_done("dedupe", ok=0, failed=failed, message="No images to group.")
        return 0

    uf = UnionFind()
    for r in records:
        uf.add(r["id"])

    # Same-stem pairs (CR3/DNG/JPG)
    by_stem: dict[str, list[str]] = {}
    for r in records:
        by_stem.setdefault(r["stem"], []).append(r["id"])
    for ids in by_stem.values():
        for other in ids[1:]:
            uf.union(ids[0], other)

    # Burst grouping: sort by time, union nearby similar hashes
    timed = sorted(records, key=lambda r: r["capture_ts"] or 0.0)
    for i, a in enumerate(timed):
        for b in timed[i + 1 : i + 40]:  # local window
            gap = abs((b["capture_ts"] or 0) - (a["capture_ts"] or 0))
            if gap > config.BURST_GAP_SECONDS:
                break
            if _hamming(a["phash"], b["phash"]) <= config.PHASH_HAMMING_MAX:
                uf.union(a["id"], b["id"])

    # Also hash-only near-dups without time (across library) — limited pass
    # Group by first 8 hex chars for candidate buckets
    buckets: dict[str, list[dict]] = {}
    for r in records:
        buckets.setdefault(r["phash"][:8], []).append(r)
    for items in buckets.values():
        for i, a in enumerate(items):
            for b in items[i + 1 :]:
                if _hamming(a["phash"], b["phash"]) <= config.PHASH_HAMMING_MAX:
                    uf.union(a["id"], b["id"])

    now = datetime.now(timezone.utc).isoformat()
    # Assign stable group ids from root
    roots: dict[str, str] = {}
    for r in records:
        root = uf.find(r["id"])
        if root not in roots:
            roots[root] = hashlib.sha1(root.encode()).hexdigest()[:16]

    with db() as conn:
        for r in records:
            gid = roots[uf.find(r["id"])]
            conn.execute(
                """
                INSERT INTO dupe_groups (image_id, group_id, phash, capture_ts, is_best, grouped_at)
                VALUES (?, ?, ?, ?, 0, ?)
                ON CONFLICT(image_id) DO UPDATE SET
                    group_id=excluded.group_id,
                    phash=excluded.phash,
                    capture_ts=excluded.capture_ts,
                    grouped_at=excluded.grouped_at
                """,
                (r["id"], gid, r["phash"], r["capture_ts"], now),
            )

    # Mark best by share_score (or overall_score fallback)
    with db() as conn:
        groups = fetchall(conn, "SELECT DISTINCT group_id FROM dupe_groups")
        for g in groups:
            members = fetchall(
                conn,
                """
                SELECT d.image_id,
                       COALESCE(r.share_score, s.overall_score, 0) AS score
                FROM dupe_groups d
                LEFT JOIN rankings r ON r.image_id = d.image_id
                LEFT JOIN scores s ON s.image_id = d.image_id
                WHERE d.group_id = ?
                ORDER BY score DESC, d.image_id
                """,
                (g["group_id"],),
            )
            if not members:
                continue
            best_id = members[0]["image_id"]
            conn.execute(
                "UPDATE dupe_groups SET is_best=0 WHERE group_id=?",
                (g["group_id"],),
            )
            conn.execute(
                "UPDATE dupe_groups SET is_best=1 WHERE image_id=?",
                (best_id,),
            )

    reporter.stage_done(
        "dedupe",
        ok=len(records),
        failed=failed,
        message=f"Grouped {len(records)} images into {len(roots)} dupe/burst groups.",
    )
    return len(records)


if __name__ == "__main__":
    compute_dedupe()
