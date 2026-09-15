<!-- dev-notes: {"id":"20260915T153833Z-replace-the-rolling-summary-with-bounded-meeting-state","created_at":"2026-09-15T15:38:33Z","title":"Replace the rolling summary with bounded meeting state","status":"active","tags":["api","context-budget","llm","meeting-state","sqlite","tests"],"files":["backend/http_app.py","backend/llm.py","backend/meeting/config.py","backend/meeting/context.py","backend/meeting/engine.py","backend/meeting/finalizer.py","backend/meeting/models.py","backend/meeting/prompts.py","backend/meeting/scheduler.py","backend/meeting/service.py","backend/meeting/store.py","backend/meeting/tokens.py","backend/meeting/updater.py","dev_notes/plan/CONTEXT_IMPLEMENTATION_PLAN.md","dist/app.js","tests/test_meeting_context.py","tests/test_meeting_engine.py","tests/test_meeting_longrun.py"],"branch":"feat/online-summary","commit":"5cc8175"} -->

# Replace the rolling summary with bounded meeting state

## Outcome
- Implemented Phases 1-4 of `dev_notes/plan/CONTEXT_IMPLEMENTATION_PLAN.md` plus the Phase 5 finalizer interface. The unbounded `S(t) = LLM(S(t-1) + ASR(t))` rolling summary is gone; each rollout now runs against a fixed-size working state.
- New `backend/meeting/` package:
  - `models.py`: `MeetingState`, `MeetingSection`, `SectionIndexEntry`, `ASRSegment`, `StateUpdate`, `TopicOperation` as stdlib dataclasses.
  - `config.py`: `MeetingSummaryConfig` and `ContextBudgetExceeded`; every budget is env-overridable.
  - `tokens.py`: `TokenizerTokenCounter` over the real model tokenizer, with a deliberately over-estimating `HeuristicTokenCounter` fallback.
  - `context.py`: `ContextBuilder` plus explicit compaction.
  - `prompts.py`, `updater.py` (JSON extraction and validation).
  - `engine.py`: rollout policy, topic operations, retries, oversized-segment splitting, flush.
  - `store.py`: SQLite persistence over `meetings`, `sections`, `section_revisions`, `segments`, `rollouts`.
  - `scheduler.py` (background rollout ticker), `service.py` (API surface), `finalizer.py` (provider-neutral `StructuralFinalizer` and `LLMFinalizer` with structural fallback).
- `backend/summary.py` was replaced by `backend/llm.py` (`ChatLLM` protocol, `MLXChatLLM`, `ScriptedChatLLM`, `LLMState`).
- `/api/summarize` was removed in favour of `/api/meetings` endpoints: create, segments, rollout, auto, finalize, state, transcript.
- `dist/app.js` now posts each ASR segment to the server and renders server-held state. VAD/ASR capture code was not touched, per the plan's do-not-modify scope.
- Phase 6 is deferred: a real hierarchical index beyond simple grouping, ordered multi-operation output, embedding retrieval, editing UI, and an eval dashboard.

## Decisions and rationale
- Meeting state moved out of the browser and onto the server with SQLite persistence. That is what makes restart recovery, atomic commits, and version-guarded concurrency possible; a browser-held state could offer none of them.
- `return_to_section` is a two-step rollout rather than one: a switch commit archives the current topic and reloads the target from the archive while consuming no segments, then a re-run executes with the real archived content in context. This guarantees a topic is never rebuilt from its short index descriptor.
- Budgets were rebalanced after measuring rather than guessed: instructions 450 / index 300 / current section 600 / pending ASR 1000 / safety 210 = 2560 input, plus a 512 output reserve, for a 3072-token application budget.
- Pydantic was not used despite the plan's suggestion: it is not installed in the `murmur-qwen-asr-mlx` environment, so the data model is stdlib dataclasses.

## Verification
- `python -m unittest discover -s tests -t .`: 31 tests pass.
- Includes a simulated ~3-hour meeting (360 segments with many topic switches) asserting that every rollout stays within budget, that input size does not grow with meeting duration, that the pending queue drains, and that all 360 raw segments map to exactly one section each.
- One real rollout was run against `Qwen/Qwen3-8B-MLX-4bit` in the MLX env: valid JSON output, a sensible `new_section` followed by an archive. The real tokenizer counted the instruction block at 289 tokens against the heuristic's 421, so the 450 budget has headroom.

## Open questions / next step
- The Qwen3.5 122B-A10B adapter behind the finalizer interface is not implemented yet; only the interface and the structural fallback exist.
- Return-to-section accuracy has not been measured against annotated transcripts.
