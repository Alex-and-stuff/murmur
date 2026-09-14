<!-- dev-notes: {"id":"20260914T160609Z-benchmark-qwen3-asr-with-mlx-on-apple-silicon","created_at":"2026-09-14T16:06:09Z","title":"Benchmark Qwen3-ASR with MLX on Apple Silicon","status":"completed","tags":["asr","benchmark","mac","mlx"],"files":["data/chinese/kelly_shorts.mp3","data/chinese/kelly_shorts.qwen3-asr-1.7b-mlx.txt"],"branch":"main","commit":"e457164"} -->

# Benchmark Qwen3-ASR with MLX on Apple Silicon

## Outcome
- Transcribed `data/chinese/kelly_shorts.mp3` with `mlx-community/Qwen3-ASR-1.7B-8bit` through `mlx-audio==0.5.4` on Apple Silicon Metal.
- Saved the plain-text transcript to `data/chinese/kelly_shorts.qwen3-asr-1.7b-mlx.txt`.
- Created a separate pyenv virtualenv, `murmur-qwen-asr-mlx`, because `mlx-audio` requires Transformers 5.x while `qwen-asr==0.0.6` pins Transformers 4.57.6.
- Restored the original `murmur-qwen-asr` environment after the initial dependency collision; both environments now pass `pip check` and the original `qwen_asr` import succeeds.

## Decisions and rationale
- Use the MLX Community 1.7B 8-bit checkpoint to preserve 1.7B recognition quality while reducing unified-memory use on the Mac.
- Keep the PyTorch and MLX runtimes isolated instead of forcing incompatible Transformers versions into one environment.
- Force `Chinese` for this benchmark and omit the forced aligner; the MLX ASR output still contains coarse chunk boundaries internally, but the saved artifact is plain text.

## Verification
- Source audio: 92.544 seconds, MP3, 48 kHz stereo.
- First MLX inference: 9.56 seconds, 4.00 GB peak memory, 423 generated tokens at 44.237 tok/s.
- Independent-environment rerun: 17.02 seconds, 4.05 GB peak memory, 423 generated tokens at 24.861 tok/s.
- Observed inference range: about 5.4x to 9.7x faster than realtime, excluding model load/download.
- Output language: Chinese. Transcript is coherent and preserves product names such as Apple, iPhone Duo, iOS, Apple Pencil, Pro, and Pro Max. No reference transcript was available, so CER/WER was not calculated.

## Reproduction
`/Users/guanlunlu/.pyenv/versions/murmur-qwen-asr-mlx/bin/python -m mlx_audio.stt.generate --model mlx-community/Qwen3-ASR-1.7B-8bit --audio data/chinese/kelly_shorts.mp3 --output-path data/chinese/kelly_shorts.qwen3-asr-1.7b-mlx --format txt --language Chinese --verbose`
