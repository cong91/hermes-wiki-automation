#!/usr/bin/env bash
set -euo pipefail

cd /root/.hermes/hermes-agent
source venv/bin/activate
exec /root/.hermes/scripts/wiki-file-durable-answer-queue-v1.py --profile agent
