# Murmur realtime transcript

Murmur captures the audio from the video or audio file that is currently playing, sends
voice-activity-gated 16 kHz PCM chunks (roughly 1-8 seconds, cut at natural pauses) to a
local inference service, and appends each Qwen3-ASR result to the transcript panel. The
source media itself is never uploaded as a file. Local MP3, other browser-supported audio,
and browser-supported video files share the same flow.

## Run locally on Apple Silicon

Use the existing MLX environment. The page shows model readiness while it loads.

```bash
/Users/guanlunlu/.pyenv/versions/murmur-qwen-asr-mlx/bin/python backend/server.py
```

Then open <http://127.0.0.1:8787>. Keep the terminal open while testing.

Optional configuration:

```bash
MURMUR_ASR_MODEL=mlx-community/Qwen3-ASR-1.7B-8bit \
  /Users/guanlunlu/.pyenv/versions/murmur-qwen-asr-mlx/bin/python backend/server.py --port 8787
```

The chunk endpoint accepts mono little-endian Float32 PCM at 16 kHz. `GET
/api/health` reports model readiness and `POST /api/transcribe` runs one chunk.

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
must currently be run through the local server because the model requires Apple Metal
and roughly 4 GB of memory.
