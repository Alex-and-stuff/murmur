"""Speech-to-text backends and their load state."""

from __future__ import annotations

import threading
import time
import uuid
from typing import Protocol

import numpy as np

from backend.config import SAMPLE_RATE


class ASRBackend(Protocol):
    name: str

    def transcribe(self, samples: np.ndarray, language: str) -> tuple[str, str]: ...


class StreamingASRBackend(Protocol):
    """Stateful ASR interface used by the low-latency browser capture path."""

    streaming: bool

    def start_stream(self, language: str) -> str: ...

    def push_stream(self, stream_id: str, samples: np.ndarray) -> tuple[str, str]: ...

    def finish_stream(self, stream_id: str, samples: np.ndarray) -> tuple[str, str]: ...

    def abort_stream(self, stream_id: str) -> None: ...


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


class VllmStreamingBackend:
    """Qwen's official stateful streaming implementation, backed by vLLM."""

    streaming = True

    def __init__(self, model_name: str, gpu_memory_utilization: float = 0.75, max_model_len: int = 4096):
        from qwen_asr import Qwen3ASRModel

        self.name = f"{model_name} · vLLM streaming CUDA"
        self.model = Qwen3ASRModel.LLM(
            model=model_name,
            gpu_memory_utilization=gpu_memory_utilization,
            max_model_len=max_model_len,
            max_new_tokens=32,
        )
        self._sessions: dict[str, object] = {}
        self._lock = threading.Lock()

    def start_stream(self, language: str) -> str:
        stream_id = uuid.uuid4().hex
        state = self.model.init_streaming_state(
            language=language or None,
            chunk_size_sec=1.0,
            unfixed_chunk_num=2,
            unfixed_token_num=5,
        )
        with self._lock:
            self._sessions[stream_id] = state
        return stream_id

    def push_stream(self, stream_id: str, samples: np.ndarray) -> tuple[str, str]:
        with self._lock:
            state = self._sessions.get(stream_id)
            if state is None:
                raise KeyError("unknown_stream")
            self.model.streaming_transcribe(samples, state)
            return str(getattr(state, "text", "")).strip(), str(getattr(state, "language", ""))

    def finish_stream(self, stream_id: str, samples: np.ndarray) -> tuple[str, str]:
        with self._lock:
            state = self._sessions.pop(stream_id, None)
            if state is None:
                raise KeyError("unknown_stream")
            if samples.size:
                self.model.streaming_transcribe(samples, state)
            self.model.finish_streaming_transcribe(state)
            return str(getattr(state, "text", "")).strip(), str(getattr(state, "language", ""))

    def abort_stream(self, stream_id: str) -> None:
        with self._lock:
            self._sessions.pop(stream_id, None)


class FixtureBackend:
    """Small deterministic backend used only by the automated smoke tests."""

    name = "fixture"

    def transcribe(self, samples: np.ndarray, language: str) -> tuple[str, str]:
        duration = len(samples) / SAMPLE_RATE
        return f"測試音訊 {duration:.1f} 秒", language


class FixtureStreamingBackend:
    """Deterministic stateful adapter for the HTTP streaming tests."""

    name = "fixture streaming"
    streaming = True

    def __init__(self):
        self._sessions: dict[str, tuple[float, str]] = {}

    def start_stream(self, language: str) -> str:
        stream_id = uuid.uuid4().hex
        self._sessions[stream_id] = (0.0, language)
        return stream_id

    def push_stream(self, stream_id: str, samples: np.ndarray) -> tuple[str, str]:
        seconds, language = self._sessions[stream_id]
        seconds += len(samples) / SAMPLE_RATE
        self._sessions[stream_id] = (seconds, language)
        return f"測試串流 {seconds:.1f} 秒", language

    def finish_stream(self, stream_id: str, samples: np.ndarray) -> tuple[str, str]:
        seconds, language = self._sessions.pop(stream_id)
        seconds += len(samples) / SAMPLE_RATE
        return f"測試串流 {seconds:.1f} 秒", language

    def abort_stream(self, stream_id: str) -> None:
        self._sessions.pop(stream_id, None)


class BackendState:
    def __init__(self):
        self.backend: ASRBackend | None = None
        self.status = "loading"
        self.error: str | None = None
        self.started_at = time.monotonic()
        self._lock = threading.Lock()

    def load(
        self,
        backend_name: str,
        model_name: str,
        *,
        vllm_gpu_memory_utilization: float = 0.75,
        vllm_max_model_len: int = 4096,
    ) -> None:
        try:
            backend: ASRBackend
            if backend_name == "fixture":
                backend = FixtureBackend()
            elif backend_name == "fixture-streaming":
                backend = FixtureStreamingBackend()
            elif backend_name == "vllm":
                backend = VllmStreamingBackend(
                    model_name,
                    gpu_memory_utilization=vllm_gpu_memory_utilization,
                    max_model_len=vllm_max_model_len,
                )
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
                "streaming": bool(getattr(self.backend, "streaming", False)),
                "uptime_seconds": round(time.monotonic() - self.started_at, 2),
            }
