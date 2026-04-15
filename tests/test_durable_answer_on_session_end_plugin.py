from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_PATH = REPO_ROOT / 'plugins' / 'durable_answer_on_session_end' / '__init__.py'


def load_module():
    assert PLUGIN_PATH.exists(), f'Missing plugin module: {PLUGIN_PATH}'
    spec = importlib.util.spec_from_file_location('durable_answer_on_session_end', PLUGIN_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def write_text(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding='utf-8')
    return path


def make_message(role: str, content: str, ts: float) -> dict:
    return {'role': role, 'content': content, 'timestamp': ts}


def test_extract_last_exchange_picks_latest_user_and_assistant_pair() -> None:
    module = load_module()
    messages = [
        make_message('user', 'old question', 1.0),
        make_message('assistant', 'old answer', 2.0),
        make_message('user', 'How should Hermes capture durable answers automatically?', 3.0),
        make_message('tool', 'ignored', 4.0),
        make_message('assistant', 'Use on_session_end to create a conservative candidate payload after each completed run.', 5.0),
    ]

    exchange = module.extract_last_exchange(messages)

    assert exchange is not None
    assert exchange['user']['content'].startswith('How should Hermes capture')
    assert exchange['assistant']['content'].startswith('Use on_session_end')


def test_should_queue_exchange_is_conservative() -> None:
    module = load_module()
    assert module.should_queue_exchange('How should Hermes capture durable answers automatically?', 'Use on_session_end to create a conservative candidate payload after each completed run.') is True
    assert module.should_queue_exchange('/help', 'ok') is False
    assert module.should_queue_exchange('short', 'tiny') is False


def test_build_payload_contains_expected_fields() -> None:
    module = load_module()
    payload = module.build_payload(
        session_id='session-123',
        platform='slack',
        user_text='How should Hermes capture durable answers automatically?',
        assistant_text='Use on_session_end to create a conservative candidate payload after each completed run.',
        timestamp=1713139200.0,
    )

    assert payload['conversation_id'] == 'session-123'
    assert payload['suggested_target'] == 'query'
    assert payload['question'].startswith('How should Hermes capture')
    assert 'on_session_end' in payload['answer']
    assert payload['source_context'] == ['slack session session-123']
    assert payload['suggested_slug']


def test_queue_payload_writes_yaml_once(tmp_path: Path) -> None:
    module = load_module()
    queue_root = tmp_path / 'wiki' / '_inbox' / 'durable-answers'
    payload = module.build_payload(
        session_id='session-123',
        platform='slack',
        user_text='How should Hermes capture durable answers automatically?',
        assistant_text='Use on_session_end to create a conservative candidate payload after each completed run.',
        timestamp=1713139200.0,
    )

    first = module.queue_payload(queue_root, payload, unique_key='assistant-5')
    second = module.queue_payload(queue_root, payload, unique_key='assistant-5')

    assert first == second
    assert first.exists()
    data = yaml.safe_load(first.read_text(encoding='utf-8'))
    assert data['conversation_id'] == 'session-123'
    pending_files = list((queue_root / 'pending').glob('*.yaml'))
    assert len(pending_files) == 1


def test_handle_session_end_reads_session_db_queues_candidate_and_sweeps_pending(tmp_path: Path) -> None:
    module = load_module()
    wiki_root = tmp_path / 'wiki'
    state_db = tmp_path / 'state.db'
    write_text(wiki_root / 'SCHEMA.md', '# schema\n')
    sweep_calls = []

    class FakeDB:
        def __init__(self, db_path=None):
            assert db_path == state_db

        def get_messages(self, session_id):
            assert session_id == 'session-123'
            return [
                make_message('user', 'How should Hermes capture durable answers automatically?', 10.0),
                make_message('assistant', 'Use on_session_end to create a conservative candidate payload after each completed run.', 11.0),
            ]

    def fake_sweep(queue_root: Path):
        sweep_calls.append(queue_root)
        return ['FILE_DURABLE_STATUS: FILED']

    module.SessionDB = FakeDB
    module.resolve_wiki_root = lambda: wiki_root
    module.resolve_state_db_path = lambda: state_db
    module.sweep_pending_payloads = fake_sweep

    result = module.handle_session_end(session_id='session-123', completed=True, interrupted=False, platform='slack')

    assert result is not None
    queued = Path(result)
    assert queued.exists()
    payload = yaml.safe_load(queued.read_text(encoding='utf-8'))
    assert payload['conversation_id'] == 'session-123'
    assert payload['source_context'] == ['slack session session-123']
    assert sweep_calls == [wiki_root / '_inbox' / 'durable-answers']


def test_handle_session_end_falls_back_to_session_json_when_db_has_no_messages(tmp_path: Path) -> None:
    module = load_module()
    wiki_root = tmp_path / 'wiki'
    state_db = tmp_path / 'state.db'
    sessions_dir = tmp_path / 'sessions'
    write_text(wiki_root / 'SCHEMA.md', '# schema\n')
    write_text(
        sessions_dir / 'session-session-456.json',
        '{\n'
        '  "session_id": "session-456",\n'
        '  "platform": "cli",\n'
        '  "session_start": "2026-04-15T01:23:45",\n'
        '  "messages": [\n'
        '    {"role": "user", "content": "How should fallback session transcript loading work for durable answers?"},\n'
        '    {"role": "assistant", "content": "If the SQLite message log is empty, the hook should read the saved session transcript JSON and still queue the durable-answer candidate."}\n'
        '  ]\n'
        '}\n',
    )
    sweep_calls = []

    class FakeDB:
        def __init__(self, db_path=None):
            assert db_path == state_db

        def get_messages(self, session_id):
            assert session_id == 'session-456'
            return []

    def fake_sweep(queue_root: Path):
        sweep_calls.append(queue_root)
        return ['FILE_DURABLE_STATUS: FILED']

    module.SessionDB = FakeDB
    module.resolve_wiki_root = lambda: wiki_root
    module.resolve_state_db_path = lambda: state_db
    module.resolve_session_json_path = lambda session_id: sessions_dir / f'session-{session_id}.json'
    module.sweep_pending_payloads = fake_sweep

    result = module.handle_session_end(session_id='session-456', completed=True, interrupted=False, platform='cli')

    assert result is not None
    queued = Path(result)
    assert queued.exists()
    payload = yaml.safe_load(queued.read_text(encoding='utf-8'))
    assert payload['conversation_id'] == 'session-456'
    assert payload['captured_at'] == '2026-04-15'
    assert payload['question'].startswith('How should fallback session transcript loading work')
    assert sweep_calls == [wiki_root / '_inbox' / 'durable-answers']


def test_resolve_queue_runner_script_path_falls_back_when_hermes_home_has_no_scripts(tmp_path: Path) -> None:
    module = load_module()
    module.hermes_home = lambda: tmp_path / 'isolated-home'

    script_path = module.resolve_queue_runner_script_path()

    assert script_path == REPO_ROOT / 'scripts' / 'wiki_file_durable_answer_queue.py'


def test_resolve_wiki_root_falls_back_to_root_config_when_profile_config_missing(tmp_path: Path) -> None:
    module = load_module()
    current_home = tmp_path / 'profiles' / 'agent'
    root_home = tmp_path / '.hermes'
    root_home.mkdir(parents=True, exist_ok=True)
    write_text(
        root_home / 'config.yaml',
        'skills:\n  config:\n    wiki:\n      path: /tmp/fallback-wiki\n',
    )
    module.hermes_home = lambda: current_home
    module.Path.home = lambda: tmp_path

    wiki_root = module.resolve_wiki_root()

    assert str(wiki_root) == '/tmp/fallback-wiki'
