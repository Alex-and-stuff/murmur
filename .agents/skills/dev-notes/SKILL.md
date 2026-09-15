---
name: dev-notes
description: Record, update, retrieve, or summarize repository development history across agent sessions using the repo-local ./dev_notes store. Use for session handoffs, progress checkpoints, recalling prior decisions or failed approaches, and preparing context for continued work; do not use for ordinary user-facing release notes.
---

# Dev Notes

Keep concise, auditable development memory in `<repo>/dev_notes`. Treat code, tests, commits, and canonical project documentation as ground truth; notes explain state and decisions but never override them.

## Locate the repository

Use the nearest Git worktree root when available, otherwise the current working directory. Run the helper from this skill directory:

```bash
python3 scripts/dev_notes.py --repo <repo> <command>
```

Paths passed to `--repo` must be explicit. Never assume that the skill's own directory is the target repository.

## Retrieve context

Before continuing prior work or answering questions about project history:

1. Run `query` with 2–5 concrete terms from the task. With no query, it returns the most recent notes.
2. Read only the matching session files printed by the query.
3. Verify stale or consequential claims against the current tree and Git history.

```bash
python3 scripts/dev_notes.py --repo <repo> query --text "auth refresh token" --limit 5
```

Do not load every historical note by default. `dev_notes/INDEX.md` is a navigation aid; `dev_notes/index.json` is a generated search index and may always be rebuilt.

## Record a checkpoint

Record a note when the user requests it, before a handoff or context switch, after a meaningful milestone, or when a non-obvious decision/failure will save future work. Do not create notes for trivial reads or commentary-only turns.

Write a compact Markdown body to stdin and invoke `add`:

```bash
python3 scripts/dev_notes.py --repo <repo> add \
  --title "Implement refresh-token rotation" \
  --status active \
  --tags auth,api \
  --files src/auth.ts,tests/auth.test.ts \
  --branch feature/auth \
  --commit abc123 <<'EOF'
## Outcome
...

## Decisions and rationale
...

## Verification
...

## Open questions / next step
...
EOF
```

Allowed statuses are `active`, `blocked`, `completed`, and `abandoned`. Include only sections that carry useful information. State failed approaches and why they failed when relevant. Record exact verification commands and outcomes. Never store secrets, credentials, private keys, access tokens, raw environment dumps, or hidden chain-of-thought. Summarize reasoning as decisions and evidence.

The helper creates one immutable timestamped file per checkpoint and rebuilds both indexes. If an existing note needs correction, add a new note that references or supersedes it; avoid silently rewriting history.

## Maintain the index

Run this after manual moves, merges, or conflict resolution:

```bash
python3 scripts/dev_notes.py --repo <repo> reindex
```

Read [references/format.md](references/format.md) only when editing notes or index tooling manually.

