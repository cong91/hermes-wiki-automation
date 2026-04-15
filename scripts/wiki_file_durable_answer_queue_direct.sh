#!/usr/bin/env bash
set -euo pipefail

cd /root/.hermes/hermes-agent
source venv/bin/activate
exec /root/.hermes/scripts/wiki_file_durable_answer_queue.py --profile agent
