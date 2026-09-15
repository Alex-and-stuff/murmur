<!-- dev-notes: {"id":"20260915T153330Z-split-the-backend-server-into-focused-modules","created_at":"2026-09-15T15:33:30Z","title":"Split the backend server into focused modules","status":"completed","tags":["architecture","backend","refactor","tests"],"files":["README.md","backend/asr.py","backend/config.py","backend/http_app.py","backend/media.py","backend/server.py","backend/summary.py","tests/test_http.py","tests/test_summary.py"],"branch":"feat/online-summary","commit":"bca36e3"} -->

# Split the backend server into focused modules

## Outcome
- Split `backend/server.py`, which had grown to roughly 633 lines holding constants, ASR backends, summary backends, the YouTube fetcher, the HTTP handler and the CLI, into focused modules.
- `backend/config.py`: paths, sample rate, body-size limits, and default model names.
- `backend/asr.py`: the `ASRBackend` protocol, `MLXQwenBackend`, `FixtureBackend`, and `BackendState`.
- `backend/summary.py`: the summary backends, `normalize_summary`, and `SummaryState`.
- `backend/media.py`: the YouTube allow-list, `YtDlpFetcher`, and `MediaFetchError`.
- `backend/http_app.py`: `MurmurHandler` and `create_server`.
- `backend/server.py` is now only the CLI entry point; it inserts the repo root on `sys.path` so `python backend/server.py` keeps working.
- Behaviour was unchanged in this commit. `tests/test_server.py` was split along the same lines into `tests/test_summary.py` and `tests/test_http.py`.

## Decisions and rationale
- The refactor is preparation, not a behaviour change: it makes room for the bounded hierarchical meeting-memory work described in `dev_notes/plan/CONTEXT_IMPLEMENTATION_PLAN.md`. Keeping the split behaviour-neutral means any later regression is attributable to the new work rather than to the move.
- `server.py` stays as the CLI entry point and keeps the `sys.path` insertion so existing run instructions and habits (`python backend/server.py`) are unaffected by the package-style imports.
- Tests were split to mirror the module boundaries, so each test file has a single obvious subject.

## Verification
- All 12 tests passed via the new command `python -m unittest discover -s tests -t .`, which also replaced the test command documented in README.md.

## Open questions / next step
- The bounded hierarchical meeting-memory implementation is in progress on the same branch; it replaces `backend/summary.py` with `backend/llm.py` and adds a `backend/meeting/` package. That work is recorded separately.
