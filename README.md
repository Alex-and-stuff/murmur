# Murmur realtime transcript

Murmur captures the audio from the video or audio file that is currently playing, sends
voice-activity-gated 16 kHz PCM chunks (roughly 1-16 seconds, cut at natural pauses) to a
local inference service, and appends each Qwen3-ASR result to the transcript panel. The
source media itself is never uploaded as a file. Local MP3, other browser-supported audio,
and browser-supported video files share the same flow.

Transcript segments then feed a **bounded meeting state** instead of an ever-growing
rolling summary. Raw ASR is stored verbatim and queued; once the queue crosses a token or
time threshold, one rollout sends a fixed-size prompt to the local 8B model:

```
instructions + one-line index of earlier topics + the full current topic + new transcript
```

The model answers with a structured state update classified as `continue`, `new_section`
or `return_to_section`. Finished topics are archived in full and represented in later
prompts by a single index line, so a three-hour meeting costs the same context as a
three-minute one. Returning to an earlier topic reloads that topic from the archive — it
is never reconstructed from its index descriptor. Nothing is silently truncated: segments
that do not fit stay queued, compaction is explicit and reported, and a prompt that still
does not fit raises instead of being cut. A failed or malformed model response leaves both
the committed state and the pending queue untouched.

State, the section archive and every raw ASR segment live in a SQLite file, so a restart
resumes from the last committed version. At the end of a meeting the pending queue is
flushed and a provider-neutral finalizer produces the actual minutes; today it runs on the
local model, and a larger API model can be dropped in behind the same interface.

## Backend layout

The backend is split by concern instead of living in one file:

| Module | Responsibility |
| --- | --- |
| `backend/config.py` | Paths, sample rate, request-size limits, default model names |
| `backend/asr.py` | `ASRBackend` protocol, MLX and fixture ASR backends, load state |
| `backend/llm.py` | Chat runtimes (MLX, scripted) and background load state |
| `backend/meeting/` | Bounded meeting memory: models, budget, context builder, engine, store, finalizer |
| `backend/media.py` | YouTube allow-list and `yt-dlp` audio fetcher |
| `backend/http_app.py` | HTTP handler, routes, `create_server` |
| `backend/server.py` | CLI entry point only |

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

The chunk endpoint accepts mono little-endian Float32 PCM at 16 kHz.

| Endpoint | Purpose |
| --- | --- |
| `GET /api/health` | ASR and summary model state plus the configured context budget |
| `POST /api/transcribe` | One audio chunk in, one transcript segment out |
| `POST /api/meetings` | Start a meeting, returns its id |
| `POST /api/meetings/{id}/segments` | Append ASR segments; they are stored and queued |
| `POST /api/meetings/{id}/rollout` | Force one rollout now |
| `POST /api/meetings/{id}/auto` | Pause or resume automatic rollouts (segments keep queueing) |
| `POST /api/meetings/{id}/finalize` | Flush the queue, then consolidate the minutes |
| `GET /api/meetings/{id}/state` | Current topic, index, pending queue and rollout metrics |
| `GET /api/meetings/{id}/transcript` | Every raw ASR segment, verbatim |

Rollout metrics feed the inference debug panel: per-block token counts, the operation the
model chose, any compaction that was applied, latency and retries.

Budgets and rollout policy are configuration, not constants in the code. The defaults keep
each rollout inside a 3072-token application budget (2560 input, 512 reserved for output)
and can be overridden per run:

```bash
MURMUR_MAX_CONTEXT_TOKENS=3072 MURMUR_ROLLOUT_TRIGGER_TOKENS=500 \
MURMUR_ROLLOUT_MAX_INTERVAL_SECONDS=60 MURMUR_MEETING_DB=data/meetings.sqlite3 \
  /Users/guanlunlu/.pyenv/versions/murmur-qwen-asr-mlx/bin/python backend/server.py
```

The first normal startup downloads the configured MLX summary weights into the Hugging
Face cache.

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
python -m unittest discover -s tests -t .
```

The deployed static prototype remains useful as a UI preview, but realtime inference
must currently be run through the local server because the POC models require Apple
Metal. This stage intentionally targets MLX; an RTX backend abstraction is deferred until
the summary workflow and output quality are validated.

## Run ASR only on Windows with NVIDIA CUDA

The Windows backend uses the official `qwen-asr` Transformers runtime. It deliberately
disables meeting summaries, so no Apple-only MLX model is loaded. An NVIDIA GPU with CUDA
support is required.

Create a clean virtual environment, then install CUDA-enabled PyTorch before the ASR
package. This example uses CUDA 12.6 wheels:

```powershell
python -m venv .venv-qwen-asr
.\.venv-qwen-asr\Scripts\python.exe -m pip install --upgrade pip
.\.venv-qwen-asr\Scripts\python.exe -m pip install torch==2.7.1+cu126 --index-url https://download.pytorch.org/whl/cu126
.\.venv-qwen-asr\Scripts\python.exe -m pip install qwen-asr numpy
```

Start with the smaller Qwen model, which downloads automatically on its first run:

```powershell
.\.venv-qwen-asr\Scripts\python.exe backend\server.py --backend transformers --model Qwen/Qwen3-ASR-0.6B --summary-backend off
```

Then open <http://127.0.0.1:8787>. The service health endpoint should show
`Qwen/Qwen3-ASR-0.6B · Transformers CUDA`. On the tested RTX 3060 Ti (8 GB), this model
used about 3.2 GB VRAM and transcribed five seconds of Chinese audio in 2.31 seconds.

## Run stateful streaming ASR through WSL2

Qwen's official streaming mode uses vLLM, which runs on Linux rather than native Windows.
On an NVIDIA Windows machine, use WSL2 and keep the Windows Transformers service stopped so
vLLM has exclusive access to the GPU.

```bash
# Run inside Ubuntu (WSL), once.
python3 -m venv ~/.venvs/murmur-qwen-vllm
~/.venvs/murmur-qwen-vllm/bin/pip install --upgrade pip
~/.venvs/murmur-qwen-vllm/bin/pip install 'qwen-asr[vllm]' yt-dlp
```

Start Murmur from the WSL copy of the repository:

```bash
cd /mnt/<drive>/path/to/murmur   # the WSL view of your Windows checkout
~/.venvs/murmur-qwen-vllm/bin/python backend/server.py \
  --port 8788 --backend vllm --model Qwen/Qwen3-ASR-0.6B --summary-backend off \
  --vllm-gpu-memory-utilization 0.75 --vllm-max-model-len 4096
```

The browser continues to use <http://127.0.0.1:8788>. When the health response exposes
`streaming: true`, it sends new one-second PCM audio to one stateful Qwen session. The model
manages its own rolling text and token rollback; at a natural pause the browser calls `finish`
and commits the final segment.
