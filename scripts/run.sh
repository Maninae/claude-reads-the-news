#!/bin/bash
# Wrapper script for launchd — sets up environment and runs the generator.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Load environment variables
if [ -f "$PROJECT_DIR/.env" ]; then
    set -a
    source "$PROJECT_DIR/.env"
    set +a
fi

# Ensure logs directory exists
mkdir -p "$PROJECT_DIR/logs"

# Log start
echo "$(date '+%Y-%m-%d %H:%M:%S') — Starting daily generation" >> "$PROJECT_DIR/logs/run.log"

# Activate virtual environment if it exists
if [ -f "$PROJECT_DIR/venv/bin/activate" ]; then
    source "$PROJECT_DIR/venv/bin/activate"
fi

# Run the generator
cd "$PROJECT_DIR"
python3 scripts/generate.py 2>&1 | tee -a "$PROJECT_DIR/logs/run.log"

EXIT_CODE=${PIPESTATUS[0]}

if [ $EXIT_CODE -ne 0 ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') — FAILED with exit code $EXIT_CODE" >> "$PROJECT_DIR/logs/run.log"
    # Optional: send notification on failure
    # osascript -e 'display notification "AI Anxiety Journal generation failed!" with title "The Watcher"'
fi

echo "$(date '+%Y-%m-%d %H:%M:%S') — Done (exit code: $EXIT_CODE)" >> "$PROJECT_DIR/logs/run.log"
