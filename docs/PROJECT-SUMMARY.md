# Project Summary

## Name
hermes-wiki-automation

## Goal
Package the proven Hermes durable-answer automation into a reusable standalone project that can be versioned, tested, installed into a shared Hermes root, and reused across multiple agents/profiles.

## Included components
- Shared Hermes plugin: `plugins/durable_answer_on_session_end/`
- Filing runner: `scripts/wiki_file_durable_answer.py`
- Queue runner: `scripts/wiki_file_durable_answer_queue.py`
- Queue wrapper: `scripts/wiki_file_durable_answer_queue_direct.sh`
- Payload preparation helper: `scripts/wiki_prepare_durable_answer_payload.py`
- Focused tests: `tests/`
- Local installer: `bin/install-local.sh`
- Docs: `README.md`, `docs/ARCHITECTURE.md`

## Installation target
- Shared plugin path: `~/.hermes/plugins/durable_answer_on_session_end`
- Shared runner path: `~/.hermes/scripts/`

## Verification command
```bash
source /root/.hermes/hermes-agent/venv/bin/activate
export HERMES_AGENT_ROOT=/root/.hermes/hermes-agent
pytest -q
```
