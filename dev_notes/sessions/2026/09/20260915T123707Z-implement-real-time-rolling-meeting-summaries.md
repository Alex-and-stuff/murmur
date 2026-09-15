<!-- dev-notes: {"id":"20260915T123707Z-implement-real-time-rolling-meeting-summaries","created_at":"2026-09-15T12:37:07Z","title":"Implement real-time rolling meeting summaries","status":"completed","tags":["api","frontend","mlx","realtime","summary"],"files":["README.md","backend/server.py","dist/app.js","dist/index.html","tests/test_server.py"],"branch":"main","commit":"f3c7cff"} -->

# Implement real-time rolling meeting summaries

## Outcome
- Replaced the manual-only Summary v1 flow with automatic rolling summaries.
- The browser waits for two stable transcript segments, generates the first summary after a 1.6-second debounce, then sends only uncovered segments plus the previous structured summary for later updates.
- Kept a manual full-refresh action and added progress copy for initial generation, queued segments, and fully synchronized summaries.
- Added stable client-side segment IDs so seeking and time-based sorting cannot cause an earlier segment to be skipped by the incremental cursor.
- Extended POST /api/summarize with an optional normalized previous_summary and full/incremental response mode.

## Decisions and rationale
- Debounce automatic updates and coalesce transcript segments that arrive while a summary request is running, avoiding parallel summary calls and excessive model churn.
- Start after two segments to give the first summary minimally useful context; once initialized, merge every later stable segment.
- Preserve the last valid summary while an update is pending or fails instead of blanking the panel.
- A manual refresh always rebuilds from the complete current transcript, providing recovery from accumulated incremental-summary drift.

## Verification
- python3 -m unittest tests.test_server passed: 11 tests in 9.569s.
- python3 -m py_compile backend/server.py tests/test_server.py, node --check dist/app.js, and git diff --check passed.
- Browser QA against fixture backends confirmed automatic first generation at two segments, incremental synchronization at three segments, queued-state copy after pause flushed a fourth segment, and automatic catch-up to four synchronized segments.

## Open questions / next step
- Measure real Qwen3-8B MLX summary latency while ASR is active and decide whether ASR/summary GPU work needs explicit scheduling or prioritization.
- Evaluate whether updating after every new segment is too frequent for longer meetings; tune debounce or add a minimum text/time threshold using representative recordings.
- Run a long-meeting factuality test to measure whether incremental merging drifts, then choose an interval for periodic full rebasing.
