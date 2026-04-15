#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
from typing import Optional

from hermes_state import SessionDB


def load_durable_plugin_module():
    plugin_path = Path(__file__).resolve().parents[1] / 'plugins' / 'durable_answer_on_session_end' / '__init__.py'
    spec = importlib.util.spec_from_file_location('durable_answer_on_session_end_runtime', plugin_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f'Unable to load durable answer plugin from {plugin_path}')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PLUGIN = load_durable_plugin_module()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='Prepare a durable-answer payload from a selected exchange in a Hermes session.'
    )
    parser.add_argument('--session-id', required=True, help='Hermes session ID to inspect.')
    parser.add_argument('--assistant-message-id', type=int, help='Pick a specific assistant message ID; defaults to the latest exchange.')
    parser.add_argument('--output', help='Write payload YAML to this path.')
    parser.add_argument('--enqueue', action='store_true', help='Write payload into the wiki durable-answer pending queue.')
    parser.add_argument('--verbose', action='store_true', help='Print extra details.')
    return parser.parse_args()


def hermes_home() -> Path:
    return Path(os.environ.get('HERMES_HOME', str(Path.home() / '.hermes'))).expanduser()


def resolve_state_db_path() -> Path:
    return hermes_home() / 'state.db'


def resolve_session_json_path(session_id: str) -> Path:
    return hermes_home() / 'sessions' / f'session_{session_id}.json'


def load_session_messages(db: SessionDB, session_id: str) -> list[dict]:
    messages = db.get_messages(session_id)
    if messages:
        return messages
    session_json_path = resolve_session_json_path(session_id)
    if not session_json_path.exists():
        return []
    data = json.loads(session_json_path.read_text(encoding='utf-8'))
    raw_messages = data.get('messages') or []
    session_start = data.get('session_start')
    if session_start:
        try:
            parsed = PLUGIN.datetime.fromisoformat(session_start.replace('Z', '+00:00'))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=PLUGIN.timezone.utc)
            base_ts = parsed.timestamp()
        except Exception:
            base_ts = 0.0
    else:
        base_ts = 0.0
    normalized: list[dict] = []
    for idx, msg in enumerate(raw_messages, start=1):
        normalized.append({
            'id': msg.get('id', idx),
            'role': msg.get('role'),
            'content': msg.get('content'),
            'timestamp': msg.get('timestamp', base_ts + idx),
        })
    return normalized


def extract_exchange_candidates(messages: list[dict]) -> list[dict]:
    exchanges: list[dict] = []
    pending_user: Optional[dict] = None
    for msg in messages:
        role = msg.get('role')
        content = PLUGIN.clean_text(msg.get('content'))
        if not content:
            continue
        if role == 'user':
            pending_user = msg
            continue
        if role == 'assistant' and pending_user is not None:
            exchanges.append({'user': pending_user, 'assistant': msg})
            pending_user = None
    return exchanges


def select_exchange(exchanges: list[dict], assistant_message_id: int | None = None) -> dict:
    if not exchanges:
        raise RuntimeError('No user→assistant exchanges found in session')
    if assistant_message_id is None:
        return exchanges[-1]
    for exchange in exchanges:
        if int(exchange['assistant'].get('id')) == int(assistant_message_id):
            return exchange
    raise RuntimeError(f'Assistant message id not found in exchange list: {assistant_message_id}')


def infer_platform(db: SessionDB, session_id: str) -> str:
    session = db.get_session(session_id) or {}
    return PLUGIN.clean_text(session.get('source')) or 'unknown'


def payload_for_exchange(session_id: str, exchange: dict, platform: str) -> dict:
    user_text = PLUGIN.clean_text(exchange['user'].get('content'))
    assistant_text = PLUGIN.clean_text(exchange['assistant'].get('content'))
    ts = float(exchange['assistant'].get('timestamp') or exchange['user'].get('timestamp') or 0)
    return PLUGIN.build_payload(
        session_id=session_id,
        platform=platform,
        user_text=user_text,
        assistant_text=assistant_text,
        timestamp=ts,
    )


def default_output_path(payload: dict) -> Path:
    return hermes_home() / 'tmp' / 'durable-answer-payloads' / f"{payload['captured_at']}-{payload['suggested_slug']}.yaml"


def write_payload(output_path: Path, payload: dict) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(PLUGIN.yaml.safe_dump(payload, sort_keys=False, allow_unicode=True), encoding='utf-8')
    return output_path


def prepare_payload_from_session(session_id: str, assistant_message_id: int | None = None, output_path: Path | None = None, enqueue: bool = False) -> Path:
    db = SessionDB(resolve_state_db_path())
    messages = load_session_messages(db, session_id)
    exchanges = extract_exchange_candidates(messages)
    exchange = select_exchange(exchanges, assistant_message_id=assistant_message_id)
    platform = infer_platform(db, session_id)
    payload = payload_for_exchange(session_id, exchange, platform)
    if enqueue:
        queue_root = PLUGIN.resolve_wiki_root() / '_inbox' / 'durable-answers'
        assistant_id = exchange['assistant'].get('id') or exchange['assistant'].get('timestamp') or 'assistant'
        return PLUGIN.queue_payload(queue_root, payload, unique_key=f'{session_id}:{assistant_id}')
    target = Path(output_path) if output_path else default_output_path(payload)
    return write_payload(target, payload)


def main() -> int:
    args = parse_args()
    try:
        written = prepare_payload_from_session(
            session_id=args.session_id,
            assistant_message_id=args.assistant_message_id,
            output_path=Path(args.output).expanduser() if args.output else None,
            enqueue=args.enqueue,
        )
        if args.verbose:
            print(f'WROTE_PAYLOAD: {written}')
        print(written)
        return 0
    except Exception as exc:
        print(f'ERROR: {exc}')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
