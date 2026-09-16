<!-- dev-notes: {"id":"20260916T162431Z-confirm-the-cuda-asr-backends-on-ubuntu-nvidia","created_at":"2026-09-16T16:24:31Z","title":"Confirm the CUDA ASR backends on Ubuntu + NVIDIA","status":"active","tags":["asr","cuda","streaming","verification","vllm"],"files":["backend/asr.py"],"branch":"main","commit":"2271da3"} -->

# Confirm the CUDA ASR backends on Ubuntu + NVIDIA

## Outcome

Supersedes the open questions in
`20260916T151920Z-review-and-patch-the-vllm-streaming-asr-pr`. PR #1 is merged to `main`
as `2271da3`; the branch `codex/qwen3-asr-streaming` is fully contained in it.

All three items that note left pending for NVIDIA hardware were exercised by the
repository owner on an **Ubuntu + NVIDIA machine** (native Linux, not the WSL2 setup the
earlier note assumed):

- `--backend transformers` transcribes correctly.
- `--backend vllm` stateful streaming works end to end.
- Both UI fixes (`5a8af28` summary disabled vs. failed load, `563dc76` dangling demo clip)
  confirmed in a real browser — the part no one had seen rendered until now.

## Decisions and rationale

- Native Ubuntu is a working host for the vLLM path. The earlier README framed WSL2 as the
  route because the collaborator's machine is Windows; WSL2 is a workaround for that host,
  not a requirement of vLLM.
- The two review findings are recorded as still open rather than resolved. Their runtime
  impact was not measured in this session, but both are still present in the merged code
  and were confirmed by reading `2271da3`:
  - `VllmStreamingBackend._sessions` (`backend/asr.py:104`) has no idle timeout. A tab that
    closes without `finish`/`abort` leaks a session and its KV cache. Because vLLM
    pre-reserves 75% of VRAM, this degrades available KV cache silently instead of raising.
  - `push_stream` (`backend/asr.py:119`) holds `self._lock` across the whole GPU inference,
    so concurrent streams fully serialize and vLLM's batching cannot engage.
- Neither is load-bearing for the single-user case that was tested, which is why the
  verification above passed with both defects in place.

## Verification

Performed by the repository owner on Ubuntu + NVIDIA; exact commands and timings not
captured here. Code-level confirmation that both findings persist was done in this session
by reading `backend/asr.py` at `2271da3`.

## Open questions / next step

- Decide whether the two `VllmStreamingBackend` defects are worth fixing now. They only
  bite with multiple concurrent streams or long-lived servers.
- Apple Silicon still has no stateful streaming path. `MLXQwenBackend` exposes no
  `streaming` attribute, so `/api/health` reports `streaming: false` and the frontend falls
  back to `queueProvisional()`, which re-transcribes the whole growing buffer every few
  seconds. Investigated this session: `mlx_audio` ships two usable stateful models —
  `vibevoice_asr` (`init_streaming_state` / `streaming_generate_step`, persistent KV cache
  via `make_prompt_cache`) and `voxtral_realtime` (purpose-built realtime encoder with
  `StreamingAudioSource` / `StreamingMel`). `qwen3_asr.stream_transcribe` is NOT usable for
  this: it streams tokens out of an already-complete recording and calls `mx.clear_cache()`
  between chunks.
- Proposed shape: keep Qwen3-ASR MLX for the per-utterance final text, add a second
  streaming backend only for the live caption. Requires a `--streaming-backend` flag and
  changes the meaning of `health.streaming` in `dist/app.js`.
