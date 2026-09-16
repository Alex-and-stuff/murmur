<!-- dev-notes: {"id":"20260916T141204Z-capture-asr-audio-from-live-input-devices","created_at":"2026-09-16T14:12:04Z","title":"Capture ASR audio from live input devices","status":"active","tags":["audio-device","capture","frontend","vad"],"files":["README.md","dist/app.js","dist/index.html","dist/styles.css"],"branch":"main","commit":"82c7ec4"} -->

# Capture ASR audio from live input devices

## Outcome
Added a 收音來源 selector with four capture modes — 播放檔案 (existing), 麥克風,
系統音／會議音, 麥克風＋系統音 — all feeding the existing VAD → `/api/transcribe` →
meeting-state pipeline. Frontend only; no backend or API change was needed because
`/api/transcribe` already takes raw PCM plus start/end headers.

- `RealtimeCapture` gained `attachStreams()` / `detachStreams()`: stream sources are
  summed into a `GainNode` mixer before the ScriptProcessor, so mic + system audio mix in
  one path and downmix to mono in the existing `ingest()` loop.
- `receive()` became `ingest()`, source-agnostic. The three media-element assumptions were
  replaced by helpers: `captureClock()`, `isCapturing()`, `speechThreshold()`.
- Live sessions time-stamp from `AudioContext.currentTime` with a `liveOffset` accumulator,
  so switching input device mid-session keeps one continuous transcript clock.
- UI: device `<select>` from `enumerateDevices`, an input level meter, per-mode hint text,
  `LIVE` duration, disabled timeline/mute, play button doubles as start/stop收音.

## Decisions and rationale
- Capture stays in the browser rather than moving to `sounddevice` in the backend (the
  package is installed in the MLX env and was the alternative considered). The browser path
  reuses the whole existing VAD/chunking/POST path; a backend path would have meant a
  second VAD implementation in Python.
- System audio goes through `getDisplayMedia` with `video: true`, video track dropped
  immediately: Chrome only offers tab/system audio from that dialog. A BlackHole-style
  virtual device remains available via the 麥克風 device list as a second route.
- Live input is never connected to `destination`. The media path keeps its monitoring
  connection; a mic monitored through the speakers would feed back.
- `echoCancellation`/`noiseSuppression`/`autoGainControl` are all off: AGC distorts the RMS
  gate, and echo cancellation would remove the far end from a mixed meeting capture.
- The absolute RMS gate (0.012) became a per-mode base (mic .012 / system .006 / mixed
  .008) plus a slowly tracked noise floor, gating at `max(base, floor * 2.5)`. System audio
  swings far more than a close mic, so a single absolute threshold was not viable.
- Mode switches and live starts reuse an empty meeting instead of creating a new one;
  `resetTranscript()` only runs when the current meeting already holds segments.

## Verification
- `node --check dist/app.js` passes.
- `python3 -m unittest discover -s tests -t .` — 31 tests, OK (backend untouched; this
  shows no regression, not that the new path works).
- Audited every remaining `activeMedia.` reference: all are either inside a
  `captureMode === "media"` branch or behind an early return.
- NOT yet done: browser QA. Microphone and screen-share permission prompts need a human,
  so no live capture has actually been run end to end.

## Open questions / next step
- Run browser QA for all three live modes, including a real BlackHole route, and check
  whether the noise-floor margin of 2.5 clips soft speech in 系統音 mode.
- ScriptProcessor is deprecated; an AudioWorklet would be the natural follow-up, but it was
  kept to match the existing capture code.
- `X-Language: Chinese` is still hardcoded in `transcribeChunk`.
