# Architecture

## Core flow

- Trigger: Hermes `on_session_end`
- Extraction: latest substantial user→assistant exchange
- Queue: `_inbox/durable-answers/pending/`
- Filing: `queries/` + `index.md` + `_meta/filed-queries.md` + `log.md`

## Design constraints

- Conservative capture only
- Skip slash commands and low-signal exchanges
- Prefer queue-based processing over direct wiki mutation in the hook
- Prefer shared plugin installation at the Hermes root so multiple profiles can reuse the same automation

## Deployment model

- Shared plugin path: `~/.hermes/plugins/durable_answer_on_session_end`
- Shared runner path: `~/.hermes/scripts/`
- Shared wiki root: `skills.config.wiki.path`
