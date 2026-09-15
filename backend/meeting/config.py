"""Tunable budgets for the online meeting-state rollouts.

Every number here is configurable; none of it may be hard-coded at a call site.
`max_context_tokens` is treated as an *application-level* budget shared by input
and output, which stays correct whether or not the backend separates the two.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(slots=True)
class MeetingSummaryConfig:
    # Context budget
    max_context_tokens: int = 3072
    output_reserve_tokens: int = 512
    max_instruction_tokens: int = 450
    max_index_tokens: int = 300
    max_current_section_tokens: int = 600
    max_pending_asr_tokens: int = 1000
    safety_margin_tokens: int = 210

    # Rollout policy
    rollout_trigger_tokens: int = 500
    rollout_max_interval_seconds: float = 60.0
    rollout_min_tokens: int = 40

    # Output shape
    max_key_points: int = 6
    max_decisions: int = 6
    max_action_items: int = 8
    max_open_questions: int = 6
    max_descriptor_chars: int = 28

    # Reliability
    max_rollout_attempts: int = 2
    recent_index_entries: int = 12

    @property
    def max_input_tokens(self) -> int:
        return self.max_context_tokens - self.output_reserve_tokens

    @property
    def max_output_tokens(self) -> int:
        return self.output_reserve_tokens

    def validate(self) -> None:
        parts = (
            self.max_instruction_tokens
            + self.max_index_tokens
            + self.max_current_section_tokens
            + self.max_pending_asr_tokens
            + self.safety_margin_tokens
        )
        if parts > self.max_input_tokens:
            raise ValueError(
                f"context budget over-subscribed: sections total {parts} tokens "
                f"but only {self.max_input_tokens} input tokens are available"
            )

    @classmethod
    def from_env(cls) -> "MeetingSummaryConfig":
        base = cls()
        config = cls(
            max_context_tokens=_env_int("MURMUR_MAX_CONTEXT_TOKENS", base.max_context_tokens),
            output_reserve_tokens=_env_int("MURMUR_OUTPUT_RESERVE_TOKENS", base.output_reserve_tokens),
            max_instruction_tokens=_env_int("MURMUR_MAX_INSTRUCTION_TOKENS", base.max_instruction_tokens),
            max_index_tokens=_env_int("MURMUR_MAX_INDEX_TOKENS", base.max_index_tokens),
            max_current_section_tokens=_env_int(
                "MURMUR_MAX_CURRENT_SECTION_TOKENS", base.max_current_section_tokens
            ),
            max_pending_asr_tokens=_env_int("MURMUR_MAX_PENDING_ASR_TOKENS", base.max_pending_asr_tokens),
            safety_margin_tokens=_env_int("MURMUR_SAFETY_MARGIN_TOKENS", base.safety_margin_tokens),
            rollout_trigger_tokens=_env_int("MURMUR_ROLLOUT_TRIGGER_TOKENS", base.rollout_trigger_tokens),
            rollout_max_interval_seconds=float(
                _env_int(
                    "MURMUR_ROLLOUT_MAX_INTERVAL_SECONDS", int(base.rollout_max_interval_seconds)
                )
            ),
            rollout_min_tokens=_env_int("MURMUR_ROLLOUT_MIN_TOKENS", base.rollout_min_tokens),
        )
        config.validate()
        return config


class ContextBudgetExceeded(Exception):
    """Raised instead of silently truncating a prompt that will not fit."""
