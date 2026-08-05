# GPU setup (Windows + RTX 5090)

PyTorch CUDA and `pyiqa` are required for the IQA scoring stage (`maniqa` / `nima`).

## Install

```powershell
# CUDA 12.8 wheels (RTX 5090 / driver 610+)
python -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128

python -m pip install pyiqa
```

## Verify

```powershell
python -c "import torch; print(torch.__version__, torch.cuda.is_available(), torch.cuda.get_device_name(0))"
python -c "import pyiqa, torch; m=pyiqa.create_metric('maniqa', device='cuda'); print('maniqa ok')"
```

Expect `cuda True` and your RTX 5090 name.

## Notes

- Run `python main.py iqa` as its own stage. Unload large Ollama models if VRAM is tight (IQA is small; `qwen3.6:35b` is not).
- Weights download once into `~\.cache\torch\hub\pyiqa\`.
