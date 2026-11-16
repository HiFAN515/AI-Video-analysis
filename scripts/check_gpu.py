"""Diagnose GPU / CUDA 12 libs for VideoMind.

Run from the project root after loading the CUDA 12 env:

    source scripts/_cuda_env.sh
    .venv/bin/python scripts/check_gpu.py
"""
from __future__ import annotations

import ctypes
import os

def main() -> None:
    print("LD_LIBRARY_PATH entries:")
    for entry in (os.environ.get("LD_LIBRARY_PATH") or "").split(":"):
        if entry:
            print("  ", entry)

    print("\nDynamic libraries expected by CTranslate2:")
    for name in ("libcublas.so.12", "libcublasLt.so.12", "libcudnn.so.9", "libcudnn_ops.so.9"):
        try:
            ctypes.CDLL(name)
            print(f"  {name}: OK")
        except OSError as exc:
            print(f"  {name}: MISSING ({exc})")

    print("\nPyTorch:")
    try:
        import torch

        print("  torch:", torch.__version__)
        print("  cuda available:", torch.cuda.is_available())
        if torch.cuda.is_available():
            print("  device:", torch.cuda.get_device_name(0))

    except Exception as exc:
        print("  torch error:", exc)

    print("\nCTranslate2:")
    try:
        import ctranslate2

        print("  cuda device count:", ctranslate2.get_cuda_device_count())
    except Exception as exc:
        print("  CTranslate2 CUDA unavailable:", exc)


if __name__ == "__main__":
    main()
