#!/usr/bin/env bash
# Development mode: backend reload + Vite HMR on :5173 (proxying /api to :7860).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

VENV="${VENV:-$ROOT/.venv}"
HOST="${VM_HOST:-127.0.0.1}"
PORT="${VM_PORT:-7860}"

# shellcheck disable=SC1091
source "$ROOT/scripts/_cuda_env.sh"

cleanup() {
  kill "${BACK_PID:-}" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

(cd backend && ../.venv/bin/python -m uvicorn app.main:app --host "$HOST" --port "$PORT" --reload) &
BACK_PID=$!

cd frontend
npm run dev -- --host "$HOST"
