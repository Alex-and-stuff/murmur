"""Meeting-end consolidation.

Provider-neutral on purpose: the online 8B only maintains state, while the final
minutes are meant to be produced by a larger model (e.g. the company Qwen3.5
122B endpoint). A failure here never touches the committed state or archive.
"""

from __future__ import annotations

import json
from typing import Protocol

from backend.llm import ChatLLM
from backend.meeting.models import MeetingSection
from backend.meeting.prompts import FINALIZER_SYSTEM_PROMPT
from backend.meeting.tokens import TokenCounter
from backend.meeting.updater import extract_json_object


def aggregate(sections: list[MeetingSection]) -> dict[str, object]:
    """Ordered topics plus deduplicated decisions, action items and open questions."""

    topics, decisions, open_questions = [], [], []
    action_items: list[dict[str, object]] = []
    for section in sections:
        topics.append(
            {
                "section_id": section.section_id,
                "title": section.title,
                "summary": section.summary,
                "key_points": section.key_points,
                "source_segment_ids": section.source_segment_ids,
            }
        )
        for decision in section.decisions:
            if decision not in decisions:
                decisions.append(decision)
        for question in section.open_questions:
            if question not in open_questions:
                open_questions.append(question)
        for item in section.action_items:
            payload = item.to_dict()
            if payload not in action_items:
                action_items.append(payload)
    return {
        "topics": topics,
        "decisions": decisions,
        "action_items": action_items,
        "open_questions": open_questions,
    }


class MeetingFinalizer(Protocol):
    name: str

    def finalize(self, sections: list[MeetingSection]) -> dict[str, object]: ...


class StructuralFinalizer:
    """No model involved: always available, and the fallback when prose generation fails."""

    name = "structural"

    def finalize(self, sections: list[MeetingSection]) -> dict[str, object]:
        data = aggregate(sections)
        data["executive_summary"] = "\n".join(
            f"{topic['title']}：{topic['summary']}" for topic in data["topics"]
        )
        data["risks"] = []
        data["mode"] = "structural"
        return data


class LLMFinalizer:
    """Asks a chat model for the prose parts, with the structural merge as fallback."""

    def __init__(self, llm: ChatLLM, counter: TokenCounter, max_input_tokens: int, max_output_tokens: int = 1024):
        self.llm = llm
        self.counter = counter
        self.max_input_tokens = max_input_tokens
        self.max_output_tokens = max_output_tokens
        self.name = f"llm:{llm.name}"
        self._fallback = StructuralFinalizer()

    def finalize(self, sections: list[MeetingSection]) -> dict[str, object]:
        data = aggregate(sections)
        body = json.dumps(data, ensure_ascii=False, indent=None)
        if self.counter.count(body) > self.max_input_tokens:
            result = self._fallback.finalize(sections)
            result["mode"] = "structural_input_too_large"
            return result

        messages = [
            {"role": "system", "content": FINALIZER_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    "以下是依序排列的各主題內容：\n"
                    f"{body}\n\n"
                    '只輸出繁體中文 JSON：{"executive_summary":"...","risks":["..."]}'
                ),
            },
        ]
        try:
            payload = extract_json_object(self.llm.chat(messages, self.max_output_tokens))
            data["executive_summary"] = str(payload.get("executive_summary") or "").strip()
            data["risks"] = [str(item).strip() for item in payload.get("risks") or [] if str(item).strip()]
            if not data["executive_summary"]:
                raise ValueError("empty executive summary")
            data["mode"] = "llm"
            return data
        except Exception as exc:
            result = self._fallback.finalize(sections)
            result["mode"] = "structural_after_llm_failure"
            result["error"] = f"{type(exc).__name__}: {exc}"
            return result
