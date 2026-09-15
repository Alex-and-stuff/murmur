<!-- dev-notes: {"id":"20260915T122404Z-serialize-mlx-asr-and-summary-startup","created_at":"2026-09-15T12:24:04Z","title":"Serialize MLX ASR and summary startup","status":"active","tags":["asr","bugfix","mlx","startup","summary"],"files":["backend/server.py"],"branch":"main","commit":"9afc64a"} -->

# Serialize MLX ASR and summary startup

## Outcome
- Diagnosed a startup-only failure where the HTTP service was listening and the 8B MLX summary model reached ready, but ASR entered error with `ImportError: cannot import name 'AutoTokenizer' from 'transformers'`.
- Changed production startup from two concurrent model-loader threads to one background loader that initializes ASR first and Summary second.
- The HTTP server still starts immediately; only the heavyweight model initialization is serialized.

## Decisions and rationale
- The same environment successfully imported both `transformers.AutoTokenizer` and `mlx_audio.stt.utils` in fresh processes, so dependency downgrade or reinstall was not warranted.
- Both MLX runtimes lazily import Transformers. Serializing first initialization avoids exposing a partially initialized lazy module and also avoids simultaneous model-load memory peaks.

## Verification
- The failing live `/api/health` response showed ASR error and Summary ready, confirming the service itself was reachable.
- Fresh-process `AutoTokenizer` and `mlx_audio` imports passed in the same virtualenv.
- `python3 -m py_compile backend/server.py` and `git diff --check` passed.
- `python3 -m unittest tests.test_server` passed: 9 tests in 10.560s.

## Open questions / next step
- Restart the currently running server so it loads the serialized-startup code, then verify `/api/health` reaches ready for both models on a real dual-model launch.
