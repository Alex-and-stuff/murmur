"""Rollout policy and state transitions for one process' worth of meetings.

Segments accumulate in the pending queue, a rollout batches them into a bounded
prompt, and only a committed transaction marks them consumed. At most one
rollout per meeting is in flight, and the commit is guarded by the state version.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field

from backend.llm import ChatLLM
from backend.meeting.config import ContextBudgetExceeded, MeetingSummaryConfig
from backend.meeting.context import ContextBuilder
from backend.meeting.models import (
    ASRSegment,
    MeetingSection,
    MeetingState,
    StateUpdate,
    TopicOperation,
)
from backend.meeting.store import ConcurrentStateUpdate, MeetingStore, RolloutCommit
from backend.meeting.tokens import HeuristicTokenCounter, TokenCounter, TokenizerTokenCounter
from backend.meeting.updater import InvalidStateUpdate, parse_state_update

SENTENCE_BREAK = re.compile(r"(?<=[。！？?!；;\n])")


@dataclass(slots=True)
class RolloutResult:
    status: str  # "applied" | "skipped" | "failed"
    reason: str = ""
    metrics: dict[str, object] = field(default_factory=dict)


class MeetingEngine:
    def __init__(
        self,
        store: MeetingStore,
        config: MeetingSummaryConfig | None = None,
        llm: ChatLLM | None = None,
        counter: TokenCounter | None = None,
    ):
        self.store = store
        self.config = config or MeetingSummaryConfig()
        self.config.validate()
        self._llm = llm
        self._counter = counter or HeuristicTokenCounter()
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    # -- wiring -----------------------------------------------------------

    def attach_llm(self, llm: ChatLLM) -> None:
        """Called once the model finishes loading; counts then match inference."""

        self._llm = llm
        tokenizer = getattr(llm, "tokenizer", None)
        if tokenizer is not None:
            self._counter = TokenizerTokenCounter(tokenizer, fallback=HeuristicTokenCounter())

    @property
    def llm(self) -> ChatLLM | None:
        return self._llm

    @property
    def counter(self) -> TokenCounter:
        return self._counter

    def _lock_for(self, meeting_id: str) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(meeting_id, threading.Lock())

    # -- ingestion --------------------------------------------------------

    def create_meeting(self, meeting_id: str) -> MeetingState:
        self.store.create_meeting(meeting_id)
        return self.store.load_state(meeting_id)

    def add_segments(self, meeting_id: str, raw_segments: list[dict[str, object]]) -> list[ASRSegment]:
        prepared: list[dict[str, object]] = []
        for raw in raw_segments:
            prepared.extend(self._split_oversized(raw))
        return self.store.append_segments(meeting_id, prepared)

    def _split_oversized(self, raw: dict[str, object]) -> list[dict[str, object]]:
        """One ASR segment longer than the pending budget is split, never truncated."""

        text = str(raw.get("text") or "").strip()
        limit = max(1, int(self.config.max_pending_asr_tokens * 0.6))
        if not text or self._counter.count(text) <= limit:
            return [raw] if text else []

        pieces: list[str] = []
        buffer = ""
        for part in SENTENCE_BREAK.split(text):
            if not part:
                continue
            if buffer and self._counter.count(buffer + part) > limit:
                pieces.append(buffer)
                buffer = part
            else:
                buffer += part
        if buffer:
            pieces.append(buffer)

        split: list[dict[str, object]] = []
        for piece in pieces:
            child = dict(raw)
            child["text"] = piece
            split.append(child)
        return split

    # -- rollout policy ---------------------------------------------------

    def pending_tokens(self, state: MeetingState) -> int:
        return sum(self._counter.count(segment.text) for segment in state.pending_segments)

    def should_rollout(self, state: MeetingState, now: float | None = None) -> bool:
        if not state.pending_segments:
            return False
        tokens = self.pending_tokens(state)
        if tokens < self.config.rollout_min_tokens:
            return False
        if tokens >= self.config.rollout_trigger_tokens:
            return True
        now = now if now is not None else time.time()
        since = now - (state.last_rollout_at or 0)
        return since >= self.config.rollout_max_interval_seconds

    def maybe_rollout(self, meeting_id: str) -> RolloutResult:
        state = self.store.load_state(meeting_id)
        if not self.should_rollout(state):
            return RolloutResult("skipped", "policy_not_triggered")
        return self.run_rollout(meeting_id)

    # -- rollout ----------------------------------------------------------

    def run_rollout(self, meeting_id: str, force: bool = False) -> RolloutResult:
        lock = self._lock_for(meeting_id)
        if not lock.acquire(blocking=False):
            return RolloutResult("skipped", "rollout_already_in_flight")
        try:
            return self._run_rollout_locked(meeting_id, force, allow_switch=True)
        finally:
            lock.release()

    def _run_rollout_locked(self, meeting_id: str, force: bool, allow_switch: bool) -> RolloutResult:
        if self._llm is None:
            return RolloutResult("skipped", "llm_not_ready")
        state = self.store.load_state(meeting_id)
        if not state.pending_segments:
            return RolloutResult("skipped", "no_pending_segments")
        if not force and not self.should_rollout(state):
            return RolloutResult("skipped", "policy_not_triggered")

        builder = ContextBuilder(self.config, self._counter)
        began = time.monotonic()
        metrics: dict[str, object] = {
            "meeting_id": meeting_id,
            "state_version_before": state.state_version,
            "pending_segment_count": len(state.pending_segments),
            "forced": force,
            "tokenizer": self._counter.name,
        }

        try:
            context = builder.build(state)
        except ContextBudgetExceeded as exc:
            metrics["error"] = f"ContextBudgetExceeded: {exc}"
            return RolloutResult("failed", str(exc), metrics)

        if context.compacted_section is not None and state.current_section is not None:
            # Never compact the only copy: archive the pre-compaction section first.
            self.store.save_section_revision(meeting_id, state.current_section, "pre_compaction")

        metrics.update(context.tokens)
        metrics["compaction_applied"] = context.compaction_applied
        metrics["consumed_segment_count"] = len(context.included_segment_ids)

        # Only the first pass may switch topics: after a return_to_section the
        # archived section is reloaded in full and the batch is summarised again
        # with that content in context, never rebuilt from its index descriptor.
        known_ids = set(state.archived_section_ids) if allow_switch else set()
        update: StateUpdate | None = None
        last_error: Exception | None = None
        for attempt in range(1, self.config.max_rollout_attempts + 1):
            metrics["retry_count"] = attempt - 1
            try:
                response = self._llm.chat(context.messages, self.config.max_output_tokens)
                output_tokens = self._counter.count(response)
                update, notes = parse_state_update(
                    response,
                    config=self.config,
                    known_section_ids=known_ids,
                    has_current_section=state.current_section is not None,
                )
                metrics["output_tokens"] = output_tokens
                if notes:
                    metrics["parse_notes"] = notes
                break
            except InvalidStateUpdate as exc:
                last_error = exc
            except Exception as exc:  # timeouts, runtime errors
                last_error = exc
        if update is None:
            metrics["validation_error"] = f"{type(last_error).__name__}: {last_error}"
            metrics["latency_ms"] = round((time.monotonic() - began) * 1000, 1)
            return RolloutResult("failed", str(last_error), metrics)

        if update.operation is TopicOperation.RETURN_TO_SECTION:
            switch = self._plan_switch(state, update.target_section_id or "")
            metrics["operation"] = "return_to_section"
            metrics["target_section_id"] = update.target_section_id
            metrics["consumed_segment_count"] = 0
            metrics["latency_ms"] = round((time.monotonic() - began) * 1000, 1)
            switch.metrics = metrics
            try:
                metrics["state_version_after"] = self.store.commit_rollout(switch)
            except (ConcurrentStateUpdate, InvalidStateUpdate) as exc:
                metrics["validation_error"] = str(exc)
                return RolloutResult("failed", str(exc), metrics)
            # Re-run with the reloaded section in context so the new ASR is
            # merged into the real archived content.
            return self._run_rollout_locked(meeting_id, force=True, allow_switch=False)

        try:
            commit = self._plan_commit(state, update, context.included_segment_ids)
        except InvalidStateUpdate as exc:
            metrics["validation_error"] = str(exc)
            metrics["latency_ms"] = round((time.monotonic() - began) * 1000, 1)
            return RolloutResult("failed", str(exc), metrics)
        commit.metrics = metrics
        metrics["operation"] = update.operation.value
        metrics["target_section_id"] = update.target_section_id
        metrics["section_id"] = commit.current_section.section_id
        metrics["latency_ms"] = round((time.monotonic() - began) * 1000, 1)
        try:
            version = self.store.commit_rollout(commit)
        except ConcurrentStateUpdate as exc:
            metrics["validation_error"] = str(exc)
            return RolloutResult("failed", str(exc), metrics)
        metrics["state_version_after"] = version
        return RolloutResult("applied", update.operation.value, metrics)

    def _plan_commit(
        self, state: MeetingState, update: StateUpdate, consumed_ids: list[str]
    ) -> RolloutCommit:
        now = time.time()
        archived: MeetingSection | None = None
        next_section_number = state.next_section_number

        if update.operation is TopicOperation.CONTINUE:
            base = state.current_section
            assert base is not None  # guaranteed by parse_state_update
        elif update.operation is TopicOperation.NEW_SECTION:
            archived = state.current_section
            base = MeetingSection(
                section_id=f"sec{next_section_number}",
                title=update.title,
                summary=update.summary,
                created_at=now,
                updated_at=now,
            )
            next_section_number += 1
        else:  # pragma: no cover - return_to_section commits through _plan_switch
            raise InvalidStateUpdate("return_to_section is applied as a separate switch")

        section = MeetingSection(
            section_id=base.section_id,
            title=update.title,
            summary=update.summary,
            short_descriptor=update.short_descriptor,
            key_points=update.key_points,
            decisions=update.decisions,
            action_items=update.action_items,
            open_questions=update.open_questions,
            created_at=base.created_at,
            updated_at=now,
            source_segment_ids=list(dict.fromkeys([*base.source_segment_ids, *consumed_ids])),
        )
        if archived is not None and archived.section_id == section.section_id:
            archived = None

        return RolloutCommit(
            meeting_id=state.meeting_id,
            expected_version=state.state_version,
            current_section=section,
            section_position=self.store.next_section_position(state.meeting_id),
            archived_section=archived,
            consumed_segment_ids=consumed_ids,
            next_section_number=next_section_number,
        )

    def _plan_switch(self, state: MeetingState, target_section_id: str) -> RolloutCommit:
        """Archives the current topic and makes an archived one current again.

        No segments are consumed here: the pending batch is summarised by the
        follow-up rollout, which sees the reloaded section in full.
        """

        target = self.store.get_section(state.meeting_id, target_section_id)
        if target is None:
            raise InvalidStateUpdate(f"archived section {target_section_id!r} is missing")
        return RolloutCommit(
            meeting_id=state.meeting_id,
            expected_version=state.state_version,
            current_section=target,
            section_position=self.store.next_section_position(state.meeting_id),
            archived_section=state.current_section,
            consumed_segment_ids=[],
        )

    # -- meeting end ------------------------------------------------------

    def flush(self, meeting_id: str) -> list[RolloutResult]:
        """Drains the pending queue before finalization; each pass is one bounded rollout."""

        results: list[RolloutResult] = []
        while True:
            result = self.run_rollout(meeting_id, force=True)
            results.append(result)
            if result.status != "applied":
                return results
            if not self.store.load_state(meeting_id).pending_segments:
                return results
