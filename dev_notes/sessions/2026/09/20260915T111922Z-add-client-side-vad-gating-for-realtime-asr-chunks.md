<!-- dev-notes: {"id":"20260915T111922Z-add-client-side-vad-gating-for-realtime-asr-chunks","created_at":"2026-09-15T11:19:22Z","title":"Add client-side VAD gating for realtime ASR chunks","status":"completed","tags":["asr","frontend","realtime","vad"],"files":["dist/app.js"],"branch":"main","commit":"6b56efd"} -->

# Add client-side VAD gating for realtime ASR chunks

## Outcome
- Replaced the fixed 5-second chunking trigger in `RealtimeCapture` with energy-based (RMS) voice activity detection.
- Silent audio before any speech is detected is never buffered; only a small pre-roll (last 2 processor frames, ~170ms) is kept so speech onsets aren't clipped.
- A chunk is flushed to `/api/transcribe` when either: speech has been followed by ~0.6s of silence (natural end-of-utterance), or the buffered speech reaches an 8s safety cap (keeps latency bounded during long uninterrupted speech).
- Bursts shorter than `MIN_CHUNK_SECONDS` (1s) after the silence hangover are still discarded rather than sent, same as before.

## Decisions and rationale
- Used a simple fixed RMS threshold (`SPEECH_RMS_THRESHOLD = 0.012`) instead of a WebRTC VAD / WASM library to avoid new dependencies and keep everything in plain `dist/app.js`. This is a heuristic tuned by ear on the bundled demo audio, not calibrated against a noise floor — may need adjustment for noisier sources.
- Kept a small pre-roll ring buffer instead of just starting capture exactly at the frame where speech crosses the threshold, since the ScriptProcessor frame size (~85-100ms depending on native sample rate) is coarse enough to clip the first phoneme otherwise.
- Removed the `flush(force)` parameter entirely since flush is now only called when VAD/caller logic has already decided a flush is appropriate (silence hangover, max-chunk cap, or pause) — the old "only flush automatically once CHUNK_SECONDS reached" gate no longer applies.

## Verification
- `node --check dist/app.js` passed.
- `git diff --check` passed.
- `python -m unittest tests.test_server` passed (3 tests, backend untouched — VAD is purely client-side).
- Not yet verified against a live mic/video session in the browser; logic was traced by hand for the speech-start, hangover-flush, max-chunk-flush, and pause-flush paths.

## Open questions / next step
- `SPEECH_RMS_THRESHOLD` is a fixed heuristic; consider an adaptive noise-floor estimate if real recordings have varying background levels.
- Very short utterances (e.g. a single short word) can fall below `MIN_CHUNK_SECONDS` once the 0.6s hangover is included and get silently dropped — may need a lower minimum or to send short-but-real bursts anyway.
- Should manually test in-browser with the bundled demo audio and a real microphone/video to confirm chunk boundaries feel natural before considering this done end-to-end.
