#!/usr/bin/env bash
# One-shot installer: Python deps, Demucs (without torchaudio), frontend build.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PYTHON="${PYTHON:-python3}"
VENV="${VENV:-$ROOT/.venv}"

command -v ffmpeg >/dev/null 2>&1 || {
  echo "未找到 ffmpeg，请先安装：sudo apt install ffmpeg（macOS: brew install ffmpeg）" >&2
  exit 1
}

if [ ! -d "$VENV" ]; then
  echo "==> 创建虚拟环境 $VENV"
  "$PYTHON" -m venv "$VENV"
fi

# shellcheck disable=SC1091
source "$VENV/bin/activate"

echo "==> 升级 pip"
python -m pip install -U pip

echo "==> 安装 Python 依赖"
PIP_INDEX="${PIP_INDEX:-}"
if [ -n "$PIP_INDEX" ]; then
  pip install --index-url "$PIP_INDEX" -r backend/requirements.txt
else
  pip install -r backend/requirements.txt
fi

if ! python -c "import torch" >/dev/null 2>&1; then
  echo "==> 安装 CPU 版 PyTorch（如需 GPU，请自行安装对应 CUDA 版本）"
  pip install torch --index-url https://download.pytorch.org/whl/cpu || pip install torch
fi

echo "==> 安装 Demucs（--no-deps，避免被 torchaudio 的 ABI 问题拖累）"
pip install --no-deps "demucs==4.0.1" openunmix julius lameenc einops \
  dora-search treetable omegaconf retrying submitit cloudpickle

if [ "${INSTALL_CUDA12:-0}" = "1" ]; then
  echo "==> 安装 CUDA 12 运行库（faster-whisper / CTranslate2 使用）"
  pip install nvidia-cublas-cu12 nvidia-cudnn-cu12
fi

echo "==> 构建前端"
if command -v npm >/dev/null 2>&1; then
  (cd frontend && npm install --no-audit --no-fund && npm run build)
else
  echo "未找到 npm。可先安装 Node.js 18+，或使用已构建的 frontend/dist。" >&2
fi

echo
echo "安装完成。运行：bash scripts/start.sh"
echo "首次解析时会自动下载 Whisper / 说话人分离 / Demucs 模型。"
