#!/usr/bin/env bash
# Local development server launcher for Linux / macOS
set -euo pipefail

echo "==> Starting Hardonia Storefront server on http://127.0.0.1:8020"

if command -v uv >/dev/null 2>&1; then
    exec uv run uvicorn app.main:app --host 0.0.0.0 --port 8020 --reload
else
    exec python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8020 --reload
fi