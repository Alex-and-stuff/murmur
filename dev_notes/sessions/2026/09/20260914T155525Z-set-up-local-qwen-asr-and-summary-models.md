<!-- dev-notes: {"id":"20260914T155525Z-set-up-local-qwen-asr-and-summary-models","created_at":"2026-09-14T15:55:25Z","title":"Set up local Qwen ASR and summary models","status":"completed","tags":["asr","gpu","models","setup"],"files":[".agents/skills/dev-notes/SKILL.md",".gitignore",".python-version","models/Qwen3-8B-AWQ","models/Qwen3-ASR-1.7B"],"branch":"main","commit":"e457164"} -->

# Set up local Qwen ASR and summary models

## Outcome
- Created pyenv virtualenv `murmur-qwen-asr` with Python 3.12.11 and bound it through `.python-version`.
- Installed `qwen-asr==0.0.6` and its runtime dependencies.
- Downloaded and verified `Qwen/Qwen3-ASR-1.7B` (4.4 GiB).
- Downloaded and verified official `Qwen/Qwen3-8B-AWQ` (AWQ 4-bit, 5.7 GiB).
- Added project-local symlinks under `models/`; `models/` is ignored by Git.
- Installed the project-local `dev-notes` skill from the datacenter repository.

## Decisions and rationale
- Use Qwen3-8B-AWQ rather than BF16 so ASR and summarization have a reasonable chance to coexist on an RTX 2000 Ada 16 GB.
- Keep Hugging Face weights in the global cache and expose them through project symlinks to avoid duplicate storage.
- Initial deployment should omit the forced aligner, limit summary context to about 8K, use batch size 1, disable thinking, and verify peak VRAM on the target GPU.

## Verification
- Both model snapshots contain both safetensors shards and no `.incomplete` files.
- `qwen_asr` and PyTorch import successfully in the project virtualenv.
- Both project model symlinks resolve and expose `config.json` plus their weight shards.
- Removed five interrupted BF16 weight fragments, reclaiming about 4.4 GiB.

## Open questions / next step
- Obtain a representative meeting video, extract 16 kHz mono audio, and benchmark transcription quality and latency.
- Measure simultaneous ASR plus summary peak VRAM on the RTX 2000 Ada 16 GB.
