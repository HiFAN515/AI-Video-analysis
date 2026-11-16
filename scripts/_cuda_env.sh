#!/usr/bin/env bash
# Make CUDA 12 user-space libraries visible to CTranslate2 (faster-whisper).
# CTranslate2 4.x is linked against libcublas.so.12; a CUDA 13 stack only
# provides libcublas.so.13, which cannot satisfy that SONAME.
#
# Usage: source scripts/_cuda_env.sh

_CUDA12_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
_CUDA12_VENV="${VENV:-$_CUDA12_ROOT/.venv}"
_CUDA12_DIRS=()

for _dir in "$_CUDA12_VENV"/lib/python*/site-packages/nvidia/cublas/lib \
            "$_CUDA12_VENV"/lib/python*/site-packages/nvidia/cudnn/lib; do
  [ -d "$_dir" ] && _CUDA12_DIRS+=("$_dir")
done
for _dir in /usr/local/cuda-12*/lib64; do
  [ -d "$_dir" ] && _CUDA12_DIRS+=("$_dir")
done

if [ "${#_CUDA12_DIRS[@]}" -gt 0 ]; then
  _CUDA12_JOINED="$(IFS=:; echo "${_CUDA12_DIRS[*]}")"
  export LD_LIBRARY_PATH="${_CUDA12_JOINED}:${LD_LIBRARY_PATH:-}"
fi

unset _dir _CUDA12_JOINED
