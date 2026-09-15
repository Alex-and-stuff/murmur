"""Meeting-summary backends and their load state."""

from __future__ import annotations

import json
import sys
import threading
from typing import Protocol


class SummaryBackend(Protocol):
    name: str

    def summarize(
        self,
        transcript: str,
        previous_summary: dict[str, object] | None = None,
    ) -> dict[str, object]: ...


def normalize_summary(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise ValueError("summary response is not a JSON object")
    summary = str(payload.get("summary") or "").strip()
    if not summary:
        raise ValueError("summary response is missing summary")

    def strings(key: str, limit: int) -> list[str]:
        value = payload.get(key, [])
        if not isinstance(value, list):
            return []
        items = [str(item).strip() for item in value if str(item).strip()]
        return items[:limit]

    action_items = []
    raw_actions = payload.get("action_items", [])
    if isinstance(raw_actions, list):
        for item in raw_actions:
            if isinstance(item, dict) and str(item.get("task") or "").strip():
                action_items.append(
                    {
                        "task": str(item["task"]).strip(),
                        "owner": str(item.get("owner") or "").strip() or None,
                        "due": str(item.get("due") or "").strip() or None,
                    }
                )
            elif str(item).strip():
                action_items.append({"task": str(item).strip(), "owner": None, "due": None})
    return {
        "summary": summary,
        "key_points": strings("key_points", 6),
        "decisions": strings("decisions", 6),
        "action_items": action_items[:8],
    }


class MLXSummaryBackend:
    def __init__(self, model_name: str):
        from mlx_lm import generate, load

        self.name = f"{model_name} · MLX"
        self.model, self.tokenizer = load(model_name)
        self._generate = generate
        self._lock = threading.Lock()

    def summarize(
        self,
        transcript: str,
        previous_summary: dict[str, object] | None = None,
    ) -> dict[str, object]:
        if previous_summary:
            task = (
                "把尚未納入的會議發言整合進目前的紀要草稿，然後重寫整份紀要。"
                "保留仍有效的事實；若發言補充或修正先前資訊，以較新的明確說法為準。"
                "目前紀要只是可修改的草稿，不要沿用其中重複、瑣碎或描述整理過程的句子。"
                "如果新發言只是替某個既有 key_point 補充例子、細節或後續發展，"
                "直接改寫那一項讓它更完整，不要另外新增一條；"
                "只有出現真正獨立的新主題時才新增 key_point，並視需要刪除已被新主題涵蓋的舊項目。"
                f"\n\n目前紀要草稿 JSON：\n{json.dumps(previous_summary, ensure_ascii=False)}"
                f"\n\n尚未納入的會議發言：\n{transcript}"
            )
        else:
            task = f"請整理以下逐字稿：\n\n{transcript}"
        messages = [
            {
                "role": "system",
                "content": (
                    "你是會議紀錄助手。只根據逐字稿整理內容，不得補充未出現的事實。"
                    "每次都要輸出一份可獨立閱讀的最新完整會議紀要，而不是更新日誌或時間軸記錄。"
                    "\n\nkey_points 的每一項要對應一個獨立的討論主題或論點，而不是逐字稿裡的單一事件、"
                    "動作或時間點。用一到兩句話講清楚這個主題在討論什麼、結論或洞見是什麼；"
                    "如果逐字稿裡有多個例子、細節或延伸描述屬於同一個主題，把它們整合進同一項描述裡"
                    "（例如用「例如…」帶出一個代表性例子），不要每個細節都各自成一條。"
                    "避免逐句複述逐字稿或條列流水帳式的事件清單——例如同一個人做的一連串小動作"
                    "（開了頻道、取了名字、上傳了頭像）不要拆成三條，應該合併成一條講清楚整體在做什麼、為什麼值得注意。"
                    "\n\n不得在結果中提及「逐字稿」「新加入」「既有摘要」「上一批」「本次更新」"
                    "或任何資料處理與生成過程。摘要（summary）用二至四句話點出整場討論的主軸與脈絡，"
                    "不是逐項清單的複述；key_points 最多六項且彼此主題不重疊；決策最多六項；"
                    "待辦事項最多八項。合併語意相同或高度相關的內容，刪除重複、過時與瑣碎項目。"
                    "請輸出繁體中文 JSON，不要使用 Markdown。格式必須是："
                    '{"summary":"...","key_points":["..."],"decisions":["..."],'
                    '"action_items":[{"task":"...","owner":null,"due":null}]}。'
                    "若逐字稿未提到決策、負責人或期限，使用空陣列或 null。"
                ),
            },
            {"role": "user", "content": task},
        ]
        prompt = self.tokenizer.apply_chat_template(
            messages,
            add_generation_prompt=True,
            enable_thinking=False,
        )
        try:
            context_tokens = len(self.tokenizer.encode(prompt))
        except (AttributeError, TypeError, ValueError):
            context_tokens = None
        text = ""
        last_error: Exception | None = None
        with self._lock:
            for attempt, max_tokens in enumerate((1_536, 3_072), start=1):
                response = self._generate(
                    self.model,
                    self.tokenizer,
                    prompt=prompt,
                    max_tokens=max_tokens,
                    verbose=False,
                )
                text = str(response).strip()
                if "</think>" in text:
                    text = text.split("</think>", 1)[1].strip()
                start, end = text.find("{"), text.rfind("}")
                if start >= 0 and end > start:
                    try:
                        result = normalize_summary(json.loads(text[start : end + 1]))
                        result["context_tokens"] = context_tokens
                        return result
                    except json.JSONDecodeError as exc:
                        last_error = exc
                else:
                    last_error = ValueError("no JSON object found in model output")
                print(
                    f"[summary] attempt {attempt} at max_tokens={max_tokens} did not yield valid JSON "
                    f"({last_error}); output tail: {text[-200:]!r}",
                    file=sys.stderr,
                )
        raise ValueError(
            f"summary model did not return JSON after {attempt} attempt(s): {last_error}"
        )


class FixtureSummaryBackend:
    name = "fixture-summary"

    def summarize(
        self,
        transcript: str,
        previous_summary: dict[str, object] | None = None,
    ) -> dict[str, object]:
        return {
            "summary": "這是一份測試逐字稿摘要。",
            "key_points": ["摘要 API 已收到逐字稿"],
            "decisions": [],
            "action_items": [],
        }


class BackendState:
    def __init__(self):
        self.backend: ASRBackend | None = None
        self.status = "loading"
        self.error: str | None = None
        self.started_at = time.monotonic()
        self._lock = threading.Lock()

    def load(self, backend_name: str, model_name: str) -> None:
        try:
            backend: ASRBackend
            if backend_name == "fixture":
                backend = FixtureBackend()
            else:
                backend = MLXQwenBackend(model_name)
            with self._lock:
                self.backend = backend
                self.status = "ready"
        except Exception as exc:
            with self._lock:
                self.status = "error"
                self.error = f"{type(exc).__name__}: {exc}"

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return {
                "status": self.status,
                "model": self.backend.name if self.backend else None,
                "error": self.error,
                "sample_rate": SAMPLE_RATE,
                "uptime_seconds": round(time.monotonic() - self.started_at, 2),
            }


class SummaryState:
    def __init__(self):
        self.backend: SummaryBackend | None = None
        self.status = "loading"
        self.error: str | None = None
        self._lock = threading.Lock()

    def load(self, backend_name: str, model_name: str) -> None:
        try:
            backend: SummaryBackend
            if backend_name == "fixture":
                backend = FixtureSummaryBackend()
            else:
                backend = MLXSummaryBackend(model_name)
            with self._lock:
                self.backend = backend
                self.status = "ready"
        except Exception as exc:
            with self._lock:
                self.status = "error"
                self.error = f"{type(exc).__name__}: {exc}"

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return {
                "status": self.status,
                "model": self.backend.name if self.backend else None,
                "error": self.error,
            }
