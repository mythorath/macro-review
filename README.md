# macro-review

Local AI pipeline for culling and ranking **macro / insect / nature** photos. It scores technical quality, aesthetics, subject-aware sharpness, and share-worthiness, then produces a browsable HTML gallery so you can find the shots worth posting.

Designed for Windows + NVIDIA GPU (tested on RTX 5090). Runs fully offline with [Ollama](https://ollama.com/) for vision critique, or optionally OpenAI.

---

## What it does

```text
index → preview → heuristics → IQA ensemble → VLM critique
      → ROI sharpness → burst dedupe → rank → HTML/CSV → crop export
```

| Stage | Purpose |
|-------|---------|
| **Index** | Walk source folders (JPG/TIFF/CR3/DNG), store manifest in SQLite |
| **Preview** | EXIF-rotated ~1024px JPEG cache (RAW via embedded thumb / rawpy) |
| **Heuristics** | Fast OpenCV Laplacian sharpness + exposure clipping |
| **IQA** | Ensemble of no-reference quality/aesthetic models via `pyiqa` |
| **Analyze** | Macro-specific VLM critique (eye focus, DOF, crop boxes, share rec) |
| **ROI** | Full-res sharpness on subject/eye regions + composition metrics |
| **Dedupe** | pHash + EXIF-time burst groups; pick best-of-burst |
| **Rank** | Percentile-calibrated `share_score` with tech/aes/vlm/comp sub-scores |
| **Report** | Filterable HTML gallery + CSV |
| **Crop export** | Full-res crops for top crop-worthy picks |

All stages are **resumable** — interrupt anytime and re-run; already-scored work is skipped unless you pass `--force`.

---

## Requirements

- Windows 10/11 (Linux should work with path tweaks)
- Python 3.11+ (tested on 3.13)
- NVIDIA GPU + recent driver for CUDA PyTorch and local VLM
- [Ollama](https://ollama.com/) with a **vision** model (default: `qwen3.6:35b`), or an OpenAI API key

### Python packages

```powershell
pip install -r requirements.txt

# CUDA PyTorch (RTX 50-series / CUDA 12.8 example — see setup_gpu.md)
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128

pip install pyiqa ImageHash
```

Verify GPU:

```powershell
python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"
```

---

## Quick start

1. **Edit paths** in [`config.py`](config.py):
   - `PROJECT_ROOT` — where cache/report live
   - `SOURCE_DIRS` — your photo libraries
   - `OLLAMA_HOST` / `VISION_MODEL` if needed (env vars also work)

2. **Smoke test** a small batch:

```powershell
cd path\to\macro-review
python main.py run --dir "C:\path\to\your\macro\folder" --limit 10
```

3. Open `report.html` in a browser (uses `file://` links to originals).

4. Full library (hours for VLM on ~1.5k images):

```powershell
python main.py run
```

---

## CLI reference

```text
python main.py <command> [--dir PATH ...] [--limit N] [--force]
```

| Command | Description |
|---------|-------------|
| `index` | Scan folders into SQLite |
| `preview` | Build preview JPEGs |
| `heuristics` | Local sharpness / exposure |
| `iqa` | IQA ensemble (TOPIQ, CLIP-IQA+, MANIQA, LAION-Aes, NIMA, Q-ReAlign, …) |
| `analyze` | VLM macro critique (`--backend ollama\|openai`) |
| `roi` | Full-res eye/subject ROI + composition |
| `dedupe` | Burst / near-duplicate grouping |
| `rank` | Recompute `share_score` (no model calls) |
| `report` | Rebuild HTML + CSV |
| `crop-export` | Export suggested crops (`--threshold`, `--limit`) |
| `run` | Chain all stages |

### Useful patterns

```powershell
# Only score a specific folder
python main.py run --dir "\\NAS\Photos\MACRO" --limit 50

# Re-blend rankings after editing weights in config.py
python main.py rank --force
python main.py report

# IQA only (GPU, no Ollama needed)
python main.py iqa --dir "D:\macros"

# Force re-analyze with new prompt version
python main.py analyze --force --limit 20
```

`--dir` is repeatable and scopes every stage to those paths. With `--limit`, `run` aligns IQA/VLM/ROI to the same filename-ordered set.

---

## Scoring model

### IQA ensemble (`pyiqa`)

Stored per image in `iqa_metrics`:

| Metric | Role |
|--------|------|
| `topiq_nr` | Technical / perceptual quality |
| `clipiqa+` | CLIP-based quality |
| `maniqa` | Modern NR-IQA |
| `laion_aes` | Fast CLIP aesthetic |
| `topiq_iaa` | Aesthetic assessment |
| `nima` | Classic AVA aesthetic |
| `qrealign_quality` / `qrealign_aesthetic` | Q-Align-style VLM judge (`qrealign-lite` by default; set `QREALIGN_VARIANT=qrealign-pro` for the 9B model) |

Models load sequentially and release VRAM between groups so they don’t fight Ollama.

### VLM critique (`macro_v3`)

Qwen (or OpenAI) returns: overall / sharpness / composition, eye focus, DOF, background, lighting, pose, distractions, share recommendation (`skip` | `maybe` | `share` | `portfolio`), crop suggestion, **`subject_box`** and **`eye_box`** (normalized rectangles for ROI).

### ROI sharpness

On the full-resolution original (or RAW decode): Laplacian, Tenengrad, and FFT energy inside the eye/subject boxes vs background — plus rule-of-thirds distance, subject size, edge-cut, and background clutter.

### `share_score` (blend_v2)

Components are **percentile-ranked within your library** (0–10), then blended:

| Sub-score | Default mix |
|-----------|-------------|
| **tech** (35%) | TOPIQ-NR, CLIP-IQA+, MANIQA, Q-ReAlign quality, eye ROI sharpness |
| **aes** (25%) | Q-ReAlign aesthetic, TOPIQ-IAA, LAION-Aes, NIMA |
| **vlm** (25%) | overall, eye_focus, pose |
| **comp** (15%) | VLM composition, thirds, bg separation, clutter |

Edit weights in `config.py` (`TECH_WEIGHTS`, `AES_WEIGHTS`, `VLM_WEIGHTS`, `COMP_WEIGHTS`, `SHARE_WEIGHTS_V2`), then:

```powershell
python main.py rank --force
python main.py report
```

---

## Outputs

| Path | Contents |
|------|----------|
| `cache/review.db` | SQLite state (images, scores, metrics, rankings) |
| `cache/previews/` | Cached preview JPEGs |
| `report.html` | Interactive gallery (share score, sub-score bars, best-of-burst, filters) |
| `results.csv` | Flat export for spreadsheets |
| `suggested_crops/` | Full-res crop JPEGs |
| `logs/` | Analyze error log |

These are gitignored — regenerate anytime with the CLI.

---

## Configuration cheatsheet

| Setting | Default | Notes |
|---------|---------|-------|
| `OLLAMA_HOST` | `http://localhost:11435` | `0.0.0.0` is rewritten to `localhost` |
| `VISION_MODEL` | `qwen3.6:35b` | Must support vision |
| `BACKEND` | `ollama` | or `openai` + `OPENAI_API_KEY` |
| `IQA_DEVICE` | `cuda` | |
| `QREALIGN_VARIANT` | `qrealign-lite` | or `qrealign-pro` |
| `PROMPT_VERSION` | `macro_v3` | Bump to force VLM re-score |
| `CROP_SCORE_THRESHOLD` | `7.0` | Min share/overall for crop export |
| `PHASH_HAMMING_MAX` | `10` | Near-dupe sensitivity |
| `BURST_GAP_SECONDS` | `20` | Max EXIF time gap for bursts |

---

## Project layout

```text
main.py              CLI entry
config.py            Paths, models, blend weights
db.py                SQLite schema + migrations
indexer.py           Folder scan
previews.py          Preview cache
heuristics.py        OpenCV pre-pass
iqa.py               pyiqa ensemble
vision_backend.py    Ollama / OpenAI VLM
analyze.py           VLM scoring loop
roi_sharpness.py     Full-res ROI + composition
dedupe.py            pHash / burst groups
rank.py              Percentile blend_v2
report.py            HTML + CSV
crop_export.py       Crop writer
image_io.py          Shared full-res / RAW loader
setup_gpu.md         CUDA torch install notes
```

---

## Tips

- **VRAM**: Run `iqa` when large Ollama models are unloaded if you hit OOM. Fast IQA metrics are small; `qrealign-lite` needs several GB.
- **NAS / OneDrive**: Preview + ROI read originals; cloud-only placeholders may fail and are logged/skipped.
- **CR3 + DNG pairs**: Same stem are linked in dedupe groups so you don’t double-count.
- **Calibration**: After reviewing the gallery, tweak blend weights rather than re-running models — `rank --force` is seconds.

---

## License

Private project. All rights reserved unless otherwise noted.
