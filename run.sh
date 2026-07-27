#!/usr/bin/env bash
#
# run.sh — orchestrate the six pipeline stages for one day's video.
#
# Each stage is a standalone script and can be run on its own; this just chains
# them in order and STOPS at the first failure (set -e + explicit checks), so a
# QC failure halts before upload.
#
# Usage:
#   ./run.sh                       # full pipeline for today
#   ./run.sh --date 2026-07-26     # a specific day
#   ./run.sh --dry-run             # ~30s validation video, no real upload
#   ./run.sh --skip-upload         # everything except upload
#   ./run.sh --date 2026-07-26 --dry-run --verbose
#
# Pass-through: any --date/--dry-run/--verbose flags are forwarded to every
# stage so they all operate on the same day and mode.

set -euo pipefail

cd "$(dirname "$0")"

# Interpreter resolution, in order:
#   1. $PYTHON if set.
#   2. The repo's own .venv — Scripts/ on Windows, bin/ elsewhere. This is where
#      the deps actually live, so prefer it over anything on PATH; picking up a
#      bare system python here fails at "PyYAML not installed".
#   3. python3, then python (Windows installs commonly ship only `python`).
PY="${PYTHON:-}"
if [[ -z "$PY" ]]; then
  for candidate in .venv/Scripts/python.exe .venv/bin/python3 .venv/bin/python; do
    if [[ -x "$candidate" ]]; then
      PY="$candidate"
      break
    fi
  done
fi
if [[ -z "$PY" ]]; then
  if command -v python3 >/dev/null 2>&1; then
    PY="python3"
  elif command -v python >/dev/null 2>&1; then
    PY="python"
  else
    echo "Neither python3 nor python found on PATH, and no .venv in $(pwd)." >&2
    exit 1
  fi
fi

INVOCATION="$*"
DRY_RUN=""
SKIP_UPLOAD=""
RUN_DATE=""
PASSTHROUGH=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)
      DRY_RUN="--dry-run"
      PASSTHROUGH+=("--dry-run")
      shift
      ;;
    --skip-upload)
      SKIP_UPLOAD="1"
      shift
      ;;
    --date)
      RUN_DATE="$2"
      PASSTHROUGH+=("--date" "$2")
      shift 2
      ;;
    --verbose|-v)
      PASSTHROUGH+=("--verbose")
      shift
      ;;
    *)
      echo "Unknown argument: $1" >&2
      echo "Usage: ./run.sh [--date YYYY-MM-DD] [--dry-run] [--skip-upload] [--verbose]" >&2
      exit 2
      ;;
  esac
done

# Persist the whole run (stdout+stderr, every stage) to logs/run-<date>.log so
# the outcome — in particular upload.py's Studio URL — is still there in the
# morning. Appends, so a re-run for the same day keeps the earlier attempts.
RUN_DATE="${RUN_DATE:-$(date +%F)}"
mkdir -p logs
LOG_FILE="logs/run-${RUN_DATE}.log"
exec > >(tee -a "$LOG_FILE") 2>&1
echo "### $(date '+%Y-%m-%d %H:%M:%S %Z')  ./run.sh ${INVOCATION}  (log: ${LOG_FILE})"

run_stage () {
  local name="$1"; shift
  echo ""
  echo "==================================================================="
  echo ">>> STAGE: ${name}"
  echo "==================================================================="
  "$PY" "$@"
}

if [[ -n "$DRY_RUN" ]]; then
  echo "### DRY-RUN: generating a short (~30s) validation video. No real upload."
fi

run_stage "script  (parse + keywords)" script.py ${PASSTHROUGH[@]+"${PASSTHROUGH[@]}"}
run_stage "tts     (Kokoro narration)" tts.py    ${PASSTHROUGH[@]+"${PASSTHROUGH[@]}"}
run_stage "images  (Pexels/Pixabay)"   images.py ${PASSTHROUGH[@]+"${PASSTHROUGH[@]}"}
run_stage "render  (ffmpeg)"           render.py ${PASSTHROUGH[@]+"${PASSTHROUGH[@]}"}
run_stage "qc      (automated checks)" qc.py     ${PASSTHROUGH[@]+"${PASSTHROUGH[@]}"}

if [[ -n "$SKIP_UPLOAD" ]]; then
  echo ""
  echo ">>> --skip-upload set: stopping before upload. video.mp4 is ready."
  exit 0
fi

# In dry-run, upload.py validates credentials but does not actually upload.
run_stage "upload  (YouTube, private)" upload.py ${PASSTHROUGH[@]+"${PASSTHROUGH[@]}"}

echo ""
echo "### Pipeline complete."
