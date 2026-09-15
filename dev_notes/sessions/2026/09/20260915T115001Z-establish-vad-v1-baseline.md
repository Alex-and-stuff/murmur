<!-- dev-notes: {"id":"20260915T115001Z-establish-vad-v1-baseline","created_at":"2026-09-15T11:50:01Z","title":"Establish VAD v1 baseline","status":"completed","tags":["asr","frontend","realtime","vad"],"files":["dist/app.js"],"branch":"main","commit":"9afc64a"} -->

# Establish VAD v1 baseline

## Outcome
- VAD v1 is now the baseline for realtime ASR chunking in `dist/app.js`.
- Client-side RMS gating buffers only detected speech with a short pre-roll, then flushes after 0.35s of silence or at the 16s safety cap.
- Current heuristics are `SPEECH_RMS_THRESHOLD = 0.012` and `MIN_CHUNK_SECONDS = 1`.
- This consolidates the earlier implementation and live-tuning notes into the committed baseline at `9afc64a`.

## Decisions and rationale
- Keep the dependency-free RMS implementation for v1; treat the constants as empirically tuned defaults rather than a final noise-robust VAD design.
- Preserve the existing trade-off: low segmentation latency from the 0.35s hangover, with fewer mid-sentence cuts from the 16s maximum chunk length.

## Verification
- `node --check dist/app.js` passed.
- `python3 -m unittest tests.test_server` passed: 7 tests in 9.558s. The sandboxed attempt could not bind a loopback port; rerunning with local port permission passed.
- `git diff --check` passed before adding this checkpoint.
- Worktree was clean before recording this note.

## Open questions / next step
- Validate against noisier microphones and varied media; consider adaptive noise-floor estimation if the fixed RMS threshold is unreliable.
- Revisit the 1s minimum if short real utterances are being dropped.
