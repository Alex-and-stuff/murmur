"""Helpers shared by the meeting tests."""

from __future__ import annotations

import json


def state_update(
    operation: str = "continue",
    title: str = "控制架構",
    summary: str = "討論控制架構與後續驗證。",
    *,
    target_section_id: str | None = None,
    key_points: list[str] | None = None,
    decisions: list[str] | None = None,
    action_items: list[dict] | None = None,
    open_questions: list[str] | None = None,
    descriptor: str = "MPC 串接 BMS",
) -> str:
    return json.dumps(
        {
            "operation": operation,
            "target_section_id": target_section_id,
            "title": title,
            "short_descriptor": descriptor,
            "summary": summary,
            "key_points": key_points if key_points is not None else ["MPC 由 BMS 下發"],
            "decisions": decisions or [],
            "action_items": action_items or [],
            "open_questions": open_questions or [],
        },
        ensure_ascii=False,
    )
