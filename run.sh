#!/usr/bin/env bash
# run.sh — start the storefront microservice on port 8020
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Load .env if present
if [ -f .env ]; then
  set -a
  source .env
  set +a
fi

PORT="${PORT:-8020}"

# Prefer a local venv if present, otherwise fall back to the command-center venv
if [ -f "$SCRIPT_DIR/.venv/bin/uvicorn" ]; then
  PYTHON="$SCRIPT_DIR/.venv/bin/python"
elif [ -f "/home/scott/ai-workspace/repos/ai-lab-command-center/.venv/bin/uvicorn" ]; then
  PYTHON="/home/scott/ai-workspace/repos/ai-lab-command-center/.venv/bin/python"
else
  PYTHON="$(command -v python3)"
fi

export PYTHONPATH="$SCRIPT_DIR:${PYTHONPATH:-}"

exec "$PYTHON" -m uvicorn app.main:app \
  --host 0.0.0.0 \
  --port "$PORT" \
  --workers 1