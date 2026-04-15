#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import os
import shutil
from pathlib import Path
from typing import Optional

QUEUE_SUBDIR = Path('_inbox') / 'durable-answers'
QUEUE_BUCKETS = ('pending', 'filed', 'skipped', 'escalated', 'error')
NO_PENDING_PAYLOADS = 'NO_PENDING_PAYLOADS'


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='Minimal queue runner for durable-answer payload automation.'
    )
    parser.add_argument('--wiki-path', help='Explicit wiki root override.')
    parser.add_argument('--profile', help='Hermes profile to read wiki.path from if --wiki-path is omitted.')
    parser.add_argument('--dry-run', action='store_true', help='Print intended action without moving payload files.')
    parser.add_argument('--verbose', action='store_true', help='Print extra details.')
    return parser.parse_args()


def load_runner_module():
    script_path = Path(__file__).resolve().parent / 'wiki_file_durable_answer.py'
    spec = importlib.util.spec_from_file_location('wiki_file_durable_answer_runtime', script_path)
    if spec is None or spec.loader is None:
        raise SystemExit(f'Unable to load durable-answer runner from {script_path}')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def resolve_wiki_path(explicit: Optional[str], profile: Optional[str]) -> Path:
    runner = load_runner_module()
    return runner.resolve_wiki_path(explicit, profile)


def ensure_queue_layout(wiki_root: Path) -> Path:
    queue_root = wiki_root / QUEUE_SUBDIR
    for bucket in QUEUE_BUCKETS:
        (queue_root / bucket).mkdir(parents=True, exist_ok=True)
    return queue_root


def pending_payloads(queue_root: Path) -> list[Path]:
    pending_dir = queue_root / 'pending'
    candidates = [path for path in pending_dir.iterdir() if path.is_file() and path.suffix.lower() in {'.yaml', '.yml', '.json'}]
    return sorted(candidates, key=lambda path: (path.stat().st_mtime, path.name.lower()))


def destination_bucket_for_status(status: str) -> str:
    return {
        'FILE_DURABLE_STATUS: FILED': 'filed',
        'FILE_DURABLE_STATUS: SKIPPED': 'skipped',
        'FILE_DURABLE_STATUS: ESCALATED': 'escalated',
    }.get(status, 'error')


def move_payload(payload_path: Path, queue_root: Path, bucket: str) -> Path:
    destination = queue_root / bucket / payload_path.name
    if destination.exists():
        destination.unlink()
    return Path(shutil.move(str(payload_path), str(destination)))


def process_payload_path(payload_path: Path, wiki_root: Path, queue_root: Path, dry_run: bool = False, verbose: bool = False) -> str:
    runner = load_runner_module()
    if verbose or dry_run:
        print(f'PAYLOAD_QUEUE_ITEM: {payload_path}')

    try:
        status = runner.run(payload_path, wiki_root, dry_run=dry_run, verbose=verbose)
    except Exception as exc:
        if verbose:
            print(f'RUNNER_ERROR: {exc}')
        if not dry_run:
            move_payload(payload_path, queue_root, 'error')
        return 'FILE_DURABLE_STATUS: ERROR'

    if not dry_run:
        move_payload(payload_path, queue_root, destination_bucket_for_status(status))
    return status


def process_next_payload(wiki_root: Path, queue_root: Path, dry_run: bool = False, verbose: bool = False) -> str:
    items = pending_payloads(queue_root)
    if not items:
        if verbose:
            print(NO_PENDING_PAYLOADS)
        return NO_PENDING_PAYLOADS

    return process_payload_path(items[0], wiki_root, queue_root, dry_run=dry_run, verbose=verbose)


def process_all_pending(wiki_root: Path, queue_root: Path, dry_run: bool = False, verbose: bool = False) -> list[str]:
    statuses: list[str] = []
    while True:
        status = process_next_payload(wiki_root, queue_root, dry_run=dry_run, verbose=verbose)
        if status == NO_PENDING_PAYLOADS:
            break
        statuses.append(status)
        if dry_run:
            break
    return statuses


def main() -> int:
    args = parse_args()
    try:
        wiki_root = resolve_wiki_path(args.wiki_path, args.profile)
        queue_root = ensure_queue_layout(wiki_root)
        status = process_next_payload(wiki_root, queue_root, dry_run=args.dry_run, verbose=args.verbose)
        print(status)
        return 0
    except SystemExit as exc:
        message = str(exc)
        if message and message != '0':
            print(message)
        print('FILE_DURABLE_STATUS: ERROR')
        return 1
    except Exception as exc:  # pragma: no cover
        print(f'ERROR: {exc}')
        print('FILE_DURABLE_STATUS: ERROR')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
