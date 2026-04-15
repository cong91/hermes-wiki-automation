from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / 'scripts' / 'wiki-file-durable-answer-v1.py'


def load_module():
    assert SCRIPT_PATH.exists(), f'Missing script: {SCRIPT_PATH}'
    spec = importlib.util.spec_from_file_location('wiki_file_durable_answer_v1', SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def write_text(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding='utf-8')
    return path


def make_payload(tmp_path: Path, **overrides) -> tuple[Path, dict]:
    payload = {
        'conversation_id': 'conv-123',
        'captured_at': '2026-04-15',
        'question': 'Why should durable answers be filed from conversations?',
        'answer': 'They preserve reusable guidance that would otherwise die in chat history.',
        'why_durable': 'Reusable operational guidance for future Hermes/wiki work.',
        'suggested_target': 'query',
        'suggested_slug': 'durable-answer-loop',
        'source_context': ['slack thread about llm-wiki automation'],
    }
    payload.update(overrides)
    path = tmp_path / 'payload.yaml'
    import yaml

    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding='utf-8')
    return path, payload


def make_wiki(tmp_path: Path) -> Path:
    wiki = tmp_path / 'wiki'
    write_text(wiki / 'SCHEMA.md', '# Wiki Schema\n\n## Tag Taxonomy\n- ops\n- hermes\n- wiki\n')
    write_text(
        wiki / 'index.md',
        '# Wiki Index\n'
        '> Content catalog for tests.\n'
        '> Last updated: 2026-04-15 | Total indexed pages: 1\n'
        '## Entities\n'
        '## Concepts\n'
        '## Summaries\n'
        '## Comparisons\n'
        '## Meta\n'
        '- [[_meta/filed-queries]] — Filed durable answers.\n'
        '## Queries\n'
        '- [[queries/existing-note]] — Existing query note.\n',
    )
    write_text(wiki / 'log.md', '# Wiki Log\n\n## [2026-04-15] create | Test wiki\n- Initialized test wiki\n')
    write_text(
        wiki / '_meta' / 'filed-queries.md',
        '---\n'
        'title: Filed Queries Registry\n'
        'created: 2026-04-15\n'
        'updated: 2026-04-15\n'
        'type: query\n'
        'tags: [wiki, automation, ops, agent]\n'
        'sources: []\n'
        '---\n\n'
        '# Filed Queries Registry\n\n'
        '## Entries\n'
        '- None yet.\n',
    )
    (wiki / 'queries').mkdir(parents=True, exist_ok=True)
    write_text(wiki / 'queries' / 'existing-note.md', '# Existing note\n')
    return wiki


def test_load_payload_accepts_yaml_and_json(tmp_path: Path) -> None:
    module = load_module()
    yaml_path, payload = make_payload(tmp_path)
    loaded_yaml = module.load_payload(yaml_path)
    assert loaded_yaml['question'] == payload['question']

    json_path = tmp_path / 'payload.json'
    json_path.write_text(json.dumps(payload), encoding='utf-8')
    loaded_json = module.load_payload(json_path)
    assert loaded_json['answer'] == payload['answer']


def test_decide_action_file_skip_and_escalate(tmp_path: Path) -> None:
    module = load_module()
    _, file_payload = make_payload(tmp_path)
    action, reason = module.decide_action(file_payload)
    assert action == 'file'
    assert reason == 'query-obvious-and-durable'

    _, skip_payload = make_payload(
        tmp_path,
        answer='ok',
        why_durable='progress update only',
    )
    action, reason = module.decide_action(skip_payload)
    assert action == 'skip'
    assert reason == 'too-thin-or-low-reuse'

    _, escalate_payload = make_payload(tmp_path, suggested_target='canonical')
    action, reason = module.decide_action(escalate_payload)
    assert action == 'escalate'
    assert reason == 'target-needs-human-judgment'


def test_render_query_page_outputs_compact_query_format(tmp_path: Path) -> None:
    module = load_module()
    _, payload = make_payload(tmp_path)

    page = module.render_query_page(payload, 'durable-answer-loop')

    assert 'title: Durable Answer Loop' in page
    assert '# Durable Answer Loop' in page
    assert '## Question' in page
    assert '## Durable answer' in page
    assert '## Why it matters' in page
    assert '## Reuse guidance' in page


def test_apply_filing_updates_query_registry_index_and_log(tmp_path: Path) -> None:
    module = load_module()
    wiki = make_wiki(tmp_path)
    _, payload = make_payload(tmp_path)

    result = module.apply_filing(wiki, payload)

    query_path = wiki / 'queries' / 'durable-answer-loop.md'
    assert result['page_path'] == query_path
    assert query_path.exists()
    assert '`queries/durable-answer-loop.md` | standalone | slack thread about llm-wiki automation' in (wiki / '_meta' / 'filed-queries.md').read_text(encoding='utf-8')
    index_text = (wiki / 'index.md').read_text(encoding='utf-8')
    assert '[[queries/durable-answer-loop]]' in index_text
    assert index_text.index('[[queries/durable-answer-loop]]') < index_text.index('[[queries/existing-note]]')
    log_text = (wiki / 'log.md').read_text(encoding='utf-8')
    assert '## [2026-04-15] query | Durable Answer Loop' in log_text


def test_cli_dry_run_and_status_outputs(tmp_path: Path) -> None:
    wiki = make_wiki(tmp_path)
    payload_path, _ = make_payload(tmp_path)

    dry_run = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), '--payload', str(payload_path), '--wiki-path', str(wiki), '--dry-run'],
        check=False,
        capture_output=True,
        text=True,
    )
    assert dry_run.returncode == 0
    assert 'ACTION: file' in dry_run.stdout
    assert 'FILE_DURABLE_STATUS: FILED' in dry_run.stdout
    assert not (wiki / 'queries' / 'durable-answer-loop.md').exists()

    filed = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), '--payload', str(payload_path), '--wiki-path', str(wiki)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert filed.returncode == 0
    assert filed.stdout.strip().endswith('FILE_DURABLE_STATUS: FILED')

    skip_payload_path, _ = make_payload(tmp_path, answer='ok', why_durable='progress update only')
    skipped = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), '--payload', str(skip_payload_path), '--wiki-path', str(wiki)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert skipped.returncode == 0
    assert skipped.stdout.strip().endswith('FILE_DURABLE_STATUS: SKIPPED')

    escalate_payload_path, _ = make_payload(tmp_path, suggested_target='canonical')
    escalated = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), '--payload', str(escalate_payload_path), '--wiki-path', str(wiki)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert escalated.returncode == 0
    assert escalated.stdout.strip().endswith('FILE_DURABLE_STATUS: ESCALATED')
