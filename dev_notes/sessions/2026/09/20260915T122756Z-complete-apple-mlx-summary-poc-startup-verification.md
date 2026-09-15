<!-- dev-notes: {"id":"20260915T122756Z-complete-apple-mlx-summary-poc-startup-verification","created_at":"2026-09-15T12:27:56Z","title":"Complete Apple MLX summary POC startup verification","status":"completed","tags":["asr","mlx","poc","startup","summary"],"files":["README.md","backend/server.py","dist/app.js","dist/index.html","dist/styles.css","tests/test_server.py"],"branch":"main","commit":"9afc64a"} -->

# Complete Apple MLX summary POC startup verification

## Outcome
- Completed the Apple Silicon MLX Summary POC and verified the serialized dual-model startup fix in the live service.
- This checkpoint completes the earlier active note `20260915T122404Z-serialize-mlx-asr-and-summary-startup`.

## Verification
- Live `GET /api/health` after restarting the updated server reported both backends ready: `Qwen3-ASR 1.7B · MLX 8-bit` and `Qwen/Qwen3-8B-MLX-4bit · MLX`, with no errors.
- `python3 -m unittest tests.test_server` passed: 9 tests in 10.069s.
- `python3 -m py_compile backend/server.py`, `node --check dist/app.js`, and `git diff --check` passed.

## Open questions / next step
- Evaluate summary factuality and usefulness on representative full-length transcripts.
- Measure combined unified-memory usage and latency before beginning the later RTX 2000 Ada backend abstraction.
