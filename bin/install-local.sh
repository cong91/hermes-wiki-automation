#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"

mkdir -p "$HERMES_HOME/plugins" "$HERMES_HOME/scripts"
rm -rf "$HERMES_HOME/plugins/durable_answer_on_session_end"
cp -R "$ROOT_DIR/plugins/durable_answer_on_session_end" "$HERMES_HOME/plugins/durable_answer_on_session_end"
cp "$ROOT_DIR/scripts/wiki-file-durable-answer-v1.py" "$HERMES_HOME/scripts/"
cp "$ROOT_DIR/scripts/wiki-file-durable-answer-queue-v1.py" "$HERMES_HOME/scripts/"
cp "$ROOT_DIR/scripts/wiki-file-durable-answer-queue-v1-direct.sh" "$HERMES_HOME/scripts/"
cp "$ROOT_DIR/scripts/wiki-prepare-durable-answer-payload-v1.py" "$HERMES_HOME/scripts/"
chmod +x "$HERMES_HOME/scripts/wiki-file-durable-answer-v1.py"          "$HERMES_HOME/scripts/wiki-file-durable-answer-queue-v1.py"          "$HERMES_HOME/scripts/wiki-file-durable-answer-queue-v1-direct.sh"          "$HERMES_HOME/scripts/wiki-prepare-durable-answer-payload-v1.py"

echo "Installed durable-answer automation into $HERMES_HOME"
