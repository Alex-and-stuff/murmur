"""Builds one rollout prompt inside a hard token budget.

Nothing here relies on the model API truncating for us: every block is measured,
compaction is explicit and reported, and a prompt that still does not fit raises
instead of being silently cut.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from backend.meeting.config import ContextBudgetExceeded, MeetingSummaryConfig
from backend.meeting.models import ASRSegment, MeetingSection, MeetingState, SectionIndexEntry
from backend.meeting.prompts import SYSTEM_PROMPT
from backend.meeting.tokens import TokenCounter

MAX_DESCRIPTOR_CHARS = 28


@dataclass(slots=True)
class BuiltContext:
    messages: list[dict[str, str]]
    user_content: str
    included_segment_ids: list[str] = field(default_factory=list)
    deferred_segment_ids: list[str] = field(default_factory=list)
    tokens: dict[str, int] = field(default_factory=dict)
    compaction_applied: list[str] = field(default_factory=list)
    compacted_section: MeetingSection | None = None


def render_index(entries: list[SectionIndexEntry]) -> str:
    if not entries:
        return "（尚無先前主題）"
    return "\n".join(
        f"{position}. [{entry.section_id}] {entry.title} — {entry.short_descriptor}"
        for position, entry in enumerate(entries, start=1)
    )


def render_section(section: MeetingSection | None) -> str:
    if section is None:
        return "（尚未建立主題）"
    lines = [f"section_id: {section.section_id}", f"標題：{section.title}", f"摘要：{section.summary}"]
    if section.key_points:
        lines.append("重點：" + "；".join(section.key_points))
    if section.decisions:
        lines.append("決策：" + "；".join(section.decisions))
    if section.action_items:
        lines.append(
            "待辦："
            + "；".join(
                item.description + (f"（{item.owner}）" if item.owner else "")
                for item in section.action_items
            )
        )
    if section.open_questions:
        lines.append("未解問題：" + "；".join(section.open_questions))
    return "\n".join(lines)


def render_segments(segments: list[ASRSegment]) -> str:
    if not segments:
        return "（沒有新逐字稿）"
    lines = []
    for segment in segments:
        stamp = ""
        if segment.started_at is not None:
            end = segment.ended_at if segment.ended_at is not None else segment.started_at
            stamp = f"[{segment.started_at:.0f}s–{end:.0f}s] "
        lines.append(f"({segment.segment_id}) {stamp}{segment.text}")
    return "\n".join(lines)


class ContextBuilder:
    def __init__(self, config: MeetingSummaryConfig, counter: TokenCounter):
        self.config = config
        self.counter = counter

    def build(self, state: MeetingState) -> BuiltContext:
        config = self.config
        count = self.counter.count

        instruction_tokens = count(SYSTEM_PROMPT)
        if instruction_tokens > config.max_instruction_tokens:
            raise ContextBudgetExceeded(
                f"instructions need {instruction_tokens} tokens but only "
                f"{config.max_instruction_tokens} are budgeted"
            )

        compaction: list[str] = []
        index_text, index_tokens = self._fit_index(state.section_index, compaction)
        section, section_text, section_tokens = self._fit_section(state.current_section, compaction)

        overhead = count(self._frame("", "", ""))
        spent = instruction_tokens + index_tokens + section_tokens + overhead
        pending_budget = min(
            config.max_pending_asr_tokens,
            config.max_input_tokens - config.safety_margin_tokens - spent,
        )
        included, deferred, pending_tokens = self._fit_segments(state.pending_segments, pending_budget)
        if deferred:
            compaction.append(f"deferred_{len(deferred)}_segments")

        user_content = self._frame(index_text, section_text, render_segments(included))
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]
        total = instruction_tokens + count(user_content)
        if total > config.max_input_tokens:
            raise ContextBudgetExceeded(
                f"prompt is {total} tokens after compaction, budget is {config.max_input_tokens}"
            )

        return BuiltContext(
            messages=messages,
            user_content=user_content,
            included_segment_ids=[segment.segment_id for segment in included],
            deferred_segment_ids=[segment.segment_id for segment in deferred],
            tokens={
                "instruction": instruction_tokens,
                "index": index_tokens,
                "current_section": section_tokens,
                "pending_asr": pending_tokens,
                "total_input": total,
                "budget_input": config.max_input_tokens,
            },
            compaction_applied=compaction,
            compacted_section=section if section is not state.current_section else None,
        )

    @staticmethod
    def _frame(index_text: str, section_text: str, segment_text: str) -> str:
        return (
            f"# 先前主題索引\n{index_text}\n\n"
            f"# 目前主題（完整內容）\n{section_text}\n\n"
            f"# 新逐字稿\n{segment_text}\n"
        )

    def _fit_index(self, entries: list[SectionIndexEntry], compaction: list[str]) -> tuple[str, int]:
        budget = self.config.max_index_tokens
        text = render_index(entries)
        tokens = self.counter.count(text)
        if tokens <= budget:
            return text, tokens

        trimmed = [
            replace(entry, short_descriptor=entry.short_descriptor[:MAX_DESCRIPTOR_CHARS])
            for entry in entries
        ]
        text = render_index(trimmed)
        tokens = self.counter.count(text)
        compaction.append("index_descriptors_trimmed")
        if tokens <= budget:
            return text, tokens

        # Older topics collapse into one grouped line; recent ones keep their descriptor.
        recent_count = self.config.recent_index_entries
        while recent_count >= 1:
            older, recent = trimmed[:-recent_count], trimmed[-recent_count:]
            grouped = "較早主題：" + "、".join(entry.title for entry in older) if older else ""
            text = "\n".join(filter(None, [grouped, render_index(recent)]))
            tokens = self.counter.count(text)
            if tokens <= budget:
                compaction.append("index_grouped")
                return text, tokens
            recent_count //= 2

        titles = "較早主題：" + "、".join(entry.title for entry in trimmed)
        tokens = self.counter.count(titles)
        compaction.append("index_titles_only")
        if tokens > budget:
            raise ContextBudgetExceeded(
                f"section index needs {tokens} tokens even as titles only, budget is {budget}"
            )
        return titles, tokens

    def _fit_section(
        self, section: MeetingSection | None, compaction: list[str]
    ) -> tuple[MeetingSection | None, str, int]:
        budget = self.config.max_current_section_tokens
        text = render_section(section)
        tokens = self.counter.count(text)
        if section is None or tokens <= budget:
            return section, text, tokens

        # Explicit, ordered compaction. Decisions and action items are never
        # dropped here; the pre-compaction section is archived by the caller.
        working = MeetingSection(
            section_id=section.section_id,
            title=section.title,
            summary=section.summary,
            key_points=list(section.key_points),
            decisions=list(section.decisions),
            action_items=list(section.action_items),
            open_questions=list(section.open_questions),
            created_at=section.created_at,
            updated_at=section.updated_at,
            source_segment_ids=list(section.source_segment_ids),
        )
        for shrink in (
            lambda: working.open_questions.pop() if working.open_questions else None,
            lambda: working.key_points.pop() if len(working.key_points) > 3 else None,
        ):
            while True:
                text = render_section(working)
                tokens = self.counter.count(text)
                if tokens <= budget or shrink() is None:
                    break
            if tokens <= budget:
                compaction.append("current_section_compacted")
                return working, text, tokens

        raise ContextBudgetExceeded(
            f"current section needs {tokens} tokens after compaction, budget is {budget}"
        )

    def _fit_segments(
        self, segments: list[ASRSegment], budget: int
    ) -> tuple[list[ASRSegment], list[ASRSegment], int]:
        included: list[ASRSegment] = []
        for position, segment in enumerate(segments):
            candidate = included + [segment]
            tokens = self.counter.count(render_segments(candidate))
            if tokens > budget and included:
                return included, segments[position:], self.counter.count(render_segments(included))
            if tokens > budget:
                # A single segment over budget still goes through: the engine
                # splits oversized ASR before it ever reaches the queue.
                return candidate, segments[position + 1 :], tokens
            included = candidate
        return included, [], self.counter.count(render_segments(included))
