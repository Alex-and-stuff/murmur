# Dev Notes storage format

`dev_notes` is intentionally portable and contains no required database.

```text
dev_notes/
├── INDEX.md
├── index.json
└── sessions/
    └── YYYY/
        └── MM/
            └── YYYYMMDDTHHMMSSZ-slug.md
```

Each session begins with one machine-readable metadata comment:

```markdown
<!-- dev-notes: {"id":"20260905T150000Z-auth","created_at":"2026-09-05T15:00:00Z","title":"Auth work","status":"active","tags":["auth"],"files":["src/auth.ts"],"branch":"feature/auth","commit":"abc123"} -->
```

The remainder is ordinary Markdown. `index.json` is derived exclusively from these comments. `INDEX.md` is a derived human-readable projection grouped by status and recency. Both generated files use paths relative to `dev_notes` so the directory remains movable.

When resolving merge conflicts, preserve all distinct files under `sessions/`, then run `reindex`. Timestamp collisions receive a numeric suffix at creation time.

