"""Data model for the bounded online meeting state.

The online state is deliberately fixed-size: only the current working section
carries full detail, earlier topics live in the archive and are represented in
the prompt by a one-line index entry.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from enum import Enum


class TopicOperation(str, Enum):
    CONTINUE = "continue"
    NEW_SECTION = "new_section"
    RETURN_TO_SECTION = "return_to_section"


@dataclass(slots=True)
class ActionItem:
    description: str
    owner: str | None = None
    due: str | None = None
    status: str = "open"

    @classmethod
    def parse(cls, raw: object) -> "ActionItem | None":
        if isinstance(raw, str):
            text = raw.strip()
            return cls(description=text) if text else None
        if not isinstance(raw, dict):
            return None
        description = str(raw.get("description") or raw.get("task") or "").strip()
        if not description:
            return None
        status = str(raw.get("status") or "open").strip().lower()
        if status not in ("open", "done", "cancelled"):
            status = "open"
        return cls(
            description=description,
            owner=str(raw.get("owner") or "").strip() or None,
            due=str(raw.get("due") or raw.get("due_at") or "").strip() or None,
            status=status,
        )

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(slots=True)
class ASRSegment:
    segment_id: str
    text: str
    started_at: float | None = None
    ended_at: float | None = None
    speaker_id: str | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, object]) -> "ASRSegment":
        return cls(
            segment_id=str(raw["segment_id"]),
            text=str(raw.get("text") or ""),
            started_at=_optional_float(raw.get("started_at")),
            ended_at=_optional_float(raw.get("ended_at")),
            speaker_id=str(raw["speaker_id"]) if raw.get("speaker_id") else None,
        )


@dataclass(slots=True)
class MeetingSection:
    section_id: str
    title: str
    summary: str
    short_descriptor: str = ""
    key_points: list[str] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)
    action_items: list[ActionItem] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    source_segment_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["action_items"] = [item.to_dict() for item in self.action_items]
        return payload

    @classmethod
    def from_dict(cls, raw: dict[str, object]) -> "MeetingSection":
        items = [ActionItem.parse(item) for item in raw.get("action_items") or []]
        return cls(
            section_id=str(raw["section_id"]),
            title=str(raw.get("title") or ""),
            summary=str(raw.get("summary") or ""),
            short_descriptor=str(raw.get("short_descriptor") or ""),
            key_points=[str(item) for item in raw.get("key_points") or []],
            decisions=[str(item) for item in raw.get("decisions") or []],
            action_items=[item for item in items if item],
            open_questions=[str(item) for item in raw.get("open_questions") or []],
            created_at=float(raw.get("created_at") or time.time()),
            updated_at=float(raw.get("updated_at") or time.time()),
            source_segment_ids=[str(item) for item in raw.get("source_segment_ids") or []],
        )


@dataclass(slots=True)
class SectionIndexEntry:
    section_id: str
    title: str
    short_descriptor: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, object]) -> "SectionIndexEntry":
        return cls(
            section_id=str(raw["section_id"]),
            title=str(raw.get("title") or ""),
            short_descriptor=str(raw.get("short_descriptor") or ""),
        )


@dataclass(slots=True)
class MeetingState:
    """Everything the next rollout needs, and nothing else.

    `section_index` holds one short line per earlier topic; the full content of
    those topics lives in the archive and is only reloaded on
    `return_to_section` or at meeting end.
    """

    meeting_id: str
    current_section: MeetingSection | None = None
    section_index: list[SectionIndexEntry] = field(default_factory=list)
    archived_section_ids: list[str] = field(default_factory=list)
    pending_segments: list[ASRSegment] = field(default_factory=list)
    state_version: int = 0
    last_rollout_at: float | None = None
    next_segment_number: int = 1
    next_section_number: int = 1

    @property
    def current_section_id(self) -> str | None:
        return self.current_section.section_id if self.current_section else None

    def to_dict(self) -> dict[str, object]:
        return {
            "meeting_id": self.meeting_id,
            "current_section_id": self.current_section_id,
            "current_section": self.current_section.to_dict() if self.current_section else None,
            "section_index": [entry.to_dict() for entry in self.section_index],
            "archived_section_ids": list(self.archived_section_ids),
            "pending_segments": [segment.to_dict() for segment in self.pending_segments],
            "state_version": self.state_version,
            "last_rollout_at": self.last_rollout_at,
        }


@dataclass(slots=True)
class StateUpdate:
    """The structured result of one rollout, before it is applied to the state."""

    operation: TopicOperation
    title: str
    summary: str
    short_descriptor: str
    target_section_id: str | None = None
    key_points: list[str] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)
    action_items: list[ActionItem] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)


def _optional_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
