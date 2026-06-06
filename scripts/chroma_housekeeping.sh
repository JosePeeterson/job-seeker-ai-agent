#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

DAYS="${DAYS:-30}"
TARGET_DIR="${TARGET_DIR:-$REPO_DIR/data/chroma_db}"
APPLY=false

usage() {
  cat <<EOF
Usage: $(basename "$0") [--apply] [--days N] [--target PATH]

Safely clean ChromaDB artifacts older than N days using filesystem mtime.

Options:
  --apply          Actually delete matched entries. Default is dry-run.
  --days N         Age threshold in days (default: 30)
  --target PATH    Directory to clean (default: data/chroma_db)
  -h, --help       Show help

Examples:
  $(basename "$0")
  $(basename "$0") --apply
  $(basename "$0") --apply --days 45
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply)
      APPLY=true
      shift
      ;;
    --days)
      DAYS="${2:-}"
      shift 2
      ;;
    --target)
      TARGET_DIR="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 2
      ;;
  esac
done

if ! [[ "$DAYS" =~ ^[0-9]+$ ]]; then
  echo "--days must be a non-negative integer." >&2
  exit 2
fi

if [[ ! -d "$TARGET_DIR" ]]; then
  echo "Target directory does not exist: $TARGET_DIR" >&2
  exit 1
fi

echo "[housekeeping] target=$TARGET_DIR"
echo "[housekeeping] threshold=${DAYS}d"
echo "[housekeeping] mode=$([[ "$APPLY" == true ]] && echo apply || echo dry-run)"

tmp_file="$(mktemp)"
trap 'rm -f "$tmp_file"' EXIT

find "$TARGET_DIR" -mindepth 1 -mtime "+$DAYS" -print | sort > "$tmp_file"
count="$(wc -l < "$tmp_file")"

echo "[housekeeping] candidates=$count"
if [[ "$count" -gt 0 ]]; then
  cat "$tmp_file"
fi

if [[ "$APPLY" == true && "$count" -gt 0 ]]; then
  # Delete only entries discovered in this run to keep behavior deterministic.
  while IFS= read -r path; do
    rm -rf -- "$path"
  done < "$tmp_file"
  echo "[housekeeping] deleted=$count"
else
  echo "[housekeeping] deleted=0"
fi

remaining="$(find "$TARGET_DIR" -mindepth 1 -mtime "+$DAYS" | wc -l)"
size="$(du -sh "$TARGET_DIR" | awk '{print $1}')"
echo "[housekeeping] remaining_older_than_threshold=$remaining"
echo "[housekeeping] size_now=$size"
