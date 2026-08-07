# GPU setup (Windows)

PyTorch and `pyiqa` power the IQA scoring stage. Ollama (or OpenAI) powers the VLM critique stage.

## Preferred path (managed venv)

```powershell
python main.py doctor              # inspect current interpreter
python main.py setup --yes         # create AppData venv + install + Ollama/model
python main.py doctor --pipeline   # verify the managed venv
```

`setup` installs into `%LOCALAPPDATA%\MacroReview\venv` using Phase 1 recommendations (`cu128` on NVIDIA, CPU wheels otherwise). It writes `pipeline_python` into settings.

Useful flags:

```powershell
python main.py setup --dry-run
python main.py setup --yes --skip-ollama --skip-model
python main.py setup --yes --force-recreate-venv
```

## Manual install (fallback)

### NVIDIA

```powershell
python -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128
python -m pip install pyiqa ImageHash
```

Verify:

```powershell
python -c "import torch; print(torch.__version__, torch.cuda.is_available(), torch.cuda.get_device_name(0))"
```

### AMD / Intel / CPU-only

- **Windows has no official PyTorch ROCm wheels.**
- **`torch-directml`** is experimental/unsupported for pyiqa / Q-ReAlign. Prefer CPU torch for IQA; use Ollama for VLM (often GPU-accelerated separately).
- `python main.py setup` will choose CPU torch automatically when no NVIDIA GPU is present.

## Notes

- Run `python main.py iqa` as its own stage if VRAM is tight. Unload large Ollama models first.
- Weights download once into `~\.cache\torch\hub\pyiqa\`.
- Developer system Python and the managed venv can diverge — prefer `doctor --pipeline` after setup.
