<!-- dev-notes: {"id":"20260914T163900Z-implement-local-realtime-qwen-asr-inference","created_at":"2026-09-14T16:39:00Z","title":"Implement local realtime Qwen ASR inference","status":"completed","tags":["asr","backend","frontend","mlx","realtime"],"files":["README.md","backend/server.py","dist/app.js","dist/index.html","dist/styles.css","tests/test_server.py"],"branch":"main","commit":"6b56efd"} -->

# Implement local realtime Qwen ASR inference

## Outcome
- Replaced the deterministic 16-cue transcript simulation with browser audio capture and real chunked inference.
- Added a local HTTP service that serves the existing UI, loads `mlx-community/Qwen3-ASR-1.7B-8bit` once, exposes model health, and transcribes 16 kHz mono Float32 PCM.
- Playback now captures 5-second chunks from either the bundled audio or a user-selected video, queues inference, and appends timestamped model results.
- Added loading, ready, listening, inference, pause, error, restart, stale-request isolation, transcript copy, and seek-aware capture states.

## Decisions and rationale
- Capture PCM through Web Audio instead of uploading the full media file or requiring ffmpeg. This keeps the source video in the browser and works for both the demo audio and local video object URLs.
- Use request-per-chunk HTTP rather than WebSocket for phase one. The inference result is naturally chunk-scoped, requests are serialized client-side, and the runtime needs no new server dependency.
- Keep inference local because MLX requires Apple Metal and about 4 GB of memory; the existing static Sites deployment cannot host this model.
- Use 5-second chunks: short enough for visible realtime progress while preserving enough speech context for Qwen3-ASR.

## Verification
- `node --check dist/app.js` passed.
- `python -m py_compile backend/server.py tests/test_server.py` passed.
- `python -m unittest tests.test_server` passed (3 tests: health/static serving, PCM transcription contract, invalid chunk rejection).
- `git diff --check` passed.
- Direct MLX inference on the first 5 seconds of the bundled Chinese sample loaded in 1.40 s and inferred in 0.38 s.
- End-to-end POST of the same 5-second PCM chunk to the running service returned a coherent Qwen transcript in 0.341 s.

## Open questions / next step
- Phase two should add voice activity detection and overlap/deduplication so words at fixed 5-second boundaries are not clipped or repeated.
- Consider Traditional Chinese normalization if product output must always match the zh-Hant interface.
- Migrate from ScriptProcessorNode to AudioWorklet when broader production browser support becomes a priority.
