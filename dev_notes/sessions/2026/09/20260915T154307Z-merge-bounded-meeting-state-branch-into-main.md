<!-- dev-notes: {"id":"20260915T154307Z-merge-bounded-meeting-state-branch-into-main","created_at":"2026-09-15T15:43:07Z","title":"Merge bounded meeting state branch into main","status":"completed","tags":["branch","meeting-state","merge","refactor"],"files":["README.md","backend/http_app.py","backend/llm.py","backend/meeting/","dist/app.js"],"branch":"feat/online-summary","commit":"304048f"} -->

# Merge bounded meeting state branch into main

## Outcome
`feat/online-summary` carried two pieces of work, recorded in detail in
`20260915T153330Z-split-the-backend-server-into-focused-modules.md` and
`20260915T153833Z-replace-the-rolling-summary-with-bounded-meeting-state.md`:
the backend module split (`bca36e3`), and the replacement of the rolling summary
with bounded meeting state (`5cc8175`). The branch is merged back into `main`
with `--no-ff` so the two-step history stays visible.

## Decisions and rationale
- `--no-ff` at the user's request: the refactor and the redesign belong together
  as one reviewable unit, and the merge commit keeps that boundary.
- The dev notes for both commits were written before the merge, so the history
  on `main` carries its own explanation.

## Verification
- `python3 -m unittest discover -s tests -t .` — 31 tests, OK.
- One real rollout against `Qwen/Qwen3-8B-MLX-4bit` in the MLX env produced
  valid JSON, a correct `new_section`, and an archived index entry.

## Open questions / next step
- `origin/feat/online-summary` was pushed at `bca36e3` by something outside this
  session (IDE Git integration; the repo has no custom hooks). Nothing has been
  pushed since, so the remote branch is behind. Decide whether to push `main` or
  delete the stale remote branch.
- Not yet implemented: the Qwen3.5 122B-A10B adapter behind the finalizer
  interface, and any measurement of return-to-section accuracy on annotated
  transcripts.
