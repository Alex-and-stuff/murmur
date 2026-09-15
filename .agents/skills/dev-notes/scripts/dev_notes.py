#!/usr/bin/env python3
"""Create and search a repo-local, Markdown-backed development journal."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
from pathlib import Path

STATUSES = ("active", "blocked", "completed", "abandoned")
META_RE = re.compile(r"^<!-- dev-notes: (\{.*\}) -->$")
TOKEN_RE = re.compile(r"[A-Za-z0-9_./:-]+")


def csv(value: str) -> list[str]:
    return sorted({part.strip() for part in value.split(",") if part.strip()})


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:60] or "session"


def notes_root(repo: Path) -> Path:
    return repo.resolve() / "dev_notes"


def git_value(repo: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
        )
        return result.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def read_entries(root: Path) -> list[dict]:
    entries: list[dict] = []
    for path in sorted((root / "sessions").glob("**/*.md")):
        try:
            first, _, body = path.read_text(encoding="utf-8").partition("\n")
            match = META_RE.match(first)
            if not match:
                continue
            metadata = json.loads(match.group(1))
            metadata["path"] = path.relative_to(root).as_posix()
            metadata["body"] = body.strip()
            entries.append(metadata)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
    return sorted(entries, key=lambda item: item.get("created_at", ""), reverse=True)


def rebuild(root: Path) -> list[dict]:
    root.mkdir(parents=True, exist_ok=True)
    entries = read_entries(root)
    machine = [{key: value for key, value in item.items() if key != "body"} for item in entries]
    (root / "index.json").write_text(
        json.dumps({"version": 1, "entries": machine}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    lines = ["# Development Notes", "", "Generated index. Session files are the source of truth.", ""]
    for status in STATUSES:
        matching = [item for item in entries if item.get("status") == status]
        if not matching:
            continue
        lines.extend([f"## {status.title()}", ""])
        for item in matching:
            tags = ", ".join(item.get("tags", []))
            suffix = f" — {tags}" if tags else ""
            lines.append(f"- [{item.get('title', item['id'])}]({item['path']}) — {item.get('created_at', '')}{suffix}")
        lines.append("")
    (root / "INDEX.md").write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return entries


def add(args: argparse.Namespace, repo: Path) -> int:
    root = notes_root(repo)
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    session_id = f"{stamp}-{slugify(args.title)}"
    directory = root / "sessions" / now.strftime("%Y") / now.strftime("%m")
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{session_id}.md"
    counter = 2
    while path.exists():
        path = directory / f"{session_id}-{counter}.md"
        counter += 1
    body = sys.stdin.read().strip()
    metadata = {
        "id": path.stem,
        "created_at": now.isoformat().replace("+00:00", "Z"),
        "title": args.title.strip(),
        "status": args.status,
        "tags": csv(args.tags),
        "files": csv(args.files),
        "branch": args.branch or git_value(repo, "branch", "--show-current"),
        "commit": args.commit or git_value(repo, "rev-parse", "--short", "HEAD"),
    }
    rendered = f"<!-- dev-notes: {json.dumps(metadata, ensure_ascii=False, separators=(',', ':'))} -->\n\n# {args.title.strip()}\n"
    if body:
        rendered += f"\n{body}\n"
    path.write_text(rendered, encoding="utf-8")
    rebuild(root)
    print(path)
    return 0


def query(args: argparse.Namespace, repo: Path) -> int:
    entries = rebuild(notes_root(repo))
    terms = [token.lower() for token in TOKEN_RE.findall(args.text or "")]
    ranked = []
    for position, item in enumerate(entries):
        haystack = " ".join([item.get("title", ""), *item.get("tags", []), *item.get("files", []), item.get("body", "")]).lower()
        score = sum(4 if term in item.get("title", "").lower() else 1 for term in terms if term in haystack)
        if not terms or score:
            ranked.append((score, -position, item))
    ranked.sort(reverse=True, key=lambda row: (row[0], row[1]))
    for score, _, item in ranked[: args.limit]:
        print(f"{item['path']}\t{item.get('status', '')}\t{item.get('created_at', '')}\t{item.get('title', '')}\tscore={score}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, type=Path)
    sub = parser.add_subparsers(dest="command", required=True)
    add_parser = sub.add_parser("add", help="Add an immutable session checkpoint from stdin")
    add_parser.add_argument("--title", required=True)
    add_parser.add_argument("--status", choices=STATUSES, default="active")
    add_parser.add_argument("--tags", default="")
    add_parser.add_argument("--files", default="")
    add_parser.add_argument("--branch", default="")
    add_parser.add_argument("--commit", default="")
    sub.add_parser("reindex", help="Rebuild INDEX.md and index.json")
    query_parser = sub.add_parser("query", help="Search note metadata and Markdown bodies")
    query_parser.add_argument("--text", default="")
    query_parser.add_argument("--limit", type=int, default=5)
    args = parser.parse_args()
    repo = args.repo.resolve()
    if not repo.is_dir():
        parser.error(f"repository path is not a directory: {repo}")
    if args.command == "add":
        return add(args, repo)
    if args.command == "reindex":
        rebuild(notes_root(repo))
        print(notes_root(repo) / "INDEX.md")
        return 0
    return query(args, repo)


if __name__ == "__main__":
    raise SystemExit(main())
