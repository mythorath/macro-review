"""HTML gallery + CSV export of scored photos (v3: sub-scores + burst collapse)."""

from __future__ import annotations

import csv
import html
import json
from pathlib import Path

import config
from db import db, fetchall, init_db


def _fmt(value, digits: int = 1) -> str:
    if value is None:
        return "-"
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return "-"


def _bar(value, label: str) -> str:
    if value is None:
        pct = 0
        text = "-"
    else:
        pct = max(0, min(100, float(value) * 10))
        text = f"{float(value):.1f}"
    return (
        f'<div class="bar-row"><span class="bar-label">{html.escape(label)}</span>'
        f'<div class="bar"><div class="bar-fill" style="width:{pct:.0f}%"></div></div>'
        f'<span class="bar-val">{text}</span></div>'
    )


def _load_scored_rows(path_dirs: list[Path] | None = None) -> list:
    init_db()
    with db() as conn:
        rows = fetchall(
            conn,
            """
            SELECT
                i.id, i.path, i.source_library, i.filename, i.extension,
                p.preview_path, p.width, p.height,
                h.sharpness, h.tech_score, h.overexpose_pct, h.underexpose_pct,
                s.overall_score, s.sharpness_score, s.composition_score,
                s.eye_focus_score, s.dof_quality, s.background_score,
                s.lighting_score, s.pose_score, s.distractions,
                s.share_recommendation, s.prompt_version,
                s.subject, s.crop_worthy, s.crop_box, s.crop_reason, s.comment,
                s.subject_box, s.eye_box,
                s.model, s.backend, s.scored_at,
                r.share_score, r.rank_version,
                r.tech_score_c, r.aes_score_c, r.vlm_score_c, r.comp_score_c,
                roi.eye_sharpness, roi.subject_bg_separation, roi.thirds_distance,
                roi.bg_clutter, roi.motion_blur_flag,
                d.group_id, d.is_best,
                (SELECT COUNT(*) FROM dupe_groups d2 WHERE d2.group_id = d.group_id) AS group_size
            FROM images i
            LEFT JOIN scores s ON s.image_id = i.id AND s.error IS NULL
            LEFT JOIN previews p ON p.image_id = i.id
            LEFT JOIN heuristics h ON h.image_id = i.id
            LEFT JOIN rankings r ON r.image_id = i.id
            LEFT JOIN roi_metrics roi ON roi.image_id = i.id
            LEFT JOIN dupe_groups d ON d.image_id = i.id
            WHERE (s.overall_score IS NOT NULL OR r.share_score IS NOT NULL)
            ORDER BY
                CASE WHEN r.share_score IS NULL THEN 1 ELSE 0 END,
                r.share_score DESC,
                s.overall_score DESC,
                i.filename ASC
            """,
        )
        # Pivot IQA metrics
        metrics = fetchall(
            conn,
            "SELECT image_id, metric, score FROM iqa_metrics WHERE error IS NULL",
        )
    metric_map: dict[str, dict[str, float]] = {}
    for m in metrics:
        metric_map.setdefault(m["image_id"], {})[m["metric"]] = m["score"]

    # Convert rows to dicts with metric columns
    enriched = []
    for row in rows:
        d = dict(row)
        for k, v in metric_map.get(row["id"], {}).items():
            d[k] = v
        enriched.append(d)

    if path_dirs:
        enriched = [r for r in enriched if config.path_under_dirs(r["path"], path_dirs)]
    return enriched


def write_csv(rows: list | None = None) -> Path:
    rows = rows if rows is not None else _load_scored_rows()
    config.ensure_dirs()
    fieldnames = [
        "id", "path", "source_library", "filename",
        "share_score", "tech_score_c", "aes_score_c", "vlm_score_c", "comp_score_c",
        "topiq_nr", "clipiqa+", "maniqa", "laion_aes", "topiq_iaa", "nima",
        "qrealign_quality", "qrealign_aesthetic",
        "overall_score", "eye_focus_score", "dof_quality", "background_score",
        "lighting_score", "pose_score", "sharpness_score", "composition_score",
        "eye_sharpness", "subject_bg_separation", "thirds_distance", "bg_clutter",
        "share_recommendation", "subject", "distractions",
        "crop_worthy", "crop_box", "crop_reason", "comment",
        "group_id", "is_best", "group_size",
        "prompt_version", "model", "backend", "preview_path",
    ]
    with config.CSV_PATH.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k) for k in fieldnames})
    print(f"Wrote {config.CSV_PATH}")
    return config.CSV_PATH


def _file_uri(path: str | Path) -> str:
    return Path(path).resolve().as_uri()


def write_html(rows: list | None = None) -> Path:
    rows = rows if rows is not None else _load_scored_rows()
    config.ensure_dirs()

    # Top picks among best-of-burst (or ungrouped)
    top_pool = [r for r in rows if r.get("is_best") in (1, None) or (r.get("group_size") or 1) <= 1]
    if not top_pool:
        top_pool = rows
    top_n = min(20, len(top_pool))
    top_links = []
    for idx, row in enumerate(top_pool[:top_n], start=1):
        sid = html.escape(row["id"])
        label = html.escape((row.get("subject") or row["filename"] or "")[:40])
        score = _fmt(row.get("share_score") if row.get("share_score") is not None else row.get("overall_score"))
        top_links.append(f'<a class="top-chip" href="#card-{sid}">#{idx} {score} · {label}</a>')

    cards: list[str] = []
    libraries = sorted({r["source_library"] for r in rows})
    for row in rows:
        preview = row.get("preview_path") or ""
        preview_uri = _file_uri(preview) if preview and Path(preview).exists() else ""
        original_uri = _file_uri(row["path"])
        crop_box = None
        if row.get("crop_box"):
            try:
                crop_box = json.loads(row["crop_box"])
            except json.JSONDecodeError:
                crop_box = None

        overlay = ""
        if crop_box and len(crop_box) == 4:
            x0, y0, x1, y1 = crop_box
            overlay = (
                f'<div class="crop-box" style="left:{x0*100:.2f}%;top:{y0*100:.2f}%;'
                f'width:{(x1-x0)*100:.2f}%;height:{(y1-y0)*100:.2f}%;"></div>'
            )

        subject = html.escape(row.get("subject") or "")
        comment = html.escape(row.get("comment") or "")
        crop_reason = html.escape(row.get("crop_reason") or "")
        distractions = html.escape(row.get("distractions") or "")
        filename = html.escape(row["filename"])
        library = html.escape(row["source_library"])
        crop_worthy = "1" if row.get("crop_worthy") else "0"
        share = float(row["share_score"]) if row.get("share_score") is not None else None
        overall = float(row["overall_score"]) if row.get("overall_score") is not None else 0.0
        badge = share if share is not None else overall
        rec = html.escape(row.get("share_recommendation") or "")
        sid = html.escape(row["id"])
        gid = html.escape(row.get("group_id") or "")
        gsize = int(row.get("group_size") or 1)
        is_best = 1 if row.get("is_best") in (1, True) or gsize <= 1 else 0
        if row.get("is_best") is None and gsize > 1:
            is_best = 0
        if row.get("group_id") is None:
            is_best = 1

        burst_badge = ""
        if gsize > 1:
            burst_badge = f'<div class="burst-badge">+{gsize - 1} similar</div>'

        bars = (
            _bar(row.get("tech_score_c"), "tech")
            + _bar(row.get("aes_score_c"), "aes")
            + _bar(row.get("vlm_score_c"), "vlm")
            + _bar(row.get("comp_score_c"), "comp")
        )

        cards.append(
            f"""
<article class="card" id="card-{sid}" data-score="{badge:.1f}" data-share="{_fmt(share)}"
         data-library="{library}" data-crop="{crop_worthy}"
         data-subject="{(row.get('subject') or '').lower()}" data-rec="{rec}"
         data-best="{is_best}" data-group="{gid}"
         data-tech="{_fmt(row.get('tech_score_c'))}" data-aes="{_fmt(row.get('aes_score_c'))}">
  <div class="thumb-wrap">
    <img src="{html.escape(preview_uri)}" alt="{filename}" loading="lazy"/>
    {overlay}
    <div class="score-badge">{badge:.1f}</div>
    {burst_badge}
  </div>
  <div class="meta">
    <h3>{filename}</h3>
    <div class="bars">{bars}</div>
    <div class="scores">
      MANIQA {_fmt(row.get('maniqa'))} · TOPIQ {_fmt(row.get('topiq_nr'))} ·
      CLIP {_fmt(row.get('clipiqa+'))} · QRe {_fmt(row.get('qrealign_quality'))}/{_fmt(row.get('qrealign_aesthetic'))} ·
      eyeROI {_fmt(row.get('eye_sharpness'))}
    </div>
    <div class="subject"><strong>{subject}</strong> · {library}
      {(' · <span class="rec">' + rec + '</span>') if rec else ''}
    </div>
    {"<p class='distractions'><em>Distractions:</em> " + distractions + "</p>" if distractions else ""}
    <p class="comment">{comment}</p>
    {"<p class='crop-reason'><em>Crop:</em> " + crop_reason + "</p>" if crop_reason else ""}
    <a href="{html.escape(original_uri)}" target="_blank" rel="noopener">Open original</a>
  </div>
</article>
"""
        )

    lib_options = "\n".join(
        f'<option value="{html.escape(lib)}">{html.escape(lib)}</option>' for lib in libraries
    )

    page = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Macro Photo Review v3</title>
<style>
  :root {{
    --bg: #0f1115; --card: #1a1f29; --text: #e8ecf1; --muted: #9aa3b2;
    --accent: #5eead4; --crop: #f59e0b; --bar: #334155;
  }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; font-family: "Segoe UI", system-ui, sans-serif; background: var(--bg); color: var(--text); }}
  header {{
    position: sticky; top: 0; z-index: 10;
    background: rgba(15,17,21,0.95); backdrop-filter: blur(8px);
    border-bottom: 1px solid #2a3140; padding: 1rem 1.5rem;
  }}
  h1 {{ margin: 0 0 0.5rem; font-size: 1.25rem; }}
  .top-strip {{ display: flex; flex-wrap: wrap; gap: 0.4rem; margin-bottom: 0.75rem; }}
  .top-chip {{
    font-size: 0.75rem; color: var(--accent); text-decoration: none;
    border: 1px solid #2a3140; border-radius: 999px; padding: 0.2rem 0.55rem; background: #12161e;
  }}
  .controls {{ display: flex; flex-wrap: wrap; gap: 0.75rem; align-items: center; }}
  label {{ color: var(--muted); font-size: 0.85rem; display: flex; gap: 0.4rem; align-items: center; }}
  input, select {{
    background: #12161e; color: var(--text); border: 1px solid #2a3140;
    border-radius: 6px; padding: 0.35rem 0.5rem;
  }}
  #stats {{ color: var(--muted); font-size: 0.85rem; margin-left: auto; }}
  main {{
    display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
    gap: 1rem; padding: 1.25rem;
  }}
  .card {{
    background: var(--card); border-radius: 12px; overflow: hidden;
    border: 1px solid #2a3140; display: flex; flex-direction: column;
  }}
  .card.hidden {{ display: none; }}
  .thumb-wrap {{ position: relative; background: #000; aspect-ratio: 4/3; }}
  .thumb-wrap img {{ width: 100%; height: 100%; object-fit: contain; display: block; }}
  .crop-box {{
    position: absolute; border: 2px solid var(--crop);
    box-shadow: 0 0 0 9999px rgba(0,0,0,0.35); pointer-events: none;
  }}
  .score-badge {{
    position: absolute; top: 0.5rem; right: 0.5rem;
    background: rgba(0,0,0,0.75); color: var(--accent);
    font-weight: 700; padding: 0.25rem 0.55rem; border-radius: 999px;
  }}
  .burst-badge {{
    position: absolute; top: 0.5rem; left: 0.5rem;
    background: rgba(245,158,11,0.9); color: #111; font-size: 0.75rem;
    font-weight: 700; padding: 0.2rem 0.45rem; border-radius: 999px;
  }}
  .meta {{ padding: 0.85rem 1rem 1rem; }}
  .meta h3 {{ margin: 0 0 0.35rem; font-size: 0.95rem; word-break: break-all; }}
  .bars {{ margin: 0.4rem 0 0.6rem; }}
  .bar-row {{ display: grid; grid-template-columns: 42px 1fr 28px; gap: 0.35rem; align-items: center; margin: 0.15rem 0; }}
  .bar-label {{ color: var(--muted); font-size: 0.7rem; text-transform: uppercase; }}
  .bar {{ background: var(--bar); height: 6px; border-radius: 3px; overflow: hidden; }}
  .bar-fill {{ background: var(--accent); height: 100%; }}
  .bar-val {{ font-size: 0.7rem; color: var(--muted); text-align: right; }}
  .scores, .subject {{ color: var(--muted); font-size: 0.8rem; margin-bottom: 0.35rem; }}
  .rec {{ color: var(--accent); text-transform: uppercase; font-size: 0.75rem; }}
  .comment, .crop-reason, .distractions {{ font-size: 0.85rem; line-height: 1.35; margin: 0.4rem 0; }}
  .crop-reason {{ color: var(--crop); }}
  .distractions {{ color: #fca5a5; }}
  a {{ color: var(--accent); }}
</style>
</head>
<body>
<header>
  <h1>Macro Photo Review v3 — {len(rows)} images</h1>
  <div class="top-strip">
    <strong style="color:var(--muted);font-size:0.8rem;align-self:center;">Top {top_n} share picks:</strong>
    {''.join(top_links) if top_links else '<span style="color:var(--muted);font-size:0.8rem;">none yet</span>'}
  </div>
  <div class="controls">
    <label>Min share <input type="number" id="minScore" min="0" max="10" step="0.5" value="0"/></label>
    <label>Min tech <input type="number" id="minTech" min="0" max="10" step="0.5" value="0"/></label>
    <label>Library
      <select id="library"><option value="">All</option>{lib_options}</select>
    </label>
    <label><input type="checkbox" id="cropOnly"/> Crop-worthy</label>
    <label><input type="checkbox" id="bestOnly" checked/> Best-of-burst only</label>
    <label>Rec
      <select id="rec">
        <option value="">All</option>
        <option value="portfolio">portfolio</option>
        <option value="share">share</option>
        <option value="maybe">maybe</option>
        <option value="skip">skip</option>
      </select>
    </label>
    <label>Subject <input type="search" id="subject" placeholder="bee, bird…"/></label>
    <label>Sort
      <select id="sort">
        <option value="score-desc">Share ↓</option>
        <option value="score-asc">Share ↑</option>
        <option value="tech-desc">Tech ↓</option>
        <option value="name">Name</option>
      </select>
    </label>
    <span id="stats"></span>
  </div>
</header>
<main id="gallery">
{''.join(cards)}
</main>
<script>
const gallery = document.getElementById('gallery');
const cards = Array.from(gallery.querySelectorAll('.card'));
const minScore = document.getElementById('minScore');
const minTech = document.getElementById('minTech');
const library = document.getElementById('library');
const cropOnly = document.getElementById('cropOnly');
const bestOnly = document.getElementById('bestOnly');
const subject = document.getElementById('subject');
const rec = document.getElementById('rec');
const sort = document.getElementById('sort');
const stats = document.getElementById('stats');

function apply() {{
  const min = parseFloat(minScore.value) || 0;
  const mt = parseFloat(minTech.value) || 0;
  const lib = library.value;
  const crop = cropOnly.checked;
  const best = bestOnly.checked;
  const q = (subject.value || '').trim().toLowerCase();
  const r = rec.value;
  let visible = 0;
  cards.forEach(card => {{
    const score = parseFloat(card.dataset.score) || 0;
    const tech = parseFloat(card.dataset.tech);
    const okScore = score >= min;
    const okTech = isNaN(tech) || tech >= mt;
    const okLib = !lib || card.dataset.library === lib;
    const okCrop = !crop || card.dataset.crop === '1';
    const okBest = !best || card.dataset.best === '1';
    const okSub = !q || (card.dataset.subject || '').includes(q);
    const okRec = !r || card.dataset.rec === r;
    const show = okScore && okTech && okLib && okCrop && okBest && okSub && okRec;
    card.classList.toggle('hidden', !show);
    if (show) visible++;
  }});
  stats.textContent = visible + ' shown';
}}

function resort() {{
  const mode = sort.value;
  const sorted = cards.slice().sort((a, b) => {{
    if (mode === 'score-asc') return (parseFloat(a.dataset.score)||0) - (parseFloat(b.dataset.score)||0);
    if (mode === 'tech-desc') return (parseFloat(b.dataset.tech)||0) - (parseFloat(a.dataset.tech)||0);
    if (mode === 'name') return a.querySelector('h3').textContent.localeCompare(b.querySelector('h3').textContent);
    return (parseFloat(b.dataset.score)||0) - (parseFloat(a.dataset.score)||0);
  }});
  sorted.forEach(c => gallery.appendChild(c));
  apply();
}}

[minScore, minTech, library, cropOnly, bestOnly, subject, rec].forEach(el => el.addEventListener('input', apply));
sort.addEventListener('change', resort);
apply();
</script>
</body>
</html>
"""
    config.REPORT_PATH.write_text(page, encoding="utf-8")
    print(f"Wrote {config.REPORT_PATH}")
    return config.REPORT_PATH


def build_report(path_dirs: list[Path] | None = None) -> tuple[Path, Path]:
    rows = _load_scored_rows(path_dirs=path_dirs)
    html_path = write_html(rows)
    csv_path = write_csv(rows)
    return html_path, csv_path


if __name__ == "__main__":
    build_report()
