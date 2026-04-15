#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"

mkdir -p "$HERMES_HOME/plugins" "$HERMES_HOME/scripts"
rm -rf "$HERMES_HOME/plugins/durable_answer_on_session_end"
cp -R "$ROOT_DIR/plugins/durable_answer_on_session_end" "$HERMES_HOME/plugins/durable_answer_on_session_end"
cp "$ROOT_DIR/scripts/wiki_file_durable_answer.py" "$HERMES_HOME/scripts/"
cp "$ROOT_DIR/scripts/wiki_file_durable_answer_queue.py" "$HERMES_HOME/scripts/"
cp "$ROOT_DIR/scripts/wiki_file_durable_answer_queue_direct.sh" "$HERMES_HOME/scripts/"
cp "$ROOT_DIR/scripts/wiki_prepare_durable_answer_payload.py" "$HERMES_HOME/scripts/"
chmod +x "$HERMES_HOME/scripts/wiki_file_durable_answer.py"          "$HERMES_HOME/scripts/wiki_file_durable_answer_queue.py"          "$HERMES_HOME/scripts/wiki_file_durable_answer_queue_direct.sh"          "$HERMES_HOME/scripts/wiki_prepare_durable_answer_payload.py"

echo "Installed durable-answer automation into $HERMES_HOME"
