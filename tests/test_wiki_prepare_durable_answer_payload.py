from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import yaml
import os


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / 'scripts' / 'wiki_prepare_durable_answer_payload.py'


def load_module():
    assert SCRIPT_PATH.exists(), f'Missing script: {SCRIPT_PATH}'
    spec = importlib.util.spec_from_file_location('wiki_prepare_durable_answer_payload', SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def make_message(message_id: int, role: str, content: str, ts: float) -> dict:
    return {'id': message_id, 'role': role, 'content': content, 'timestamp': ts}


def test_extract_exchange_candidates_pairs_user_with_following_assistant() -> None:
    module = load_module()
    messages = [
        make_message(1, 'user', 'old question', 1.0),
        make_message(2, 'assistant', 'old answer', 2.0),
        make_message(3, 'user', 'How should Hermes prepare durable-answer payloads from selected exchanges?', 3.0),
        make_message(4, 'assistant', 'Select the assistant reply you want, pair it with the preceding user message, and build the payload from that exchange.', 4.0),
    ]

    exchanges = module.extract_exchange_candidates(messages)

    assert len(exchanges) == 2
    assert exchanges[-1]['assistant']['id'] == 4
    assert exchanges[-1]['user']['id'] == 3


def test_select_exchange_defaults_to_latest() -> None:
    module = load_module()
    exchanges = [
        {'user': make_message(1, 'user', 'first', 1.0), 'assistant': make_message(2, 'assistant', 'first answer', 2.0)},
        {'user': make_message(3, 'user', 'second', 3.0), 'assistant': make_message(4, 'assistant', 'second answer', 4.0)},
    ]

    chosen = module.select_exchange(exchanges)

    assert chosen['assistant']['id'] == 4


def test_select_exchange_can_target_specific_assistant_message_id() -> None:
    module = load_module()
    exchanges = [
        {'user': make_message(1, 'user', 'first', 1.0), 'assistant': make_message(2, 'assistant', 'first answer', 2.0)},
        {'user': make_message(3, 'user', 'second', 3.0), 'assistant': make_message(4, 'assistant', 'second answer', 4.0)},
    ]

    chosen = module.select_exchange(exchanges, assistant_message_id=2)

    assert chosen['assistant']['id'] == 2
    assert chosen['user']['id'] == 1


def test_prepare_payload_from_session_writes_output_file(tmp_path: Path) -> None:
    module = load_module()

    class FakeDB:
        def __init__(self, db_path=None):
            self.db_path = db_path

        def get_messages(self, session_id):
            assert session_id == 'session-123'
            return [
                make_message(10, 'user', 'How should Hermes prepare durable-answer payloads from selected exchanges?', 10.0),
                make_message(11, 'assistant', 'Select the assistant reply you want, pair it with the preceding user message, and build the payload from that exchange.', 11.0),
            ]

        def get_session(self, session_id):
            return {'id': session_id, 'source': 'slack'}

    module.SessionDB = FakeDB
    module.resolve_state_db_path = lambda: tmp_path / 'state.db'
    output_path = tmp_path / 'payload.yaml'

    written = module.prepare_payload_from_session('session-123', output_path=output_path)

    assert written == output_path
    payload = yaml.safe_load(output_path.read_text(encoding='utf-8'))
    assert payload['conversation_id'] == 'session-123'
    assert payload['suggested_target'] == 'query'
    assert payload['source_context'] == ['slack session session-123']


def test_prepare_payload_from_session_falls_back_to_session_json(tmp_path: Path) -> None:
    module = load_module()

    class FakeDB:
        def __init__(self, db_path=None):
            self.db_path = db_path

        def get_messages(self, session_id):
            assert session_id == 'session-456'
            return []

        def get_session(self, session_id):
            return {'id': session_id, 'source': 'cli'}

    module.SessionDB = FakeDB
    module.resolve_state_db_path = lambda: tmp_path / 'state.db'
    module.resolve_session_json_path = lambda session_id: tmp_path / 'sessions' / f'session_{session_id}.json'
    module.write_text = None
    session_json = tmp_path / 'sessions' / 'session_session-456.json'
    session_json.parent.mkdir(parents=True, exist_ok=True)
    session_json.write_text(
        '{\n'
        '  "session_id": "session-456",\n'
        '  "platform": "cli",\n'
        '  "session_start": "2026-04-15T01:23:45",\n'
        '  "messages": [\n'
        '    {"role": "user", "content": "How should fallback session JSON selection work?"},\n'
        '    {"role": "assistant", "content": "If the message DB is empty, the helper should still read the session transcript JSON and export a durable-answer payload."}\n'
        '  ]\n'
        '}\n',
        encoding='utf-8',
    )
    output_path = tmp_path / 'payload-fallback.yaml'

    written = module.prepare_payload_from_session('session-456', output_path=output_path)

    assert written == output_path
    payload = yaml.safe_load(output_path.read_text(encoding='utf-8'))
    assert payload['conversation_id'] == 'session-456'
    assert payload['captured_at'] == '2026-04-15'
    assert payload['question'].startswith('How should fallback session JSON selection work')


def test_cli_can_enqueue_latest_exchange_into_pending_queue(tmp_path: Path) -> None:
    state_db = tmp_path / 'state.db'
    wiki_root = tmp_path / 'wiki'
    config_path = tmp_path / 'config.yaml'
    config_path.write_text(
        f'skills:\n  config:\n    wiki:\n      path: "{wiki_root}"\n',
        encoding='utf-8',
    )
    (wiki_root / '_inbox' / 'durable-answers' / 'pending').mkdir(parents=True, exist_ok=True)

    hermes_agent_root = Path(os.environ['HERMES_AGENT_ROOT'])
    sys.path.insert(0, str(hermes_agent_root))
    from hermes_state import SessionDB

    db = SessionDB(state_db)
    db.create_session('session-123', source='slack', user_id='u1', model='test')
    db.append_message('session-123', 'user', 'How should Hermes prepare durable-answer payloads from selected exchanges?')
    db.append_message('session-123', 'assistant', 'Select the assistant reply you want, pair it with the preceding user message, and build the payload from that exchange.')

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            '--session-id', 'session-123',
            '--enqueue',
        ],
        check=False,
        capture_output=True,
        text=True,
        env={**__import__('os').environ, 'HERMES_HOME': str(tmp_path), 'HERMES_AGENT_ROOT': __import__('os').environ['HERMES_AGENT_ROOT']},
    )

    assert result.returncode == 0
    queued_path = Path(result.stdout.strip().splitlines()[-1])
    assert queued_path.exists()
    payload = yaml.safe_load(queued_path.read_text(encoding='utf-8'))
    assert payload['conversation_id'] == 'session-123'
    assert payload['source_context'] == ['slack session session-123']
