#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BOOTSTRAP="${PYTHON_BOOTSTRAP:-$(command -v python3)}"

for tool in ffmpeg ffprobe; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "Missing required system command: $tool" >&2
    echo "Install FFmpeg first, then rerun this script." >&2
    exit 1
  fi
done

cd "$ROOT_DIR"
if [[ ! -x "$ROOT_DIR/.venv/bin/python" ]]; then
  "$PYTHON_BOOTSTRAP" -m venv "$ROOT_DIR/.venv"
fi

"$ROOT_DIR/.venv/bin/python" -m pip install --upgrade pip
"$ROOT_DIR/.venv/bin/python" -m pip install -r "$ROOT_DIR/requirements.txt"
"$ROOT_DIR/.venv/bin/python" -m pytest tests -q

echo "Factory environment ready: $ROOT_DIR/.venv/bin/python"
