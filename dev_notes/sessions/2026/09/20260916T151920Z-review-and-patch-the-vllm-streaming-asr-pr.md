<!-- dev-notes: {"id":"20260916T151920Z-review-and-patch-the-vllm-streaming-asr-pr","created_at":"2026-09-16T15:19:20Z","title":"Review and patch the vLLM streaming ASR PR","status":"active","tags":["asr","frontend","review","streaming","vllm"],"files":["README.md","dist/app.js","dist/index.html","dist/styles.css"],"branch":"codex/qwen3-asr-streaming","commit":"563dc76"} -->

# Review and patch the vLLM streaming ASR PR

## Outcome

Reviewed the incoming PR `codex/qwen3-asr-streaming` (4 commits by Alex Chen, adding a
Windows CUDA Transformers backend and a WSL2/vLLM stateful streaming backend) and pushed
two fix commits onto that branch.

The branch tracks `git@github.com:Alex-and-stuff/murmur.git` (Alex's fork), NOT
`origin`/`guanlunlu/murmur`. We have write access; both commits were pushed there as a
fast-forward.

- `5a8af28` — UI could not distinguish an intentionally disabled summary backend, a
  service that was not running, and a service whose model failed to load.
- `563dc76` — the player advertised a demo clip that was deleted in `b536af6`.

## Decisions and rationale

- Split `serverStatus` into `offline` (health fetch failed) vs `error` (service answered,
  model load failed), and surfaced `health.error`, which the backend already reported and
  the frontend discarded. Without this a failed model load showed "請先啟動本機 inference
  service" for a service that was demonstrably running — this cost real debugging time
  this session before we found the cause was a missing package in the collaborator's venv.
- Treated `summary.status: "disabled"` as its own frontend state. The PR added
  `--summary-backend off` and its own README tells Windows users to pass it, so following
  the PR's instructions produced a UI that looked broken.
- Truncated backend error detail to 140 chars in the footer (real payloads are full
  tracebacks and would break the layout) and logged the full text to the console.
- Fixed the dangling `dist/assets/kelly_shorts.mp3` reference in the same push even though
  it predates the PR: it makes the play button silently do nothing, which masks exactly
  the backend failures the other commit makes visible. Kept it as a separate commit so it
  can be split out if the PR author objects.
- Did NOT touch `VllmStreamingBackend`. No NVIDIA hardware here, so the code cannot even
  be imported; changing unverifiable code and handing it to the only person with the
  hardware would be worse than reporting it.
- Left `.python-version` (`murmur-qwen-asr` -> `murmur-qwen-asr-mlx`) uncommitted; it is
  unrelated local pyenv state.

## Verification

- `python3 -m unittest discover -s tests -t .` -> `Ran 33 tests` / `OK` (pytest is NOT
  installed in this venv; use unittest).
- `node --check dist/app.js` -> clean.
- Reproduced both target states against a real server and drove the REAL `checkHealth()`
  source (extracted from `dist/app.js`, `new Function`, Proxy-stubbed `el`) against the
  live `/api/health` payloads:
  - `--backend fixture-streaming --summary-backend off` -> `summary.status: "disabled"`,
    footer renders 摘要功能已停用.
  - `--backend mlx --model does-not-exist/nope` -> `status: "error"` with a
    `RepositoryNotFoundError` detail; footer renders `Inference service 錯誤: ...`.
  - unreachable port -> `offline`, footer renders 未連接本機 inference service.
- NOT verified in a real browser by anyone yet — no browser available in this environment.

## Open questions / next step

Next step is to pull this branch on an Ubuntu box WITH an NVIDIA GPU (vLLM is CUDA-only;
plain Linux is not enough — check `nvidia-smi` first) and:

1. exercise `--backend transformers` and `--backend vllm` for real (neither has been run
   by anyone but the PR author);
2. confirm the two pushed commits in an actual browser — this is the only part no one has
   seen rendered;
3. reproduce the two unfixed findings reported to the author, and consider fixing them
   there since that machine has the hardware:
   - `VllmStreamingBackend._sessions` has no idle timeout; a browser tab that closes
     without calling `finish`/`abort` leaks a vLLM session and its KV cache, against a
     pre-reserved 75% of VRAM;
   - `push_stream` holds `self._lock` across the whole GPU inference, fully serializing
     concurrent streams.

Also unresolved: MLX still has no streaming path (`MLXQwenBackend` has no `streaming`
attribute, health reports `streaming: false`, frontend falls back to chunked). Asked the
author whether that is deliberate. Drafted PR comment covering all of the above is at
`/tmp/murmur-pr-comment.md` (not yet posted; `gh` is not installed on this machine).
