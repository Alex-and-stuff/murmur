# Murmur realtime transcript

Murmur captures the audio from the video or audio file that is currently playing, sends
voice-activity-gated 16 kHz PCM chunks (roughly 1-16 seconds, cut at natural pauses) to a
local inference service, and appends each Qwen3-ASR result to the transcript panel. The
source media itself is never uploaded as a file. Local MP3, other browser-supported audio,
and browser-supported video files share the same flow. Once two stable transcript segments
exist, the page automatically creates a structured summary, key points, decisions, and
action items. Each later segment is merged into the existing notes after a short debounce,
so the summary rolls forward without repeatedly sending the full meeting transcript. A
manual full refresh remains available. The prompt rewrites the result as standalone notes,
deduplicates related points, and forbids update-log phrases such as “new transcript.” The
browser also performs a full rebase after every six additional segments while the complete
transcript remains within the model's safe context budget. The UI can pause automatic
online summary updates without disabling manual refreshes, and its inference debug panel
lists every ASR audio context and summary prompt context for the current media session.
Audio-only sources use the compact player without the old sample-video visual.

## Run locally on Apple Silicon

Use the existing MLX environment. The page reports ASR and summary model readiness
independently while they load. Install the language-model runtime once:

```bash
/Users/guanlunlu/.pyenv/versions/murmur-qwen-asr-mlx/bin/pip install mlx-lm
```

```bash
/Users/guanlunlu/.pyenv/versions/murmur-qwen-asr-mlx/bin/python backend/server.py
```

Then open <http://127.0.0.1:8787>. Keep the terminal open while testing.

Optional configuration:

```bash
MURMUR_ASR_MODEL=mlx-community/Qwen3-ASR-1.7B-8bit \
MURMUR_SUMMARY_MODEL=Qwen/Qwen3-8B-MLX-4bit \
  /Users/guanlunlu/.pyenv/versions/murmur-qwen-asr-mlx/bin/python backend/server.py --port 8787
```

The chunk endpoint accepts mono little-endian Float32 PCM at 16 kHz. `GET
/api/health` reports both model states, `POST /api/transcribe` runs one audio chunk,
and `POST /api/summarize` accepts transcript segments plus an optional `previous_summary`
for incremental updates. Responses include context measurements used by the debug UI:
audio samples for ASR and prompt tokens plus input characters/segments for summaries.
The first normal startup downloads the configured MLX summary
weights into the Hugging Face cache.

## Test against a YouTube video

Paste a YouTube link into the topbar field and press 載入 instead of uploading a local
file. The backend downloads audio-only via `yt-dlp` into `dist/uploads/` (gitignored,
cached by video id) and serves it back to the page as a same-origin file, which then
flows through the exact same capture/VAD/inference pipeline as a local upload. Only
`youtube.com`/`youtu.be` links are accepted.

Requires `yt-dlp` in the same environment that runs the backend:

```bash
/Users/guanlunlu/.pyenv/versions/murmur-qwen-asr-mlx/bin/pip install yt-dlp
```

Downloading requires internet access, and is subject to YouTube's Terms of Service —
this is intended for local, personal testing of the transcription pipeline, not for
redistributing downloaded audio.

## Smoke test without loading the model

```bash
python -m unittest tests.test_server
```

The deployed static prototype remains useful as a UI preview, but realtime inference
must currently be run through the local server because the POC models require Apple
Metal. This stage intentionally targets MLX; an RTX backend abstraction is deferred until
the summary workflow and output quality are validated.
