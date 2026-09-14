<!-- dev-notes: {"id":"20260914T162051Z-build-video-transcript-playback-prototype","created_at":"2026-09-14T16:20:51Z","title":"Build video transcript playback prototype","status":"completed","tags":["frontend","prototype","transcript","video"],"files":["dist/app.js","dist/assets/kelly_shorts.mp3","dist/index.html","dist/styles.css"],"branch":"main","commit":"e457164"} -->

# Build video transcript playback prototype

## Outcome
- Reworked the static Murmur UI into a video transcription test lab.
- Added a bundled 92-second Chinese demo recording and 16 timed transcript cues.
- Playback now reveals an in-progress sentence character by character, commits completed lines, and keeps transcript progress synchronized with seeking.
- Added local video selection for layout testing, play/pause, scrubbing, mute, restart, timestamp navigation, transcript copy, responsive styling, and explicit local-only prototype messaging.

## Decisions and rationale
- Use the existing Qwen3-ASR benchmark audio as the built-in fixture so the prototype opens ready to test without an upload or backend.
- Keep transcription deterministic and precomputed for this UI prototype; selected local videos use the fixture transcript and are never uploaded.
- Separate completed transcript lines from the active recognition draft so users can distinguish stable output from interim ASR output.

## Verification
- `node --check dist/app.js` passed.
- `git diff --check` passed.
- Local server returned HTTP 200 for both `/` and `/assets/kelly_shorts.mp3`.
- Browser QA confirmed desktop rendering, playback, live interim text, completed transcript lines, pause/resume, progress display, and synchronized seek behavior.

## Open questions / next step
- Connect local video audio to the Qwen3-ASR runtime and replace fixture cues with timestamped model output.
- Decide whether production should stream short chunks during playback or transcribe the entire file ahead of playback.
