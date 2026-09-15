<!-- dev-notes: {"id":"20260915T113725Z-add-youtube-link-testing-via-server-side-yt-dlp-download","created_at":"2026-09-15T11:37:25Z","title":"Add YouTube link testing via server-side yt-dlp download","status":"completed","tags":["asr","backend","frontend","testing","youtube"],"files":[".gitignore","README.md","backend/server.py","dist/app.js","dist/index.html","dist/styles.css","tests/test_server.py"],"branch":"main","commit":"6b56efd"} -->

# Add YouTube link testing via server-side yt-dlp download

## Outcome
- Added a "貼上 YouTube 連結" input in the topbar so a YouTube video can be tested through the exact same VAD + Qwen3-ASR pipeline as a local file upload, without needing to download the video manually first.
- New backend endpoint `POST /api/fetch-media` (JSON `{"url": ...}`) validates the host against an allowlist (`youtube.com`, `www.youtube.com`, `m.youtube.com`, `music.youtube.com`, `youtu.be`), then uses `yt-dlp` (`YtDlpFetcher` in backend/server.py) to download audio-only (`bestaudio[ext=m4a]/bestaudio/best`) into `dist/uploads/<video_id>.<ext>`, cached by video id so repeat requests are instant.
- The static handler now serves `dist/uploads/` automatically (it's just a subdirectory of the existing static root); added explicit `.m4a`/`.webm`/`.opus`/`.mp3` entries to `MurmurHandler.extensions_map` so the browser gets a correct audio `Content-Type`.
- Frontend: refactored `loadMedia(file)`'s tail (src assignment, badge/title/meta text, resetTranscript, updatePlaybackState, showToast) into a shared `activateMediaSource(url, {...})` helper, and added `loadYoutubeMedia(url)` which POSTs to `/api/fetch-media` and feeds the returned same-origin URL into that same helper — the capture/VAD/transcribe code path is completely unchanged.

## Decisions and rationale
- Chose server-side download-then-serve-as-local-file over embedding a YouTube `<iframe>` player: `RealtimeCapture.attach()` uses `createMediaElementSource`, which cannot capture audio from a cross-origin iframe. Downloading audio and serving it same-origin from `dist/uploads/` was the only way to reuse the existing Web Audio capture pipeline unchanged.
- Extract audio-only rather than video: ASR only needs audio, this keeps downloads small/fast and lets the YouTube flow reuse the existing audio-element + visualization path with no video-codec handling.
- Strict host allowlist on `/api/fetch-media` (not a generic URL fetcher) to avoid turning the local dev server into an open SSRF-style proxy.
- `yt-dlp` is imported lazily inside `YtDlpFetcher.fetch`, matching the existing lazy-import pattern for `mlx_audio` in `MLXQwenBackend` — so the server and its test suite still run fine without `yt-dlp` installed as long as the feature isn't exercised.
- `MediaFetcher` is a `Protocol` injected via `create_server(..., media_fetcher=...)`, mirroring the existing `ASRBackend`/`FixtureBackend` dependency-injection pattern, so unit tests stub the downloader instead of hitting the real network.
- Drive-by fix: README described chunking as "5-second" fixed, which was already stale after the earlier VAD change in this session; updated it to describe the VAD-gated variable-length chunking.

## Verification
- `node --check dist/app.js` passed.
- `python -m py_compile backend/server.py tests/test_server.py` passed.
- `git diff --check` passed.
- `python -m unittest tests.test_server` passed: 7 tests (3 pre-existing + 4 new `FetchMediaTest` cases covering non-YouTube rejection, malformed JSON body, a stubbed success path, and a stubbed fetch-error path) — none of these hit the real network.
- Installed `yt-dlp` into the `murmur-qwen-asr-mlx` venv (`pip install yt-dlp`, resolved to 2026.8.19).
- Real end-to-end smoke test against the running local server: restarted `backend/server.py` (Python code changes need a restart, unlike the static frontend files) and POSTed `https://www.youtube.com/watch?v=jNQXAC9IVRw` ("Me at the zoo", the first YouTube video, ~19s, public/well-known) to `/api/fetch-media` — got back `{"url": "/uploads/jNQXAC9IVRw.m4a", "title": "Me at the zoo"}`. Confirmed the file is served with `Content-Type: audio/mp4` and is a valid ISO-media/MPEG-4 audio file (`file` command). Confirmed a repeat request for the same URL hits the on-disk cache (no re-download) and returns in ~1.3s. Confirmed a non-YouTube URL (`https://example.com/video.mp4`) is rejected with 400 `unsupported_url`.
- Did NOT verify the browser-side `<input>`/form UI interactively (no headless browser tooling — no `chromium-cli`, no `playwright` installed in this environment) — only traced the JS by hand. The user should click through the new topbar field once to confirm the UI/UX feels right.
- Skipped the optional bonus check (piping downloaded audio through `/api/transcribe` for a real transcript) — no `ffmpeg` or `soundfile` available in the venv to resample the m4a to 16kHz mono float32 PCM, and installing `ffmpeg` felt like a bigger, unrequested system-level dependency to add unprompted. The download → serve → same pipeline wiring is verified; only the "does Qwen3-ASR produce a sensible transcript for this particular audio" step is unverified.

## Open questions / next step
- Legal/ToS note (surfacing plainly, not editorializing): downloading YouTube audio via `yt-dlp` is against YouTube's Terms of Service in general; this is wired up for local, personal testing of the transcription pipeline only, not for redistribution. Worth the user's own judgment call on which videos they test against.
- No cleanup/eviction policy for `dist/uploads/` — it will grow unbounded with every distinct video tested. Fine for a local dev/test tool, but worth a manual `rm -rf dist/uploads/` occasionally or a follow-up if this becomes a real feature.
- The cache-hit path still calls `yt_dlp.YoutubeDL(...).extract_info(url, download=False)` (a network probe) even when the file already exists on disk, to get the id/title — could short-circuit further by caching title metadata alongside the file, but current latency (~1.3s) seemed acceptable for a test tool.
- Should manually try the browser UI end-to-end (paste a link, watch it download and start playing) since only the backend endpoint and the JS logic were verified, not the actual click-through experience.
