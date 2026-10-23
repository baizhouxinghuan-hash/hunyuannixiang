#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# AI Studio OpenAI-Compatible Proxy (Linux / macOS)
# OpenAI Base URL: http://127.0.0.1:1010/v1
# ---------------------------------------------------------------------------
set -euo pipefail
cd "$(dirname "$0")"

PY=python3
if [ -x ".venv/bin/python" ]; then
  PY=".venv/bin/python"
fi

echo "[*] 启动 AI Studio OpenAI Proxy ..."
exec "$PY" run.py "$@"
