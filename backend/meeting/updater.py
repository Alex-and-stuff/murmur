"""Turns one model response into a validated state update.

A malformed or hallucinating response must never reach the store: parsing
failures raise, the caller retries, and on final failure the committed state and
the pending segments are both left untouched.
"""

from __future__ import annotations

import json

from backend.meeting.config import MeetingSummaryConfig
from backend.meeting.models import ActionItem, StateUpdate, TopicOperation


class InvalidStateUpdate(ValueError):
    """The model returned something that cannot be applied to the state."""


def extract_json_object(text: str) -> dict[str, object]:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise InvalidStateUpdate("no JSON object found in model output")
    try:
        payload = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise InvalidStateUpdate(f"model output is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise InvalidStateUpdate("model output is not a JSON object")
    return payload


def parse_state_update(
    text: str,
    *,
    config: MeetingSummaryConfig,
    known_section_ids: set[str],
    has_current_section: bool,
) -> tuple[StateUpdate, list[str]]:
    payload = extract_json_object(text)
    notes: list[str] = []

    raw_operation = str(payload.get("operation") or "").strip().lower()
    try:
        operation = TopicOperation(raw_operation)
    except ValueError as exc:
        raise InvalidStateUpdate(f"unknown operation {raw_operation!r}") from exc

    target = payload.get("target_section_id")
    target_section_id = str(target).strip() if target not in (None, "") else None

    if operation is TopicOperation.RETURN_TO_SECTION:
        if not target_section_id:
            raise InvalidStateUpdate("return_to_section without target_section_id")
        if target_section_id not in known_section_ids:
            raise InvalidStateUpdate(f"unknown target_section_id {target_section_id!r}")
    else:
        target_section_id = None

    if operation is TopicOperation.CONTINUE and not has_current_section:
        operation = TopicOperation.NEW_SECTION
        notes.append("continue_without_current_section_coerced_to_new_section")

    title = str(payload.get("title") or "").strip()
    summary = str(payload.get("summary") or "").strip()
    if not title:
        raise InvalidStateUpdate("state update is missing a title")
    if not summary:
        raise InvalidStateUpdate("state update is missing a summary")

    descriptor = str(payload.get("short_descriptor") or "").strip()[: config.max_descriptor_chars]
    if not descriptor:
        descriptor = summary[: config.max_descriptor_chars]
        notes.append("descriptor_derived_from_summary")

    def strings(key: str, limit: int) -> list[str]:
        value = payload.get(key)
        if not isinstance(value, list):
            return []
        items = [str(item).strip() for item in value if str(item).strip()]
        return items[:limit]

    actions = []
    raw_actions = payload.get("action_items")
    if isinstance(raw_actions, list):
        for raw in raw_actions[: config.max_action_items]:
            item = ActionItem.parse(raw)
            if item:
                actions.append(item)

    return (
        StateUpdate(
            operation=operation,
            target_section_id=target_section_id,
            title=title,
            summary=summary,
            short_descriptor=descriptor,
            key_points=strings("key_points", config.max_key_points),
            decisions=strings("decisions", config.max_decisions),
            action_items=actions,
            open_questions=strings("open_questions", config.max_open_questions),
        ),
        notes,
    )
