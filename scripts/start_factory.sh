#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_PATH="${FACTORY_CONFIG:-$ROOT_DIR/config/factory.config.json}"
MODE="${MODE:-novel}"
PROJECT="${PROJECT:-sample_episode}"
TITLE="${TITLE:-旧城来信}"
INPUT_PATH="${INPUT_PATH:-$ROOT_DIR/samples/sample_novel.txt}"
IDEA="${IDEA:-两只猫在原木风房间调查一个会响的纸盒}"
SHOTS="${SHOTS:-8}"

if [[ -n "${FACTORY_PYTHON:-}" ]]; then
  PYTHON_BIN="$FACTORY_PYTHON"
elif [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
  PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
else
  PYTHON_BIN="$(command -v python3)"
fi

cd "$ROOT_DIR"

if [[ "${RUN_TESTS:-0}" == "1" ]]; then
  "$PYTHON_BIN" -m pytest -q
fi

RUNS_DIR="$(
  "$PYTHON_BIN" -c \
    'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["runsDir"])' \
    "$CONFIG_PATH"
)"
PROJECT_DIR="$RUNS_DIR/$PROJECT"

if [[ ! -f "$PROJECT_DIR/project.json" ]]; then
  CREATE_ARGS=(
    factory create
    --mode "$MODE"
    --project "$PROJECT"
    --title "$TITLE"
    --shots "$SHOTS"
  )
  if [[ "$MODE" == "original" ]]; then
    CREATE_ARGS+=(--idea "$IDEA")
  else
    CREATE_ARGS+=(--input "$INPUT_PATH")
  fi
  if [[ -n "${CHARACTER_ASSETS:-}" ]]; then
    CREATE_ARGS+=(--character-assets "$CHARACTER_ASSETS")
  fi
  "$PYTHON_BIN" factory_cli.py --config "$CONFIG_PATH" "${CREATE_ARGS[@]}"
fi

RUN_ARGS=(factory run "$PROJECT")
if [[ -n "${THROUGH:-}" ]]; then
  RUN_ARGS+=(--through "$THROUGH")
fi
if [[ "${ENABLE_LIVE:-0}" == "1" ]]; then
  RUN_ARGS+=(--enable-live)
fi

"$PYTHON_BIN" factory_cli.py --config "$CONFIG_PATH" "${RUN_ARGS[@]}" || true
"$PYTHON_BIN" factory_cli.py --config "$CONFIG_PATH" factory status "$PROJECT"
