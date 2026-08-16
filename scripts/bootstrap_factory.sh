#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

python_supported() {
  "$1" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' \
    >/dev/null 2>&1
}

if [[ -n "${PYTHON_BOOTSTRAP:-}" ]]; then
  PYTHON_CANDIDATES=("$PYTHON_BOOTSTRAP")
else
  PYTHON_CANDIDATES=(python3.13 python3.12 python3.11 python3.10 python3)
fi

PYTHON_BOOTSTRAP=""
for candidate in "${PYTHON_CANDIDATES[@]}"; do
  candidate_path="$(command -v "$candidate" 2>/dev/null || true)"
  if [[ -n "$candidate_path" ]] && python_supported "$candidate_path"; then
    PYTHON_BOOTSTRAP="$candidate_path"
    break
  fi
done

if [[ -z "$PYTHON_BOOTSTRAP" ]]; then
  echo "Python 3.10 or newer is required." >&2
  exit 1
fi

for tool in ffmpeg ffprobe; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "Missing required system command: $tool" >&2
    echo "Install FFmpeg first, then rerun this script." >&2
    exit 1
  fi
done

if ! command -v node >/dev/null 2>&1; then
  echo "Node.js is required for the StoryMotion workbench." >&2
  exit 1
fi
if ! command -v npm >/dev/null 2>&1; then
  echo "npm is required for the StoryMotion workbench." >&2
  exit 1
fi
node --version >/dev/null
npm --version >/dev/null

cd "$ROOT_DIR"
if [[ ! -x "$ROOT_DIR/.venv/bin/python" ]]; then
  "$PYTHON_BOOTSTRAP" -m venv "$ROOT_DIR/.venv"
elif ! python_supported "$ROOT_DIR/.venv/bin/python"; then
  "$PYTHON_BOOTSTRAP" -m venv --clear "$ROOT_DIR/.venv"
fi

"$ROOT_DIR/.venv/bin/python" -m pip install --upgrade pip
"$ROOT_DIR/.venv/bin/python" -m pip install -r "$ROOT_DIR/requirements.txt"
cd "$ROOT_DIR/sites/storymotion-studio"
npm ci
cd "$ROOT_DIR"
"$ROOT_DIR/.venv/bin/python" -m pytest tests -q

echo "Factory environment ready: $ROOT_DIR/.venv/bin/python"
