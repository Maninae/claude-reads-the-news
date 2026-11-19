#!/bin/bash
# Wrapper script for launchd — sets up environment and runs the generator.
#
# Failure handling: an EXIT trap logs FAILED and sends an alert whenever the run exits
# non-zero, including when `set -e` stops the script early. The alert goes through
# DIGEST_ALERT_SCRIPT (a Telegram delivery script called as
# `/usr/bin/python3 <script> --raw "<message>"`); when that file is absent, a macOS
# notification is shown instead.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
RUN_LOG="$PROJECT_DIR/logs/run.log"
: "${DIGEST_ALERT_SCRIPT:=$HOME/.mineru/scripts/deliver-output.py}"

log_line() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') — $*" >> "$RUN_LOG"
}

send_failure_alert() {
    local message="$1"
    if [ -f "$DIGEST_ALERT_SCRIPT" ]; then
        /usr/bin/python3 "$DIGEST_ALERT_SCRIPT" --raw "$message" >> "$RUN_LOG" 2>&1 \
            || log_line "alert delivery failed"
    else
        /usr/bin/osascript -e "display notification \"$message\" with title \"Claude's Daily Digest\"" \
            >/dev/null 2>&1 || true
    fi
}

on_exit() {
    local exit_code=$?
    if [ "$exit_code" -ne 0 ]; then
        log_line "FAILED with exit code $exit_code"
        send_failure_alert "❌ Daily digest run FAILED (exit $exit_code). See logs/run.log in claude-reads-the-news."
    fi
    log_line "Done (exit code: $exit_code)"
}

mkdir -p "$PROJECT_DIR/logs"
trap on_exit EXIT

log_line "Starting daily generation"

# Activate virtual environment if it exists
if [ -f "$PROJECT_DIR/venv/bin/activate" ]; then
    source "$PROJECT_DIR/venv/bin/activate"
fi

# Run the generator; pipefail makes the pipeline carry generate.py's exit code.
cd "$PROJECT_DIR"
python3 scripts/generate.py 2>&1 | tee -a "$RUN_LOG"
