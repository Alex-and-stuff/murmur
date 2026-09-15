<!-- dev-notes: {"id":"20260915T143528Z-add-online-summary-control-and-inference-context-diagnostics","created_at":"2026-09-15T14:35:28Z","title":"Add online summary control and inference context diagnostics","status":"completed","tags":["api","debug","frontend","summary"],"files":["README.md","backend/server.py","dist/app.js","dist/index.html","dist/styles.css","tests/test_server.py"],"branch":"main","commit":"5ea4950"} -->

# Add online summary control and inference context diagnostics

## Outcome
- Added an accessible Online summary switch that pauses automatic rolling updates while keeping manual summary generation available.
- Removed the sample visual for audio-only sources; audio now uses a compact player, while uploaded videos still render in the video card.
- Added a per-session inference context panel. ASR rows show audio duration and sample count; summary rows show exact prompt tokens when available, input size, mode, and latency.
- Extended API responses with context metadata and regression assertions.

## Decisions and rationale
- The switch controls background scheduling only, so users can disable automatic model load while retaining an explicit full refresh.
- Preserve video rendering but hide the entire media card for audio rather than replacing the removed debug visual with another placeholder.
- Count the fully rendered MLX chat prompt with the model tokenizer; use input characters as the fixture/fallback display.

## Verification
- `git diff --check`, `node --check dist/app.js`, and Python compilation passed.
- `python3 -m unittest tests.test_server` passed: 12 tests in 9.571s.
- Browser QA with fixture backends confirmed switch states, compact audio layout, and context rows for four ASR and two summary calls.
