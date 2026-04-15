from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
QUEUE_SCRIPT_PATH = REPO_ROOT / 'scripts' / 'wiki-file-durable-answer-queue-v1.py'
RUNNER_SCRIPT_PATH = REPO_ROOT / 'scripts' / 'wiki-file-durable-answer-v1.py'


def load_module():
    assert QUEUE_SCRIPT_PATH.exists(), f'Missing script: {QUEUE_SCRIPT_PATH}'
    spec = importlib.util.spec_from_file_location('wiki_file_durable_answer_queue_v1', QUEUE_SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def write_text(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding='utf-8')
    return path


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


def make_payload(path: Path, **overrides) -> Path:
    payload = {
        'conversation_id': 'conv-queue-001',
        'captured_at': '2026-04-15',
        'question': 'Why should durable answers be queued before filing?',
        'answer': 'A queue gives the automation one prepared candidate at a time, which keeps the filing flow conservative and easy to reason about.',
        'why_durable': 'Reusable automation guidance for conversation-to-wiki filing.',
        'suggested_target': 'query',
        'suggested_slug': 'queued-durable-answer',
        'source_context': ['slack thread about durable answer automation'],
    }
    payload.update(overrides)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding='utf-8')
    return path


def test_ensure_queue_layout_creates_expected_directories(tmp_path: Path) -> None:
    module = load_module()
    wiki = make_wiki(tmp_path)

    queue_root = module.ensure_queue_layout(wiki)

    assert queue_root == wiki / '_inbox' / 'durable-answers'
    assert (queue_root / 'pending').exists()
    assert (queue_root / 'filed').exists()
    assert (queue_root / 'skipped').exists()
    assert (queue_root / 'escalated').exists()
    assert (queue_root / 'error').exists()


def test_process_next_payload_returns_idle_when_queue_empty(tmp_path: Path) -> None:
    module = load_module()
    wiki = make_wiki(tmp_path)
    queue_root = module.ensure_queue_layout(wiki)

    status = module.process_next_payload(wiki, queue_root, dry_run=False, verbose=False)

    assert status == 'NO_PENDING_PAYLOADS'


def test_process_next_payload_dry_run_keeps_payload_pending(tmp_path: Path) -> None:
    module = load_module()
    wiki = make_wiki(tmp_path)
    queue_root = module.ensure_queue_layout(wiki)
    payload_path = make_payload(queue_root / 'pending' / '2026-04-15-queued-durable-answer.yaml')

    status = module.process_next_payload(wiki, queue_root, dry_run=True, verbose=False)

    assert status == 'FILE_DURABLE_STATUS: FILED'
    assert payload_path.exists()
    assert not (queue_root / 'filed' / payload_path.name).exists()
    assert not (wiki / 'queries' / 'queued-durable-answer.md').exists()


def test_process_next_payload_moves_filed_payload_and_updates_wiki(tmp_path: Path) -> None:
    module = load_module()
    wiki = make_wiki(tmp_path)
    queue_root = module.ensure_queue_layout(wiki)
    payload_path = make_payload(queue_root / 'pending' / '2026-04-15-queued-durable-answer.yaml')

    status = module.process_next_payload(wiki, queue_root, dry_run=False, verbose=False)

    assert status == 'FILE_DURABLE_STATUS: FILED'
    assert not payload_path.exists()
    archived = queue_root / 'filed' / payload_path.name
    assert archived.exists()
    assert (wiki / 'queries' / 'queued-durable-answer.md').exists()


def test_process_next_payload_routes_skipped_and_escalated_payloads(tmp_path: Path) -> None:
    module = load_module()
    wiki = make_wiki(tmp_path)
    queue_root = module.ensure_queue_layout(wiki)
    skipped_path = make_payload(
        queue_root / 'pending' / '2026-04-15-skip.yaml',
        answer='ok',
        why_durable='progress update only',
        suggested_slug='skip-me',
    )
    escalated_path = make_payload(
        queue_root / 'pending' / '2026-04-16-escalate.yaml',
        suggested_target='canonical',
        suggested_slug='escalate-me',
    )

    first_status = module.process_next_payload(wiki, queue_root, dry_run=False, verbose=False)
    second_status = module.process_next_payload(wiki, queue_root, dry_run=False, verbose=False)

    assert first_status == 'FILE_DURABLE_STATUS: SKIPPED'
    assert second_status == 'FILE_DURABLE_STATUS: ESCALATED'
    assert not skipped_path.exists()
    assert not escalated_path.exists()
    assert (queue_root / 'skipped' / skipped_path.name).exists()
    assert (queue_root / 'escalated' / escalated_path.name).exists()


def test_process_all_pending_sweeps_until_queue_empty(tmp_path: Path) -> None:
    module = load_module()
    wiki = make_wiki(tmp_path)
    queue_root = module.ensure_queue_layout(wiki)
    first = make_payload(queue_root / 'pending' / '2026-04-15-first.yaml', suggested_slug='first-durable-answer')
    second = make_payload(queue_root / 'pending' / '2026-04-16-second.yaml', suggested_slug='second-durable-answer')

    statuses = module.process_all_pending(wiki, queue_root, dry_run=False, verbose=False)

    assert statuses == ['FILE_DURABLE_STATUS: FILED', 'FILE_DURABLE_STATUS: FILED']
    assert not first.exists()
    assert not second.exists()
    assert (queue_root / 'filed' / first.name).exists()
    assert (queue_root / 'filed' / second.name).exists()
    assert (wiki / 'queries' / 'first-durable-answer.md').exists()
    assert (wiki / 'queries' / 'second-durable-answer.md').exists()


def test_cli_processes_oldest_pending_payload(tmp_path: Path) -> None:
    wiki = make_wiki(tmp_path)
    queue_root = wiki / '_inbox' / 'durable-answers' / 'pending'
    older = make_payload(queue_root / '2026-04-15-older.yaml', suggested_slug='older-durable-answer')
    newer = make_payload(queue_root / '2026-04-16-newer.yaml', suggested_slug='newer-durable-answer')

    result = subprocess.run(
        [sys.executable, str(QUEUE_SCRIPT_PATH), '--wiki-path', str(wiki)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert result.stdout.strip().endswith('FILE_DURABLE_STATUS: FILED')
    assert not older.exists()
    assert newer.exists()
    assert (wiki / '_inbox' / 'durable-answers' / 'filed' / older.name).exists()
    assert not (wiki / '_inbox' / 'durable-answers' / 'filed' / newer.name).exists()
    assert (wiki / 'queries' / 'older-durable-answer.md').exists()
