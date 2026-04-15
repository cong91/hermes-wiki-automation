# hermes-wiki-automation

Shared Hermes automation project for filing durable conversation outputs into an Obsidian-based LLM Wiki.

## What this project contains

- `plugins/durable_answer_on_session_end/` — Hermes `on_session_end` plugin that extracts conservative durable-answer candidates.
- `scripts/wiki_file_durable_answer.py` — single-payload filing runner.
- `scripts/wiki_file_durable_answer_queue.py` — queue sweeper that files one or more pending payloads.
- `scripts/wiki_prepare_durable_answer_payload.py` — helper for preparing a payload from a selected Hermes session exchange.
- `tests/` — focused regression tests for the plugin and runners.

## Architecture

1. Hermes finishes a user-facing exchange.
2. The shared plugin inspects the latest user→assistant pair on `on_session_end`.
3. If the exchange is substantial enough, it queues a durable-answer payload under `_inbox/durable-answers/pending/`.
4. The queue runner files that payload into `queries/`, updates `index.md`, updates `_meta/filed-queries.md`, and appends `log.md`.

## Repository layout

```text
plugins/
  durable_answer_on_session_end/
scripts/
tests/
docs/
bin/
```

## Requirements

- Python 3.11+
- Hermes checkout available locally for `hermes_state.py` during tests and runtime helpers
- Shared wiki configured via `skills.config.wiki.path`

## Local test command

```bash
cd /root/projects/hermes-wiki-automation
export HERMES_AGENT_ROOT=/root/.hermes/hermes-agent
pytest
```

## Install into Hermes shared root

```bash
cd /root/projects/hermes-wiki-automation
./bin/install-local.sh
```

That copies the shared plugin into `~/.hermes/plugins/durable_answer_on_session_end` and copies the runner scripts into `~/.hermes/scripts/`.

## Current scope

This project packages the durable-answer automation path that was proven locally:

- shared Hermes plugin
- prepared durable-answer payload flow
- queue-based conservative filing into the wiki
- tests for plugin, queue runner, and payload-preparation helper
