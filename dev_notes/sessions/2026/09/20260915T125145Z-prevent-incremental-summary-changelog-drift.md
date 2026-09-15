<!-- dev-notes: {"id":"20260915T125145Z-prevent-incremental-summary-changelog-drift","created_at":"2026-09-15T12:51:45Z","title":"Prevent incremental summary changelog drift","status":"completed","tags":["prompt","quality","realtime","summary"],"files":["README.md","backend/server.py","dist/app.js","tests/test_server.py"],"branch":"main","commit":"f3c7cff"} -->

# Prevent incremental summary changelog drift

## Outcome
- Fixed incremental summaries that leaked process language such as “新加入的逐字稿” and accumulated duplicate bullet points.
- Reworked the MLX prompt to always produce standalone current meeting notes, explicitly forbid update-log language, merge semantically overlapping points, and cap section lengths.
- Added an automatic full-summary rebase after every six new segments when the full transcript remains under 20,000 estimated characters.
- Added a prompt regression test.

## Decisions and rationale
- Treat the previous summary as an editable draft rather than an append-only record so the model is encouraged to remove repetition and stale phrasing.
- Periodically rebuild from the transcript to limit compounding errors from recursive incremental summarization.
- Keep incremental updates between rebases for latency and context efficiency.

## Verification
- python3 -m unittest tests.test_server passed: 12 tests in 9.068s.
- python3 -m py_compile backend/server.py tests/test_server.py, node --check dist/app.js, and git diff --check passed.
- Confirmed an older server process is still listening on 127.0.0.1:8787; it must be restarted before the new backend prompt takes effect.

## Next step
- Restart the live service and use the manual full refresh once to replace the already contaminated in-browser summary.
- Evaluate the corrected prompt on the representative recording and tune the six-segment rebase interval if needed.
