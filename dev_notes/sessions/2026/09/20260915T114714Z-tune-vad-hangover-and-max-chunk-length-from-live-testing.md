<!-- dev-notes: {"id":"20260915T114714Z-tune-vad-hangover-and-max-chunk-length-from-live-testing","created_at":"2026-09-15T11:47:14Z","title":"Tune VAD hangover and max chunk length from live testing","status":"completed","tags":["asr","frontend","realtime","vad"],"files":["dist/app.js"],"branch":"main","commit":"6b56efd"} -->

# Tune VAD hangover and max chunk length from live testing

## Outcome
- Lowered `SILENCE_HANGOVER_SECONDS` from 0.6 to 0.35 after live-testing in the browser: the user found 0.6s felt sluggish before a segment was sent.
- Raised `MAX_CHUNK_SECONDS` from 8 to 16 after the user found 8s cut off natural continuous speech too aggressively.

## Decisions and rationale
- Both values are heuristics tuned by the user's own ear against live playback (demo audio + browser), not derived analytically. Faster hangover trades a small risk of splitting a sentence on a mid-sentence breath/pause for lower perceived latency; the user accepted that trade-off.
- Raising the max-chunk cap accepts more end-to-end latency on long uninterrupted speech in exchange for fewer awkward mid-sentence cuts.

## Verification
- `node --check dist/app.js` passed after each change.
- Pure frontend constant changes; no backend/test impact. Not re-run through `unittest tests.test_server` since that suite doesn't touch these constants.

## Open questions / next step
- These are still guesses tuned against one demo clip; revisit if real recordings (mic input, other YouTube videos) show the hangover/max-chunk values feel wrong again.
