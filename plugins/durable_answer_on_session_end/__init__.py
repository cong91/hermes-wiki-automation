from __future__ import annotations

import hashlib
import importlib.util
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml

from hermes_state import SessionDB

logger = logging.getLogger(__name__)
LOW_SIGNAL_PATTERNS = (
    '/help',
    '/status',
    '/commands',
    '/model',
    '/reset',
    '/new',
)
MIN_USER_LEN = 20
MIN_ASSISTANT_LEN = 72


def hermes_home() -> Path:
    return Path(os.environ.get('HERMES_HOME', str(Path.home() / '.hermes'))).expanduser()


def resolve_wiki_root() -> Path:
    config_candidates = [hermes_home() / 'config.yaml', Path.home() / '.hermes' / 'config.yaml']
    for config_path in config_candidates:
        if not config_path.exists():
            continue
        data = yaml.safe_load(config_path.read_text(encoding='utf-8')) or {}
        wiki_path = data.get('skills', {}).get('config', {}).get('wiki', {}).get('path')
        if wiki_path:
            return Path(wiki_path).expanduser().resolve()
    raise RuntimeError('skills.config.wiki.path is not configured')


def resolve_state_db_path() -> Path:
    return hermes_home() / 'state.db'


def resolve_session_json_path(session_id: str) -> Path:
    return hermes_home() / 'sessions' / f'session_{session_id}.json'


def load_session_messages(session_id: str) -> list[dict]:
    db = SessionDB(resolve_state_db_path())
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
            parsed = datetime.fromisoformat(session_start.replace('Z', '+00:00'))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
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


def clean_text(value: object) -> str:
    return ' '.join(str(value or '').strip().split())


def slugify(value: object) -> str:
    text = clean_text(value).lower()
    text = re.sub(r'[^a-z0-9]+', '-', text)
    return text.strip('-')


def extract_last_exchange(messages: list[dict]) -> Optional[dict]:
    assistant = None
    user = None
    for msg in reversed(messages):
        role = msg.get('role')
        content = clean_text(msg.get('content'))
        if not content:
            continue
        if assistant is None and role == 'assistant':
            assistant = msg
            continue
        if assistant is not None and role == 'user':
            user = msg
            break
    if not assistant or not user:
        return None
    return {'user': user, 'assistant': assistant}


def should_queue_exchange(user_text: str, assistant_text: str) -> bool:
    user_clean = clean_text(user_text)
    assistant_clean = clean_text(assistant_text)
    if len(user_clean) < MIN_USER_LEN or len(assistant_clean) < MIN_ASSISTANT_LEN:
        return False
    lowered = user_clean.lower()
    if any(lowered.startswith(pattern) for pattern in LOW_SIGNAL_PATTERNS):
        return False
    return True


def build_payload(session_id: str, platform: str, user_text: str, assistant_text: str, timestamp: float) -> dict:
    captured_at = datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime('%Y-%m-%d')
    question = clean_text(user_text)
    answer = clean_text(assistant_text)
    slug = slugify(question)[:80] or f'durable-answer-{session_id}'
    why = (
        'Captured automatically from on_session_end because the exchange looks reusable '
        'and substantial enough to review for durable wiki filing.'
    )
    return {
        'conversation_id': session_id,
        'captured_at': captured_at,
        'question': question,
        'answer': answer,
        'why_durable': why,
        'suggested_target': 'query',
        'suggested_slug': slug,
        'source_context': [f'{platform or "unknown"} session {session_id}'],
    }


def queue_payload(queue_root: Path, payload: dict, unique_key: str) -> Path:
    pending_dir = queue_root / 'pending'
    pending_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha1(unique_key.encode('utf-8')).hexdigest()[:12]
    filename = f"{payload['captured_at']}-{payload['suggested_slug']}-{digest}.yaml"
    path = pending_dir / filename
    if not path.exists():
        path.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True), encoding='utf-8')
    return path


def resolve_queue_runner_script_path() -> Path:
    current = Path(__file__).resolve()
    candidates = [
        hermes_home() / 'scripts' / 'wiki-file-durable-answer-queue-v1.py',
        current.parents[2] / 'scripts' / 'wiki-file-durable-answer-queue-v1.py',
        Path(__file__).resolve().parents[2] / 'scripts' / 'wiki-file-durable-answer-queue-v1.py',
        Path('/root/.hermes/scripts/wiki-file-durable-answer-queue-v1.py'),
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise RuntimeError(
        'Unable to locate durable-answer queue runner script; checked: '
        + ', '.join(str(path) for path in candidates)
    )


def load_queue_runner_module():
    script_path = resolve_queue_runner_script_path()
    spec = importlib.util.spec_from_file_location('wiki_file_durable_answer_queue_v1_runtime', script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f'Unable to load durable-answer queue runner from {script_path}')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sweep_pending_payloads(queue_root: Path) -> list[str]:
    queue_runner = load_queue_runner_module()
    wiki_root = resolve_wiki_root()
    queue_runner.ensure_queue_layout(wiki_root)
    return queue_runner.process_all_pending(wiki_root, queue_root, dry_run=False, verbose=False)


def handle_session_end(session_id: Optional[str], completed: bool = True, interrupted: bool = False, platform: str = '', **_: dict) -> Optional[str]:
    if not session_id or interrupted or not completed:
        return None
    messages = load_session_messages(session_id)
    exchange = extract_last_exchange(messages)
    if not exchange:
        return None
    user_text = clean_text(exchange['user'].get('content'))
    assistant_text = clean_text(exchange['assistant'].get('content'))
    if not should_queue_exchange(user_text, assistant_text):
        return None
    ts = float(exchange['assistant'].get('timestamp') or exchange['user'].get('timestamp') or datetime.now(tz=timezone.utc).timestamp())
    payload = build_payload(session_id=session_id, platform=platform, user_text=user_text, assistant_text=assistant_text, timestamp=ts)
    assistant_id = exchange['assistant'].get('id') or exchange['assistant'].get('timestamp') or 'assistant'
    queue_root = resolve_wiki_root() / '_inbox' / 'durable-answers'
    queued = queue_payload(queue_root, payload, unique_key=f'{session_id}:{assistant_id}')
    sweep_statuses = sweep_pending_payloads(queue_root)
    logger.info('durable_answer_on_session_end queued candidate: %s', queued)
    if sweep_statuses:
        logger.info('durable_answer_on_session_end swept pending payloads: %s', ', '.join(sweep_statuses))
    return str(queued)


def register(ctx) -> None:
    ctx.register_hook('on_session_end', handle_session_end)
