"""Token counting for the online context budget.

Counts must match the tokenizer of the backend that actually runs inference.
When that tokenizer is unavailable we fall back to a deliberately conservative
character heuristic and the caller is expected to keep a larger safety margin.
"""

from __future__ import annotations

import math
import threading
from typing import Protocol


class TokenCounter(Protocol):
    name: str
    exact: bool

    def count(self, text: str) -> int: ...


def _is_cjk(char: str) -> bool:
    code = ord(char)
    return (
        0x3040 <= code <= 0x30FF  # kana
        or 0x3400 <= code <= 0x4DBF  # CJK ext A
        or 0x4E00 <= code <= 0x9FFF  # CJK unified
        or 0xF900 <= code <= 0xFAFF  # compatibility
        or 0xFF00 <= code <= 0xFF60  # fullwidth forms
    )


class HeuristicTokenCounter:
    """Over-estimates rather than under-estimates: budgets must never be exceeded."""

    name = "heuristic"
    exact = False

    def __init__(self, chars_per_token: float = 3.0, inflation: float = 1.1):
        self.chars_per_token = chars_per_token
        self.inflation = inflation

    def count(self, text: str) -> int:
        if not text:
            return 0
        cjk = sum(1 for char in text if _is_cjk(char))
        other = len(text) - cjk
        estimate = cjk + other / self.chars_per_token
        return max(1, math.ceil(estimate * self.inflation))


class TokenizerTokenCounter:
    """Wraps the tokenizer of the summary model so counts match inference exactly."""

    name = "tokenizer"
    exact = True

    def __init__(self, tokenizer, fallback: TokenCounter | None = None):
        self.tokenizer = tokenizer
        self.fallback = fallback or HeuristicTokenCounter()
        self._lock = threading.Lock()

    def count(self, text: str) -> int:
        if not text:
            return 0
        try:
            with self._lock:
                return len(self.tokenizer.encode(text))
        except Exception:
            return self.fallback.count(text)
