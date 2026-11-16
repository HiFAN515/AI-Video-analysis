#!/usr/bin/env bash
# Start VideoMind (production mode: FastAPI serves the built frontend).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

VENV="${VENV:-$ROOT/.venv}"
if [ ! -x "$VENV/bin/python" ]; then
  echo "未找到虚拟环境 $VENV，请先运行 bash scripts/install.sh" >&2
  exit 1
fi

# CTranslate2 needs CUDA 12 libs even when torch is built for CUDA 13.
# shellcheck disable=SC1091
source "$ROOT/scripts/_cuda_env.sh"

if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi >/dev/null 2>&1; then
  echo "GPU: $(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader | head -1)"
else
  echo "注意：当前终端无法访问 NVIDIA GPU（nvidia-smi 失败），Demucs/ASR 将使用 CPU。"
fi

if [ ! -f frontend/dist/index.html ]; then
  echo "==> 前端尚未构建，正在构建…"
  (cd frontend && npm install --no-audit --no-fund && npm run build)
fi

HOST="${VM_HOST:-127.0.0.1}"
PORT="${VM_PORT:-7860}"

echo "VideoMind 启动：http://$HOST:$PORT"
cd backend
exec ../.venv/bin/python -m uvicorn app.main:app --host "$HOST" --port "$PORT"
