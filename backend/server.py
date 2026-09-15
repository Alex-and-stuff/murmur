#!/usr/bin/env python3
"""Serve the Murmur UI and run chunked Qwen3-ASR inference locally."""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import traceback
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Protocol
from urllib.parse import urlparse

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = ROOT / "dist"
UPLOADS_DIR = STATIC_DIR / "uploads"
SAMPLE_RATE = 16_000
MAX_AUDIO_BYTES = SAMPLE_RATE * 4 * 30
MAX_FETCH_BODY_BYTES = 4_096
MAX_SUMMARY_BODY_BYTES = 256_000
MAX_SUMMARY_TRANSCRIPT_CHARS = 24_000
ALLOWED_YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
}


def is_allowed_media_url(url: str) -> bool:
    """Only YouTube links are accepted; this must never become an open URL fetcher (SSRF risk)."""

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False
    return (parsed.hostname or "").lower() in ALLOWED_YOUTUBE_HOSTS


class MediaFetchError(Exception):
    def __init__(self, message: str, status: int = HTTPStatus.BAD_GATEWAY):
        super().__init__(message)
        self.status = status


class MediaFetcher(Protocol):
    def fetch(self, url: str) -> dict[str, object]: ...


class YtDlpFetcher:
    """Downloads audio-only from an allow-listed YouTube URL, cached on disk by video id."""

    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def fetch(self, url: str) -> dict[str, object]:
        import yt_dlp

        try:
            with yt_dlp.YoutubeDL({"quiet": True, "noplaylist": True, "skip_download": True}) as probe:
                info = probe.extract_info(url, download=False)
        except Exception as exc:
            raise MediaFetchError(f"無法讀取這個 YouTube 連結：{exc}") from exc

        video_id = info.get("id") if isinstance(info, dict) else None
        if not video_id:
            raise MediaFetchError("無法辨識 YouTube 影片 ID")

        existing = sorted(self.cache_dir.glob(f"{video_id}.*"))
        if not existing:
            options = {
                "quiet": True,
                "noplaylist": True,
                "format": "bestaudio[ext=m4a]/bestaudio/best",
                "outtmpl": str(self.cache_dir / f"{video_id}.%(ext)s"),
            }
            try:
                with yt_dlp.YoutubeDL(options) as downloader:
                    downloader.download([url])
            except Exception as exc:
                raise MediaFetchError(f"下載 YouTube 音訊失敗：{exc}") from exc
            existing = sorted(self.cache_dir.glob(f"{video_id}.*"))
            if not existing:
                raise MediaFetchError("下載完成但找不到音訊檔案", status=HTTPStatus.INTERNAL_SERVER_ERROR)

        return {"url": f"/uploads/{existing[0].name}", "title": str(info.get("title") or video_id)}


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


class FixtureBackend:
    """Small deterministic backend used only by the automated smoke tests."""

    name = "fixture"

    def transcribe(self, samples: np.ndarray, language: str) -> tuple[str, str]:
        duration = len(samples) / SAMPLE_RATE
        return f"測試音訊 {duration:.1f} 秒", language


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


class MurmurHandler(SimpleHTTPRequestHandler):
    server_version = "Murmur/0.1"
    extensions_map = {
        **SimpleHTTPRequestHandler.extensions_map,
        ".m4a": "audio/mp4",
        ".webm": "audio/webm",
        ".opus": "audio/ogg",
        ".mp3": "audio/mpeg",
    }

    def __init__(self, *args, directory: str | None = None, **kwargs):
        super().__init__(*args, directory=str(STATIC_DIR), **kwargs)

    @property
    def state(self) -> BackendState:
        return self.server.backend_state  # type: ignore[attr-defined]

    @property
    def media_fetcher(self) -> MediaFetcher:
        return self.server.media_fetcher  # type: ignore[attr-defined]

    @property
    def summary_state(self) -> SummaryState:
        return self.server.summary_state  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args) -> None:
        print(f"[{self.log_date_time_string()}] {fmt % args}")

    def _json(self, status: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if urlparse(self.path).path == "/api/health":
            payload = self.state.snapshot()
            payload["summary"] = self.summary_state.snapshot()
            self._json(HTTPStatus.OK, payload)
            return
        super().do_GET()

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/fetch-media":
            self._handle_fetch_media()
            return
        if path == "/api/summarize":
            self._handle_summarize()
            return
        if path != "/api/transcribe":
            self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            return

        snapshot = self.state.snapshot()
        if snapshot["status"] != "ready" or self.state.backend is None:
            self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "model_not_ready", **snapshot})
            return

        content_type = self.headers.get("Content-Type", "").split(";", 1)[0]
        if content_type != "application/octet-stream":
            self._json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": "expected_float32_pcm"})
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length < SAMPLE_RATE * 4 or length > MAX_AUDIO_BYTES or length % 4:
            self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid_audio_length"})
            return

        try:
            start = max(0.0, float(self.headers.get("X-Audio-Start", "0")))
            end = max(start, float(self.headers.get("X-Audio-End", "0")))
            language = self.headers.get("X-Language", "Chinese")[:32]
            samples = np.frombuffer(self.rfile.read(length), dtype="<f4").copy()
            if not np.isfinite(samples).all():
                raise ValueError("audio contains non-finite samples")
            samples = np.clip(samples, -1.0, 1.0)
            began = time.monotonic()
            text, detected_language = self.state.backend.transcribe(samples, language)
            self._json(
                HTTPStatus.OK,
                {
                    "text": text,
                    "language": detected_language,
                    "start": round(start, 3),
                    "end": round(end, 3),
                    "audio_seconds": round(len(samples) / SAMPLE_RATE, 3),
                    "context_samples": len(samples),
                    "inference_seconds": round(time.monotonic() - began, 3),
                },
            )
        except Exception as exc:
            self._json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": "inference_failed", "detail": f"{type(exc).__name__}: {exc}"},
            )

    def _handle_summarize(self) -> None:
        snapshot = self.summary_state.snapshot()
        if snapshot["status"] != "ready" or self.summary_state.backend is None:
            self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "summary_model_not_ready", **snapshot})
            return
        if self.headers.get("Content-Type", "").split(";", 1)[0] != "application/json":
            self._json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": "expected_json"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_SUMMARY_BODY_BYTES:
            self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid_request_body"})
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            segments = payload["segments"]
            if not isinstance(segments, list) or not segments:
                raise ValueError("segments must be a non-empty list")
            lines = []
            for item in segments:
                if not isinstance(item, dict):
                    raise ValueError("invalid segment")
                text = str(item.get("text") or "").strip()
                if not text:
                    continue
                start = max(0.0, float(item.get("start", 0)))
                end = max(start, float(item.get("end", start)))
                lines.append(f"[{start:.1f}s–{end:.1f}s] {text}")
            transcript = "\n".join(lines)
            if not transcript:
                raise ValueError("transcript is empty")
            if len(transcript) > MAX_SUMMARY_TRANSCRIPT_CHARS:
                self._json(
                    HTTPStatus.BAD_REQUEST,
                    {"error": "transcript_too_long", "max_chars": MAX_SUMMARY_TRANSCRIPT_CHARS},
                )
                return
            raw_previous = payload.get("previous_summary")
            previous_summary = normalize_summary(raw_previous) if raw_previous is not None else None
        except (json.JSONDecodeError, UnicodeDecodeError, KeyError, TypeError, ValueError) as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid_transcript", "detail": str(exc)})
            return

        try:
            began = time.monotonic()
            result = self.summary_state.backend.summarize(transcript, previous_summary)
            result["inference_seconds"] = round(time.monotonic() - began, 3)
            result["mode"] = "incremental" if previous_summary else "full"
            result["input_chars"] = len(transcript) + (
                len(json.dumps(previous_summary, ensure_ascii=False)) if previous_summary else 0
            )
            result["input_segments"] = len(lines)
            self._json(HTTPStatus.OK, result)
        except Exception as exc:
            print(
                f"[{self.log_date_time_string()}] /api/summarize failed "
                f"(transcript_chars={len(transcript)}, has_previous_summary={previous_summary is not None}): "
                f"{type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            traceback.print_exc(file=sys.stderr)
            self._json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": "summary_failed", "detail": f"{type(exc).__name__}: {exc}"},
            )

    def _handle_fetch_media(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_FETCH_BODY_BYTES:
            self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid_request_body"})
            return

        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            url = str(payload["url"]).strip()
        except (json.JSONDecodeError, UnicodeDecodeError, KeyError, TypeError):
            self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid_json"})
            return

        if not url or not is_allowed_media_url(url):
            self._json(HTTPStatus.BAD_REQUEST, {"error": "unsupported_url", "detail": "只接受 YouTube 連結"})
            return

        try:
            result = self.media_fetcher.fetch(url)
        except MediaFetchError as exc:
            self._json(exc.status, {"error": "fetch_failed", "detail": str(exc)})
            return
        except Exception as exc:
            self._json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": "fetch_failed", "detail": f"{type(exc).__name__}: {exc}"},
            )
            return

        self._json(HTTPStatus.OK, result)


def create_server(
    host: str,
    port: int,
    state: BackendState,
    media_fetcher: MediaFetcher | None = None,
    summary_state: SummaryState | None = None,
) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), MurmurHandler)
    server.backend_state = state  # type: ignore[attr-defined]
    server.media_fetcher = media_fetcher or YtDlpFetcher(UPLOADS_DIR)  # type: ignore[attr-defined]
    if summary_state is None:
        summary_state = SummaryState()
        summary_state.load("fixture", "unused")
    server.summary_state = summary_state  # type: ignore[attr-defined]
    return server


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8787, type=int)
    parser.add_argument(
        "--backend",
        choices=("mlx", "fixture"),
        default=os.environ.get("MURMUR_ASR_BACKEND", "mlx"),
    )
    parser.add_argument(
        "--summary-backend",
        choices=("mlx", "fixture"),
        default=os.environ.get("MURMUR_SUMMARY_BACKEND", "mlx"),
    )
    parser.add_argument(
        "--summary-model",
        default=os.environ.get("MURMUR_SUMMARY_MODEL", "Qwen/Qwen3-8B-MLX-4bit"),
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("MURMUR_ASR_MODEL", "mlx-community/Qwen3-ASR-1.7B-8bit"),
    )
    args = parser.parse_args()

    state = BackendState()
    summary_state = SummaryState()

    def load_models() -> None:
        # Both MLX runtimes lazily import transformers. Initializing them in
        # parallel can expose a partially initialized lazy module, so keep
        # model startup off the HTTP thread but serialize the two loads.
        state.load(args.backend, args.model)
        summary_state.load(args.summary_backend, args.summary_model)

    threading.Thread(
        target=load_models,
        name="model-loader",
        daemon=True,
    ).start()
    server = create_server(args.host, args.port, state, summary_state=summary_state)
    print(f"Murmur is available at http://{args.host}:{server.server_port}")
    print("The ASR and summary models are loading in the background. Keep this window open.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Murmur.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
