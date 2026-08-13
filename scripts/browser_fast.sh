#!/usr/bin/env bash
set -euo pipefail

SESSION="${BROWSER_SESSION:-manju-factory}"
SNAPSHOT_DEPTH="${BROWSER_SNAPSHOT_DEPTH:-6}"
BROWSER=(agent-browser --session "$SESSION" --session-name "$SESSION")

usage() {
  cat >&2 <<'EOF'
Usage: scripts/browser_fast.sh <command> [arguments]

Commands:
  inspect <url>              Open, verify, and print a compact interactive snapshot
  open <url>                 Open a page in the persistent session
  login <url>                Open a visible browser for one-time manual login
  snapshot                   Print a compact interactive snapshot
  click <ref|selector>       Click an element
  fill <ref|selector> <text> Fill an input
  get <kind> [selector]      Read title, URL, text, value, or attributes
  screenshot [path]          Capture a full-page screenshot
  close                      Close the current browser session
  raw <arguments...>         Pass arguments directly to agent-browser

Environment:
  BROWSER_SESSION            Persistent session name (default: manju-factory)
  BROWSER_SNAPSHOT_DEPTH     Snapshot tree depth (default: 6)
EOF
}

require_arg() {
  local value="${1:-}"
  local label="$2"
  if [[ -z "$value" ]]; then
    echo "Missing $label." >&2
    usage
    exit 2
  fi
}

if ! command -v agent-browser >/dev/null 2>&1; then
  echo "agent-browser is not installed or is not on PATH." >&2
  exit 127
fi

command_name="${1:-}"
if [[ -z "$command_name" ]]; then
  usage
  exit 2
fi
shift

case "$command_name" in
  inspect)
    require_arg "${1:-}" "URL"
    "${BROWSER[@]}" open "$1"
    "${BROWSER[@]}" get title
    "${BROWSER[@]}" get url
    "${BROWSER[@]}" snapshot -i -c -d "$SNAPSHOT_DEPTH"
    ;;
  open)
    require_arg "${1:-}" "URL"
    "${BROWSER[@]}" open "$1"
    ;;
  login)
    require_arg "${1:-}" "URL"
    "${BROWSER[@]}" close
    "${BROWSER[@]}" --headed open "$1"
    ;;
  snapshot)
    "${BROWSER[@]}" snapshot -i -c -d "$SNAPSHOT_DEPTH"
    ;;
  click)
    require_arg "${1:-}" "element reference or selector"
    "${BROWSER[@]}" click "$1"
    ;;
  fill)
    require_arg "${1:-}" "element reference or selector"
    require_arg "${2:-}" "text"
    "${BROWSER[@]}" fill "$1" "$2"
    ;;
  get)
    require_arg "${1:-}" "value kind"
    "${BROWSER[@]}" get "$@"
    ;;
  screenshot)
    if [[ -n "${1:-}" ]]; then
      "${BROWSER[@]}" screenshot --full "$1"
    else
      "${BROWSER[@]}" screenshot --full
    fi
    ;;
  close)
    "${BROWSER[@]}" close
    ;;
  raw)
    require_arg "${1:-}" "agent-browser arguments"
    "${BROWSER[@]}" "$@"
    ;;
  help|-h|--help)
    usage
    ;;
  *)
    echo "Unknown command: $command_name" >&2
    usage
    exit 2
    ;;
esac
