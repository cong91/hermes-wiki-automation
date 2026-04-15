#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

import yaml

STATUS_FILED = "FILE_DURABLE_STATUS: FILED"
STATUS_SKIPPED = "FILE_DURABLE_STATUS: SKIPPED"
STATUS_ESCALATED = "FILE_DURABLE_STATUS: ESCALATED"
STATUS_ERROR = "FILE_DURABLE_STATUS: ERROR"
REQUIRED_FIELDS = (
    "captured_at",
    "question",
    "answer",
    "why_durable",
    "suggested_target",
    "source_context",
)
FRONTMATTER_UPDATED_RE = re.compile(r"(?m)^updated:\s*\d{4}-\d{2}-\d{2}$")
INDEX_UPDATED_RE = re.compile(r"(?m)^> Last updated: .*$")
INDEX_TOTAL_RE = re.compile(r"Total indexed pages: (\d+)")
LOW_REUSE_PATTERNS = (
    "progress update",
    "routine progress",
    "temporary",
    "temp",
    "status only",
    "no likely reuse",
    "one-off",
    "ephemeral",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Minimal shared-wiki durable-answer filing runner."
    )
    parser.add_argument("--payload", required=True, help="Path to one prepared YAML/JSON payload.")
    parser.add_argument("--wiki-path", help="Explicit wiki root override.")
    parser.add_argument("--profile", help="Hermes profile to read wiki.path from if --wiki-path is omitted.")
    parser.add_argument("--dry-run", action="store_true", help="Print intended action without writing files.")
    parser.add_argument("--verbose", action="store_true", help="Print extra details.")
    return parser.parse_args()


def hermes_home() -> Path:
    return Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes"))).expanduser()


def load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def resolve_wiki_path(explicit: Optional[str], profile: Optional[str]) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()

    base = hermes_home()
    config_paths = [base / "config.yaml"]
    if profile:
        config_paths.append(base / "profiles" / profile / "config.yaml")

    for config_path in config_paths:
        data = load_yaml(config_path)
        wiki_path = data.get("skills", {}).get("config", {}).get("wiki", {}).get("path")
        if wiki_path:
            return Path(wiki_path).expanduser().resolve()

    raise SystemExit(
        "Could not resolve wiki path from --wiki-path or Hermes config (skills.config.wiki.path)."
    )


def ensure_wiki_layout(wiki_root: Path) -> None:
    required = [
        wiki_root / "SCHEMA.md",
        wiki_root / "index.md",
        wiki_root / "log.md",
        wiki_root / "_meta" / "filed-queries.md",
        wiki_root / "queries",
    ]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise SystemExit("Wiki scaffolding incomplete. Missing: " + ", ".join(missing))


def clean_text(value: object) -> str:
    return " ".join(str(value or "").strip().split())


def slugify(value: object) -> str:
    cleaned = clean_text(value).lower()
    cleaned = re.sub(r"[^a-z0-9]+", "-", cleaned)
    return cleaned.strip("-")


def titleize_slug(slug: str) -> str:
    return " ".join(part.capitalize() for part in slug.split("-") if part)


def load_payload(path: Path | str) -> dict:
    payload_path = Path(path).expanduser().resolve()
    if not payload_path.exists():
        raise SystemExit(f"Payload file does not exist: {payload_path}")

    text = payload_path.read_text(encoding="utf-8")
    suffix = payload_path.suffix.lower()
    if suffix == ".json":
        data = json.loads(text)
    elif suffix in {".yaml", ".yml"}:
        data = yaml.safe_load(text)
    else:
        raise SystemExit("Payload must be .json, .yaml, or .yml")

    if not isinstance(data, dict):
        raise SystemExit("Payload must decode to a mapping/object")

    normalized = {key: data.get(key) for key in data}
    for field in REQUIRED_FIELDS:
        value = normalized.get(field)
        if field == "source_context":
            if not isinstance(value, list) or not [clean_text(item) for item in value if clean_text(item)]:
                raise SystemExit(f"Payload missing required field: {field}")
            normalized[field] = [clean_text(item) for item in value if clean_text(item)]
            continue
        if not clean_text(value):
            raise SystemExit(f"Payload missing required field: {field}")
        normalized[field] = clean_text(value)

    normalized["suggested_target"] = normalized["suggested_target"].lower()
    normalized["conversation_id"] = clean_text(normalized.get("conversation_id")) or None
    normalized["suggested_slug"] = slugify(normalized.get("suggested_slug")) or None
    return normalized


def is_low_reuse_payload(payload: dict) -> bool:
    answer = clean_text(payload.get("answer"))
    why = clean_text(payload.get("why_durable")).lower()
    if len(answer) < 25:
        return True
    return any(pattern in why for pattern in LOW_REUSE_PATTERNS)


def is_nontrivial_answer(payload: dict) -> bool:
    question = clean_text(payload.get("question"))
    answer = clean_text(payload.get("answer"))
    why = clean_text(payload.get("why_durable"))
    return len(question) >= 20 and len(answer) >= 40 and len(why) >= 20


def decide_action(payload: dict) -> tuple[str, str]:
    target = payload["suggested_target"]
    if target in {"canonical", "comparison"}:
        return "escalate", "target-needs-human-judgment"
    if is_low_reuse_payload(payload):
        return "skip", "too-thin-or-low-reuse"
    if target == "query" and is_nontrivial_answer(payload):
        return "file", "query-obvious-and-durable"
    return "escalate", "target-needs-human-judgment"


def derive_query_slug(payload: dict) -> str:
    return payload.get("suggested_slug") or slugify(payload.get("question")) or "durable-answer"


def derive_query_title(payload: dict) -> str:
    slug = derive_query_slug(payload)
    return titleize_slug(slug)


def render_query_page(payload: dict, slug: str) -> str:
    title = derive_query_title(payload)
    captured_at = payload["captured_at"]
    context_bullets = "\n".join(f"- {item}" for item in payload["source_context"])
    conversation_line = (
        f"- conversation id: `{payload['conversation_id']}`\n" if payload.get("conversation_id") else ""
    )
    return (
        "---\n"
        f"title: {title}\n"
        f"created: {captured_at}\n"
        f"updated: {captured_at}\n"
        "type: query\n"
        "tags: [ops, hermes, wiki]\n"
        "sources: []\n"
        "---\n\n"
        f"# {title}\n\n"
        "## Question\n"
        f"{payload['question']}\n\n"
        "## Durable answer\n"
        f"{payload['answer']}\n\n"
        "## Why it matters\n"
        f"{payload['why_durable']}\n\n"
        "## Reuse guidance\n"
        "- Reuse this when the same operational question comes up again.\n"
        "- Treat this as a concise durable answer, not a raw transcript.\n"
        f"- Suggested target at capture time: `{payload['suggested_target']}`.\n\n"
        "## Source context\n"
        f"{conversation_line}{context_bullets}\n"
    )


def update_frontmatter_updated(text: str, date_str: str) -> str:
    if FRONTMATTER_UPDATED_RE.search(text):
        return FRONTMATTER_UPDATED_RE.sub(f"updated: {date_str}", text, count=1)
    return text


def replace_none_yet(text: str, bullet: str) -> str:
    marker = "- None yet."
    if marker in text:
        return text.replace(marker, bullet, 1)
    if not text.endswith("\n"):
        text += "\n"
    return text + bullet


def append_filed_registry_entry(registry_path: Path, relative_page_path: str, payload: dict, date_str: str) -> None:
    text = registry_path.read_text(encoding="utf-8")
    text = update_frontmatter_updated(text, date_str)
    bullet = (
        f"- {date_str} | `{relative_page_path}` | standalone | "
        f"{payload['source_context'][0]}\n"
    )
    if f"`{relative_page_path}`" in text:
        registry_path.write_text(text, encoding="utf-8")
        return
    text = replace_none_yet(text, bullet)
    registry_path.write_text(text, encoding="utf-8")


def update_index_header(index_text: str, date_str: str, total_pages: int) -> str:
    replacement = f"> Last updated: {date_str} | Total indexed pages: {total_pages}"
    if INDEX_UPDATED_RE.search(index_text):
        return INDEX_UPDATED_RE.sub(replacement, index_text, count=1)
    return index_text


def current_index_total(index_text: str) -> int:
    match = INDEX_TOTAL_RE.search(index_text)
    return int(match.group(1)) if match else 0


def insert_query_index_entry(index_path: Path, slug: str, title: str, summary: str, date_str: str) -> None:
    index_text = index_path.read_text(encoding="utf-8")
    entry = f"- [[queries/{slug}]] — {summary}."
    if f"[[queries/{slug}]]" in index_text:
        index_path.write_text(update_index_header(index_text, date_str, current_index_total(index_text)), encoding="utf-8")
        return

    lines = index_text.splitlines()
    try:
        queries_idx = lines.index("## Queries")
    except ValueError as exc:
        raise SystemExit("index.md missing '## Queries' section") from exc

    insert_at = len(lines)
    for idx in range(queries_idx + 1, len(lines)):
        line = lines[idx]
        if line.startswith("## ") and idx > queries_idx + 1:
            insert_at = idx
            break
        if line.startswith("- [[queries/"):
            existing_slug = line.split("[[queries/", 1)[1].split("]]", 1)[0]
            if slug < existing_slug:
                insert_at = idx
                break

    lines.insert(insert_at, entry)
    total = current_index_total(index_text) + 1
    updated_text = "\n".join(lines) + "\n"
    updated_text = update_index_header(updated_text, date_str, total)
    index_path.write_text(updated_text, encoding="utf-8")


def append_log_entry(log_path: Path, title: str, relative_page_path: str, payload: dict, date_str: str) -> None:
    text = log_path.read_text(encoding="utf-8")
    if not text.endswith("\n"):
        text += "\n"
    entry = (
        f"## [{date_str}] query | {title}\n"
        f"- Filed durable answer to `{relative_page_path}`\n"
        f"- Why durable: {payload['why_durable']}\n"
        f"- Source context: {payload['source_context'][0]}\n"
    )
    text += entry
    log_path.write_text(text, encoding="utf-8")


def read_orientation(wiki_root: Path) -> dict[str, str]:
    log_path = wiki_root / "log.md"
    log_lines = log_path.read_text(encoding="utf-8").splitlines()
    recent_log = "\n".join(log_lines[-30:])
    return {
        "schema": (wiki_root / "SCHEMA.md").read_text(encoding="utf-8"),
        "index": (wiki_root / "index.md").read_text(encoding="utf-8"),
        "recent_log": recent_log,
        "filed_queries": (wiki_root / "_meta" / "filed-queries.md").read_text(encoding="utf-8"),
    }


def summarize_answer(answer: str, limit: int = 90) -> str:
    cleaned = clean_text(answer)
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "…"


def apply_filing(wiki_root: Path, payload: dict) -> dict:
    date_str = payload["captured_at"]
    slug = derive_query_slug(payload)
    title = derive_query_title(payload)
    page_path = wiki_root / "queries" / f"{slug}.md"
    relative_page_path = f"queries/{slug}.md"
    page_path.write_text(render_query_page(payload, slug), encoding="utf-8")
    append_filed_registry_entry(wiki_root / "_meta" / "filed-queries.md", relative_page_path, payload, date_str)
    insert_query_index_entry(
        wiki_root / "index.md",
        slug,
        title,
        summarize_answer(payload["answer"]),
        date_str,
    )
    append_log_entry(wiki_root / "log.md", title, relative_page_path, payload, date_str)
    return {"page_path": page_path, "slug": slug, "title": title}


def status_for_action(action: str) -> str:
    return {
        "file": STATUS_FILED,
        "skip": STATUS_SKIPPED,
        "escalate": STATUS_ESCALATED,
    }.get(action, STATUS_ERROR)


def run(payload_path: Path | str, wiki_root: Path, dry_run: bool = False, verbose: bool = False) -> str:
    ensure_wiki_layout(wiki_root)
    _orientation = read_orientation(wiki_root)
    payload = load_payload(payload_path)
    action, reason = decide_action(payload)

    if verbose or dry_run:
        print(f"PAYLOAD: {Path(payload_path).expanduser().resolve()}")
        print(f"ACTION: {action}")
        print(f"REASON: {reason}")

    if action == "file":
        slug = derive_query_slug(payload)
        if verbose or dry_run:
            print(f"TARGET: queries/{slug}.md")
        if not dry_run:
            result = apply_filing(wiki_root, payload)
            if verbose:
                print(f"WROTE: {result['page_path']}")
    return status_for_action(action)


def main() -> int:
    args = parse_args()
    try:
        wiki_root = resolve_wiki_path(args.wiki_path, args.profile)
        status = run(args.payload, wiki_root, dry_run=args.dry_run, verbose=args.verbose)
        print(status)
        return 0 if status != STATUS_ERROR else 1
    except SystemExit as exc:
        message = str(exc)
        if message and message != "0":
            print(message)
        print(STATUS_ERROR)
        return 1
    except Exception as exc:  # pragma: no cover - defensive CLI path
        print(f"ERROR: {exc}")
        print(STATUS_ERROR)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
