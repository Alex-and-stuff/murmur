"""Speech-to-text backends and their load state."""

from __future__ import annotations

import threading
import time
from typing import Protocol

import numpy as np

from backend.config import SAMPLE_RATE


class ASRBackend(Protocol):
    name: str

    def transcribe(self, samples: np.ndarray, language: str) -> tuple[str, str]: ...


class MLXQwenBackend:
    name = "Qwen3-ASR 1.7B · MLX 8-bit"

    def __init__(self, model_name: str):
        from mlx_audio.stt.utils import load_model

        self.model = load_model(model_name)
        self._lock = threading.Lock()

    def transcribe(self, samples: np.ndarray, language: str) -> tuple[str, str]:
        with self._lock:
            result = self.model.generate(
                samples,
                language=language,
                max_tokens=256,
                min_chunk_duration=1.0,
                verbose=False,
            )
        detected = result.language
        if isinstance(detected, list):
            detected = next((item for item in detected if item), language)
        return result.text.strip(), str(detected or language)


class TransformersQwenBackend:
    """Qwen3-ASR through the official Transformers/CUDA runtime."""

    def __init__(self, model_name: str):
        import torch
        from qwen_asr import Qwen3ASRModel

        if not torch.cuda.is_available():
            raise RuntimeError("CUDA is required for the transformers ASR backend")
        self.name = f"{model_name} · Transformers CUDA"
        self.model = Qwen3ASRModel.from_pretrained(
            model_name,
            dtype=torch.float16,
            device_map="cuda:0",
            max_inference_batch_size=1,
            max_new_tokens=256,
        )
        self._lock = threading.Lock()

    def transcribe(self, samples: np.ndarray, language: str) -> tuple[str, str]:
        with self._lock:
            result = self.model.transcribe(
                audio=(samples, SAMPLE_RATE),
                language=language or None,
            )[0]
        detected = getattr(result, "language", None) or language
        text = str(getattr(result, "text", result)).strip()
        return text, str(detected)


class FixtureBackend:
    """Small deterministic backend used only by the automated smoke tests."""

    name = "fixture"

    def transcribe(self, samples: np.ndarray, language: str) -> tuple[str, str]:
        duration = len(samples) / SAMPLE_RATE
        return f"測試音訊 {duration:.1f} 秒", language


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
            elif backend_name == "transformers":
                backend = TransformersQwenBackend(model_name)
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
