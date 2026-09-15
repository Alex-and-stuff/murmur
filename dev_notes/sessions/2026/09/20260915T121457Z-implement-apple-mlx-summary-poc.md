<!-- dev-notes: {"id":"20260915T121457Z-implement-apple-mlx-summary-poc","created_at":"2026-09-15T12:14:57Z","title":"Implement Apple MLX summary POC","status":"completed","tags":["asr","frontend","mlx","qwen","summary"],"files":["README.md","backend/server.py","dist/app.js","dist/index.html","dist/styles.css","tests/test_server.py"],"branch":"main","commit":"9afc64a"} -->

# Implement Apple MLX summary POC

## Outcome
- Added a manual Summary v1 flow on top of accumulated timestamped transcript segments.
- Added `POST /api/summarize`, independent summary readiness in `/api/health`, bounded request validation, structured JSON normalization, and failure responses.
- Added an Apple Silicon `mlx-lm` summary runtime using official `Qwen/Qwen3-8B-MLX-4bit`, with thinking disabled and a Traditional Chinese meeting-notes schema: summary, key points, decisions, and action items.
- Added a responsive summary panel with generate/regenerate and copy controls. Summaries are invalidated when the transcript changes, and stale responses are ignored after switching media.
- Installed `mlx-lm==0.31.3` in the existing `murmur-qwen-asr-mlx` environment and downloaded the complete 8B MLX 4-bit snapshot (~4.1 GiB) into the Hugging Face cache.

## Decisions and rationale
- Keep the POC Apple-only and MLX-first for both ASR and summarization. Do not build the RTX backend abstraction until the workflow and summary quality are validated.
- Use the same 8B model scale planned for RTX, but with platform-specific weights: `Qwen3-8B-MLX-4bit` on Apple now and the existing `Qwen3-8B-AWQ` on RTX later.
- Generate summaries manually rather than continuously so users can judge transcript completeness and output quality before adding incremental summarization.
- Limit the formatted transcript to 24,000 characters for the initial roughly-8K-context POC; long-meeting chunking or rolling summaries remain future work.

## Verification
- `python3 -m unittest tests.test_server` passed: 9 tests in 9.059s.
- `python3 -m py_compile backend/server.py`, `node --check dist/app.js`, and `git diff --check` passed.
- Real MLX smoke test produced valid structured JSON from a short Chinese transcript, correctly extracting the Friday release decision and assigning the testing task to 小王.
- Browser QA with fixture backends confirmed summary readiness, disabled/enabled button states, successful summary rendering, and enabled copy control.

## Open questions / next step
- Run a representative full transcript through the 8B model and tune the prompt/schema based on factuality and usefulness.
- Measure simultaneous MLX ASR plus 8B summary peak unified-memory usage and latency on the target Mac.
- A stopped exploratory `Qwen3-4B-MLX-4bit` download remains in the Hugging Face cache (~2.0 GiB); remove it later if disk cleanup is desired.
- After POC validation, introduce explicit ASR and summary backend abstractions for the RTX 2000 Ada deployment and use the existing 8B AWQ snapshot there.
