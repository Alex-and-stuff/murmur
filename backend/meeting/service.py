"""Wires the store, engine, scheduler and finalizer into the API surface."""

from __future__ import annotations

import uuid
from pathlib import Path

from backend.llm import ChatLLM, LLMState
from backend.meeting.config import MeetingSummaryConfig
from backend.meeting.engine import MeetingEngine
from backend.meeting.finalizer import LLMFinalizer, StructuralFinalizer
from backend.meeting.scheduler import RolloutScheduler
from backend.meeting.store import MeetingStore


class MeetingService:
    def __init__(
        self,
        store: MeetingStore | None = None,
        config: MeetingSummaryConfig | None = None,
        llm_state: LLMState | None = None,
        scheduler_interval: float = 1.0,
        db_path: Path | str = ":memory:",
    ):
        self.config = config or MeetingSummaryConfig.from_env()
        self.store = store or MeetingStore(db_path)
        self.llm_state = llm_state or LLMState("summary")
        self.engine = MeetingEngine(self.store, self.config)
        self.scheduler = RolloutScheduler(self.engine, scheduler_interval)
        self.finalizer = StructuralFinalizer()
        if self.llm_state.llm is not None:
            self.attach_llm(self.llm_state.llm)

    def attach_llm(self, llm: ChatLLM) -> None:
        self.engine.attach_llm(llm)
        self.finalizer = LLMFinalizer(
            llm,
            self.engine.counter,
            max_input_tokens=self.config.max_input_tokens,
            max_output_tokens=self.config.max_output_tokens,
        )

    def start(self) -> None:
        self.scheduler.start()

    def stop(self) -> None:
        self.scheduler.stop()

    # -- API ---------------------------------------------------------------

    def create_meeting(self) -> dict[str, object]:
        meeting_id = uuid.uuid4().hex[:12]
        self.engine.create_meeting(meeting_id)
        self.scheduler.notify(meeting_id)
        return {"meeting_id": meeting_id, "state": self.state(meeting_id)}

    def add_segments(self, meeting_id: str, segments: list[dict[str, object]]) -> dict[str, object]:
        accepted = self.engine.add_segments(meeting_id, segments)
        self.scheduler.notify(meeting_id)
        return {
            "accepted": [segment.to_dict() for segment in accepted],
            "state": self.state(meeting_id),
        }

    def state(self, meeting_id: str) -> dict[str, object]:
        state = self.store.load_state(meeting_id)
        payload = state.to_dict()
        payload["pending_segment_count"] = len(state.pending_segments)
        payload["pending_tokens"] = self.engine.pending_tokens(state)
        payload["pending_segments"] = [segment.to_dict() for segment in state.pending_segments]
        payload["budget"] = {
            "max_context_tokens": self.config.max_context_tokens,
            "max_input_tokens": self.config.max_input_tokens,
            "output_reserve_tokens": self.config.output_reserve_tokens,
            "tokenizer": self.engine.counter.name,
        }
        payload["rollouts"] = self.store.rollout_metrics(meeting_id, limit=20)
        payload["final_document"] = self.store.final_document(meeting_id)
        return payload

    def set_auto_rollout(self, meeting_id: str, enabled: bool) -> dict[str, object]:
        """Pausing only stops the ticker; segments keep queueing and are flushed later."""

        if enabled:
            self.scheduler.notify(meeting_id)
        else:
            self.scheduler.forget(meeting_id)
        return {"auto_rollout": enabled, "state": self.state(meeting_id)}

    def rollout(self, meeting_id: str, force: bool = True) -> dict[str, object]:
        result = self.engine.run_rollout(meeting_id, force=force)
        return {
            "status": result.status,
            "reason": result.reason,
            "metrics": result.metrics,
            "state": self.state(meeting_id),
        }

    def finalize(self, meeting_id: str) -> dict[str, object]:
        flushes = [
            {"status": result.status, "reason": result.reason}
            for result in self.engine.flush(meeting_id)
        ]
        sections = self.store.sections_in_order(meeting_id)
        document = self.finalizer.finalize(sections)
        document["finalizer"] = self.finalizer.name
        self.store.save_final_document(meeting_id, document)
        self.scheduler.forget(meeting_id)
        return {"flushes": flushes, "document": document, "state": self.state(meeting_id)}

    def transcript(self, meeting_id: str) -> dict[str, object]:
        return {"segments": [segment.to_dict() for segment in self.store.transcript(meeting_id)]}

    def snapshot(self) -> dict[str, object]:
        payload = self.llm_state.snapshot()
        payload["max_context_tokens"] = self.config.max_context_tokens
        payload["max_input_tokens"] = self.config.max_input_tokens
        return payload
